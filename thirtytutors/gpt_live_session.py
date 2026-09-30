"""GPT-Live 1 relay for the original ThirtyTutors browser protocol.

This is the primary voice backend for Zeiad English Coach. It preserves the
ThirtyTutors UI, profiles, avatars, memory, quizzes, statistics, scenarios,
and hands-free speaker verification while replacing Gemini Live with
OpenAI GPT-Live 1 plus Responses delegation.

Browser protocol retained:
  init / start_turn / audio_chunk / turn_complete
  handsfree_start / handsfree_chunk / handsfree_stop
  quiz_answer / quiz_done / quiz_skip

OpenAI Live protocol:
  session.start
  session.input_audio.append
  session.input_audio.mute / unmute
  session.output_audio.delta
  session.input_transcript.delta / session.output_transcript.delta
  response.event for delegated Responses work
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from datetime import date

import numpy as np
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import memory, observability, quizzes, scenarios, speech_detection
from .constants import (
    DEFAULT_DIFFICULTY,
    DEFAULT_MIC_CALIBRATION_KEY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    GPT_LIVE_BACKEND_MODEL,
    GPT_LIVE_FAST_BACKEND_MODEL,
    HANDSFREE_SILENCE_RMS_THRESHOLD,
    HANDSFREE_WINDOW_BYTES,
    SPEAKER_VERIFICATION_THRESHOLD,
    VOICE_OPTIONS,
    get_api_voice_name,
)
from .openai_live_session import (
    _error_payload,
    _memory_context,
    _pcm16_16k_to_24k_b64,
    _quiz_results_summary,
    _record_active_day,
    _validate_quiz_items,
    get_active_conversation,
)
from .profiles_store import get_profile_by_id, patch_profile
from .summarization import summarize_conversation
from .tutor_instructions import build_system_instruction
from .tutor_tools import MOOD_TOOL, build_quiz_tool

router = APIRouter()

GPT_LIVE_MODEL = "gpt-live-1"
GPT_LIVE_URL = "wss://api.openai.com/v1/live/sessions"
OUTPUT_IDLE_FINALIZE_S = 0.9
INPUT_IDLE_FINALIZE_S = 0.7
END_OF_PTT_SILENCE_MS = 320

_VOICE_MESSAGE_TYPES = frozenset(
    {
        "start_turn",
        "audio_chunk",
        "turn_complete",
        "handsfree_start",
        "handsfree_chunk",
        "handsfree_stop",
    }
)


def _safety_id(profile: dict) -> str:
    stable = str(profile.get("id") or profile.get("name") or "local-user")
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def _backend_model(stored_model: str) -> str:
    if stored_model == "gpt-live-1-fast":
        return GPT_LIVE_FAST_BACKEND_MODEL
    return GPT_LIVE_BACKEND_MODEL


def _tutor_prompt(profile: dict, conv_config: dict, durable_context: str | None) -> str:
    name = profile.get("name") or "the student"
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    voice_name = conv_config.get("voice_name") or DEFAULT_VOICE
    tutor_name = next(
        (voice.get("alias") or voice["name"] for voice in VOICE_OPTIONS if voice["name"] == voice_name),
        voice_name,
    )
    scenario_id = conv_config.get("scenario") or scenarios.DEFAULT_SCENARIO
    scenario_template = scenarios.SCENARIO_TEMPLATES.get(
        scenario_id, scenarios.SCENARIO_TEMPLATES[scenarios.DEFAULT_SCENARIO]
    )
    difficulty = conv_config.get("difficulty") or DEFAULT_DIFFICULTY

    return build_system_instruction(
        scenario_template,
        name=name,
        native_language=native_language,
        target_language=target_language,
        tutor_name=tutor_name,
        difficulty=difficulty,
        summary_text=durable_context,
        review_terms=[],
        taught_vocab=[],
    )


def _live_prompt(profile: dict, conv_config: dict) -> str:
    name = profile.get("name") or "the student"
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    return (
        f"You are Zeiad English Coach, a warm real-time speaking tutor for {name}. "
        f"Practice {target_language} naturally in an American conversational style. "
        f"The learner's native language is {native_language}. Keep spoken turns concise "
        "and conversational, listen while speaking, and yield naturally when the learner "
        "interrupts. For grammar correction, natural American rephrasing, explanations, "
        "lesson decisions, quizzes, or any substantive tutoring judgment, delegate to the "
        "Responses backend. Never claim pronunciation is correct merely because speech "
        "was transcribed. If Arabic explanation is appropriate, let the delegated tutor "
        "supply it and speak only as much of it as the learner needs."
    )


def _startup_input(durable_context: str | None) -> list[dict]:
    if not durable_context:
        return []
    return [
        {
            "type": "message",
            "role": "developer",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Durable learning memory from earlier sessions. Use it as context "
                        "without reciting it verbatim unless relevant:\n" + durable_context
                    ),
                }
            ],
        }
    ]


async def _send(ws, event: dict) -> None:
    await ws.send(json.dumps(event, ensure_ascii=False))


async def _connect(api_key: str, profile: dict):
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("No OpenAI API key is set for this profile.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": _safety_id(profile),
    }
    kwargs = {"max_size": None, "ping_interval": 20, "ping_timeout": 20}
    try:
        return await websockets.connect(GPT_LIVE_URL, additional_headers=headers, **kwargs)
    except TypeError:
        return await websockets.connect(GPT_LIVE_URL, extra_headers=headers, **kwargs)


def _session_config(
    profile: dict,
    conv_config: dict,
    *,
    durable_context: str | None,
) -> dict:
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    stored_model = conv_config.get("model_name") or DEFAULT_MODEL
    backend_model = _backend_model(stored_model)
    voice = get_api_voice_name(conv_config.get("voice_name") or DEFAULT_VOICE)

    backend_instructions = _tutor_prompt(profile, conv_config, durable_context)
    backend_instructions += (
        "\n\nQUALITY POLICY:\n"
        "- Prefer natural contemporary American phrasing, not literal translation.\n"
        "- Correct meaningful grammar, vocabulary, and expression mistakes.\n"
        "- Explain in the learner's native language when helpful or requested.\n"
        "- Keep the learner speaking: after correction, give a short retry opportunity.\n"
        "- Do not equate transcription accuracy with pronunciation quality.\n"
        f"- Target language: {target_language}. Native language: {native_language}.\n"
    )

    return {
        "model": GPT_LIVE_MODEL,
        "instructions": _live_prompt(profile, conv_config),
        "input": _startup_input(durable_context),
        "audio": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "output": {"voice": voice},
        },
        "delegation": {
            "type": "responses",
            "responses": {
                "model": backend_model,
                "instructions": backend_instructions,
                "tools": [
                    MOOD_TOOL,
                    build_quiz_tool(
                        native_language=native_language,
                        target_language=target_language,
                    ),
                ],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            },
        },
        "store": False,
    }


async def _wait_for_started(openai_ws, timeout: float = 15.0) -> dict:
    async def _wait():
        async for raw in openai_ws:
            event = json.loads(raw)
            if event.get("type") == "session.started":
                return event
            if event.get("type") == "error":
                error = event.get("error") or {}
                raise RuntimeError(error.get("message") or str(error))
        raise RuntimeError("GPT-Live connection closed before session.started")

    return await asyncio.wait_for(_wait(), timeout=timeout)


async def _inject_backend_text(openai_ws, text: str) -> None:
    await _send(
        openai_ws,
        {
            "type": "response.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        },
    )
    await _send(openai_ws, {"type": "response.create"})


def _silence_b64(milliseconds: int = END_OF_PTT_SILENCE_MS) -> str:
    samples = int(24000 * (milliseconds / 1000.0))
    return base64.b64encode(b"\x00\x00" * samples).decode("ascii")


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()

    try:
        init_msg = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    if init_msg.get("type") != "init":
        await websocket.send_json({"type": "error", "message": "First message must be type 'init'."})
        await websocket.close(code=1002)
        return

    profile_id = init_msg.get("profile_id")
    profile = get_profile_by_id(profile_id) if profile_id else None
    conv = None

    if profile is not None:
        requested_cid = init_msg.get("conversation_id")
        if requested_cid:
            conv = memory.get_conversation(requested_cid)
            if conv is not None and conv["profile_id"] != profile_id:
                conv = None
        if conv is None:
            conv = get_active_conversation(profile)
        if conv is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "This profile doesn't have a language set up yet - add one first.",
                }
            )
            await websocket.close()
            return
        conv_config = dict(conv["config"])
        patch_profile(profile_id, {"active_conversation_id": conv["id"]})
        _record_active_day(profile)
    else:
        profile = {
            "id": None,
            "name": init_msg.get("profile_name") or "the student",
            "api_key": init_msg.get("api_key"),
        }
        conv_config = {
            "voice_name": init_msg.get("voice_name") or DEFAULT_VOICE,
            "native_language": init_msg.get("native_language") or DEFAULT_NATIVE_LANGUAGE,
            "target_language": init_msg.get("target_language") or DEFAULT_TARGET_LANGUAGE,
            "model_name": init_msg.get("model_name") or DEFAULT_MODEL,
            "scenario": init_msg.get("scenario") or scenarios.DEFAULT_SCENARIO,
            "difficulty": init_msg.get("difficulty") or DEFAULT_DIFFICULTY,
        }

    stored_model = conv_config.get("model_name") or DEFAULT_MODEL
    durable_context, _, _ = _memory_context(conv)

    observability.set_profile_keys(
        profile.get("langfuse_public_key"),
        profile.get("langfuse_secret_key"),
        profile.get("langfuse_base_url"),
    )

    try:
        openai_ws = await _connect(profile.get("api_key"), profile)
        await _send(
            openai_ws,
            {
                "type": "session.start",
                "event_id": "zeiad_session_start",
                "session": _session_config(
                    profile,
                    conv_config,
                    durable_context=durable_context,
                ),
            },
        )
        started_event = await _wait_for_started(openai_ws)
        await _send(openai_ws, {"type": "session.input_audio.mute"})
    except Exception as exc:
        print(f"[gpt_live] connect/start failed: {type(exc).__name__}: {exc}")
        await websocket.send_json(_error_payload(exc))
        await websocket.send_json(
            {"type": "session_status", "model_name": None, "unavailable": True}
        )
        await websocket.close()
        return

    session_id = ((started_event.get("session") or {}).get("id"))
    await websocket.send_json(
        {
            "type": "session_status",
            "resumed": False,
            "conversation_name": (conv or {}).get("name"),
            "model_name": stored_model,
            "live_session_id": session_id,
        }
    )

    session_started = time.monotonic()
    quiz_state = {"active": False, "quiz_id": None}
    if conv is not None:
        in_progress = quizzes.get_in_progress_quiz(conv["id"])
        if in_progress is not None:
            quiz_state["active"] = True
            quiz_state["quiz_id"] = in_progress["quiz_id"]
            await websocket.send_json(
                {
                    "type": "quiz_resume",
                    "quiz_id": in_progress["quiz_id"],
                    "quiz_type": in_progress["quiz_type"],
                    "items": in_progress["payload"].get("items"),
                    "current_index": in_progress["current_index"],
                    "answered_items": in_progress["answered_items"],
                }
            )

    mic_key = profile.get("mic_label") or DEFAULT_MIC_CALIBRATION_KEY
    mic_calibration = (profile.get("mic_calibrations") or {}).get(mic_key) or {}
    hf_silence_threshold = (
        mic_calibration.get("silence_rms_threshold")
        or HANDSFREE_SILENCE_RMS_THRESHOLD
    )
    hf_similarity_threshold = (
        mic_calibration.get("speaker_threshold")
        or SPEAKER_VERIFICATION_THRESHOLD
    )
    hf_state = {"active": False, "buffer": bytearray()}

    user_chunks: list[str] = []
    tutor_chunks: list[str] = []
    input_finalize_task: asyncio.Task | None = None
    output_finalize_task: asyncio.Task | None = None
    output_active = False

    async def maybe_summarize() -> None:
        if conv is None:
            return
        count = memory.get_turn_count(conv["id"])
        if count and count % memory.SUMMARY_FOLD_EVERY_N_TURNS == 0:
            await asyncio.to_thread(
                summarize_conversation,
                conv["id"],
                profile.get("name") or "the student",
                profile.get("api_key"),
            )

    async def finalize_user_turn() -> None:
        text = "".join(user_chunks).strip()
        user_chunks.clear()
        if conv is not None and text:
            memory.insert_turn(conv["id"], "user", text)
        await maybe_summarize()

    async def finalize_tutor_turn(*, notify_browser: bool = True) -> None:
        nonlocal output_active
        text = "".join(tutor_chunks).strip()
        tutor_chunks.clear()
        output_active = False
        if conv is not None and text:
            memory.insert_turn(conv["id"], "tutor", text)
        await maybe_summarize()
        if notify_browser:
            try:
                await websocket.send_json({"type": "turn_complete"})
            except Exception:
                pass

    def schedule_input_finalize() -> None:
        nonlocal input_finalize_task
        if input_finalize_task and not input_finalize_task.done():
            input_finalize_task.cancel()

        async def _later():
            try:
                await asyncio.sleep(INPUT_IDLE_FINALIZE_S)
                await finalize_user_turn()
            except asyncio.CancelledError:
                pass

        input_finalize_task = asyncio.create_task(_later())

    def schedule_output_finalize() -> None:
        nonlocal output_finalize_task
        if output_finalize_task and not output_finalize_task.done():
            output_finalize_task.cancel()

        async def _later():
            try:
                await asyncio.sleep(OUTPUT_IDLE_FINALIZE_S)
                await finalize_tutor_turn()
            except asyncio.CancelledError:
                pass

        output_finalize_task = asyncio.create_task(_later())

    async def process_tool_call(item: dict) -> None:
        name = item.get("name")
        call_id = item.get("call_id")
        try:
            args = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}

        result: dict = {"result": "ok"}

        if name == "set_mood":
            mood = args.get("mood", "neutral")
            await websocket.send_json({"type": "mood_change", "mood": mood})
            result = {"mood": mood}

        elif name == "start_quiz" and conv is not None:
            existing = quizzes.get_in_progress_quiz(conv["id"])
            if existing is not None:
                quiz_id = existing["quiz_id"]
                payload = existing["payload"]
            else:
                payload = dict(args)
                items = payload.get("items") or []
                _validate_quiz_items(items)
                quiz_id = quizzes.start_quiz_session(
                    conv["id"], quizzes.compute_quiz_type(items), payload
                )
            quiz_state["active"] = True
            quiz_state["quiz_id"] = quiz_id
            await websocket.send_json(
                {
                    "type": "quiz_start",
                    "quiz_id": quiz_id,
                    "items": payload.get("items"),
                }
            )
            result = {"quiz_id": quiz_id, "opened": True}

        if call_id:
            await _send(
                openai_ws,
                {
                    "type": "response.item.create",
                    "event_id": f"tool_result_{call_id}",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                },
            )
            await _send(
                openai_ws,
                {
                    "type": "response.create",
                    "event_id": f"continue_{call_id}",
                },
            )

    async def append_audio_16k(b64_audio: str) -> None:
        converted = _pcm16_16k_to_24k_b64(b64_audio)
        if converted:
            await _send(
                openai_ws,
                {"type": "session.input_audio.append", "audio": converted},
            )

    async def append_turn_end_silence() -> None:
        await _send(
            openai_ws,
            {"type": "session.input_audio.append", "audio": _silence_b64()},
        )

    async def handle_handsfree_window(window_bytes: bytes) -> None:
        audio = speech_detection.pcm16_bytes_to_float32(window_bytes)
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0

        if rms <= hf_silence_threshold:
            await append_turn_end_silence()
            schedule_input_finalize()
            return

        verified = await asyncio.to_thread(
            speech_detection.verify,
            profile.get("id"),
            mic_key,
            audio,
            hf_silence_threshold,
            hf_similarity_threshold,
        )
        if verified is not None and not verified[0]:
            return

        encoded = base64.b64encode(window_bytes).decode("ascii")
        await append_audio_16k(encoded)

    async def browser_to_live() -> None:
        nonlocal output_active

        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if quiz_state["active"] and msg_type in _VOICE_MESSAGE_TYPES:
                continue

            if msg_type == "start_turn":
                if output_active or tutor_chunks:
                    if output_finalize_task and not output_finalize_task.done():
                        output_finalize_task.cancel()
                    await finalize_tutor_turn()
                    await websocket.send_json({"type": "interrupted"})
                await _send(openai_ws, {"type": "session.input_audio.unmute"})

            elif msg_type == "audio_chunk":
                await append_audio_16k(msg.get("data") or "")

            elif msg_type == "turn_complete":
                await append_turn_end_silence()
                await _send(openai_ws, {"type": "session.input_audio.mute"})
                schedule_input_finalize()

            elif msg_type == "handsfree_start":
                hf_state["active"] = True
                hf_state["buffer"] = bytearray()
                await _send(openai_ws, {"type": "session.input_audio.unmute"})

            elif msg_type == "handsfree_chunk":
                if not hf_state["active"]:
                    continue
                hf_state["buffer"].extend(base64.b64decode(msg.get("data") or ""))
                while len(hf_state["buffer"]) >= HANDSFREE_WINDOW_BYTES:
                    window = bytes(hf_state["buffer"][:HANDSFREE_WINDOW_BYTES])
                    del hf_state["buffer"][:HANDSFREE_WINDOW_BYTES]
                    await handle_handsfree_window(window)

            elif msg_type == "handsfree_stop":
                hf_state["active"] = False
                hf_state["buffer"] = bytearray()
                await append_turn_end_silence()
                await _send(openai_ws, {"type": "session.input_audio.mute"})
                schedule_input_finalize()

            elif msg_type == "quiz_answer":
                quizzes.record_item_answer(
                    msg["quiz_id"],
                    item_index=msg["item_index"],
                    target_term=msg["target_term"],
                    prompt_or_text=msg["prompt_or_text"],
                    correct_answer=msg["correct_answer"],
                    student_answer=msg.get("student_answer"),
                    is_correct=bool(msg["is_correct"]),
                )

            elif msg_type == "quiz_done":
                quiz_id = msg["quiz_id"]
                quizzes.finalize_quiz_session(quiz_id, status="completed")
                quiz_state["active"] = False
                quiz_state["quiz_id"] = None
                await _inject_backend_text(
                    openai_ws,
                    _quiz_results_summary(quizzes.get_quiz_items(quiz_id)),
                )

            elif msg_type == "quiz_skip":
                quiz_id = msg["quiz_id"]
                quizzes.finalize_quiz_session(quiz_id, status="skipped")
                quiz_state["active"] = False
                quiz_state["quiz_id"] = None
                answered = [
                    item
                    for item in quizzes.get_quiz_items(quiz_id)
                    if item["student_answer"] is not None
                ]
                if answered:
                    correct = sum(1 for item in answered if item["is_correct"])
                    summary = (
                        f"[Quiz skipped partway through: {correct}/{len(answered)} "
                        "answered correctly before stopping.]"
                    )
                else:
                    summary = "[Quiz skipped before answering anything.]"
                await _inject_backend_text(openai_ws, summary)

            elif msg_type == "close":
                try:
                    await _send(openai_ws, {"type": "session.close"})
                finally:
                    return

    async def live_to_browser() -> None:
        nonlocal output_active

        async for raw in openai_ws:
            event = json.loads(raw)
            event_type = event.get("type")

            if event_type == "session.output_audio.delta":
                output_active = True
                delta = event.get("delta")
                if delta:
                    await websocket.send_json({"type": "audio", "data": delta})
                    schedule_output_finalize()

            elif event_type == "session.input_transcript.delta":
                delta = event.get("delta") or ""
                if delta:
                    user_chunks.append(delta)
                    await websocket.send_json({"type": "transcript_in", "text": delta})
                    schedule_input_finalize()

            elif event_type == "session.output_transcript.delta":
                output_active = True
                delta = event.get("delta") or ""
                if delta:
                    tutor_chunks.append(delta)
                    await websocket.send_json({"type": "transcript_out", "text": delta})
                    schedule_output_finalize()

            elif event_type == "response.event":
                nested = event.get("event") or {}
                if nested.get("type") == "response.output_item.done":
                    item = nested.get("item") or {}
                    if item.get("type") == "function_call":
                        await process_tool_call(item)

            elif event_type == "error":
                error = event.get("error") or {}
                message = error.get("message") or str(error)
                code = str(error.get("code") or "")
                payload = {"type": "error", "message": message}
                if "429" in message or "rate" in code.lower():
                    payload["kind"] = "rate_limit"
                await websocket.send_json(payload)

            elif event_type == "session.closed":
                return

    try:
        browser_task = asyncio.create_task(browser_to_live())
        model_task = asyncio.create_task(live_to_browser())
        done, pending = await asyncio.wait(
            {browser_task, model_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        print(f"[gpt_live] relay failed: {type(exc).__name__}: {exc}")
        try:
            await websocket.send_json(_error_payload(exc))
        except Exception:
            pass
    finally:
        for task in (input_finalize_task, output_finalize_task):
            if task and not task.done():
                task.cancel()

        try:
            await finalize_user_turn()
            await finalize_tutor_turn(notify_browser=False)
        except Exception:
            pass

        try:
            await _send(openai_ws, {"type": "session.close"})
            await asyncio.sleep(0.05)
        except Exception:
            pass
        try:
            await openai_ws.close()
        except Exception:
            pass

        if profile.get("id"):
            elapsed = max(0, int(time.monotonic() - session_started))
            memory.add_seconds_studied(profile["id"], elapsed)

        if conv is not None:
            try:
                await asyncio.to_thread(
                    summarize_conversation,
                    conv["id"],
                    profile.get("name") or "the student",
                    profile.get("api_key"),
                )
            except Exception as exc:
                print(f"[gpt_live] final summarization failed: {type(exc).__name__}: {exc}")
