"""OpenAI Realtime relay for the ThirtyTutors-derived browser protocol.

The browser still speaks the exact ThirtyTutors websocket protocol:
init/start_turn/audio_chunk/turn_complete and hands-free/quiz messages.
This module translates that protocol to OpenAI Realtime events, so the
frontend, avatar, profiles, quizzes, memory, statistics, and speaker
verification can remain intact while the original Gemini Live backend is replaced by OpenAI Realtime.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import date

import numpy as np
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from scipy.signal import resample_poly

from . import memory, observability, quizzes, scenarios, speech_detection
from .constants import (
    DEFAULT_DIFFICULTY,
    DEFAULT_MIC_CALIBRATION_KEY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    MODEL_OPTIONS,
    HANDSFREE_SILENCE_RMS_THRESHOLD,
    HANDSFREE_WINDOW_BYTES,
    SPEAKER_VERIFICATION_THRESHOLD,
    VOICE_OPTIONS,
    get_api_voice_name,
)
from .profiles_store import get_profile_by_id, patch_profile
from .summarization import summarize_conversation
from .tutor_instructions import build_system_instruction
from .tutor_tools import MOOD_TOOL, build_quiz_tool

router = APIRouter()

_OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"
_VOICE_MESSAGE_TYPES = frozenset(
    {"start_turn", "audio_chunk", "turn_complete", "handsfree_start", "handsfree_chunk", "handsfree_stop"}
)
_BLANK_RE = re.compile(r"\{\d+\}")


def _quiz_results_summary(items: list[dict]) -> str:
    total = len(items)
    correct = sum(1 for item in items if item["is_correct"])
    summary = f"[Quiz results: {correct}/{total} correct."
    missed = [item for item in items if not item["is_correct"]]
    if missed:
        missed_desc = ", ".join(
            f"'{item['target_term']}' (wrote '{item['student_answer'] or ''}')"
            for item in missed
        )
        summary += f" Missed: {missed_desc}."
    return summary + "]"


def _validate_quiz_items(items: list[dict]) -> None:
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or item.get("item_type") != "fill_blank_dragdrop":
            continue
        blank_count = len(_BLANK_RE.findall(item.get("text_with_blanks") or ""))
        answer_count = len(item.get("correct_answers") or [])
        if blank_count != answer_count:
            print(
                f"[start_quiz] item {idx}: {blank_count} blanks vs "
                f"{answer_count} correct answers"
            )


def get_active_conversation(profile: dict) -> dict | None:
    convs = memory.list_conversations(profile["id"])
    if not convs:
        return None
    active_id = profile.get("active_conversation_id")
    return next((conv for conv in convs if conv["id"] == active_id), None) or convs[0]


def _record_active_day(profile: dict) -> None:
    today = date.today().isoformat()
    state = memory.record_active_day(profile["id"], today)
    profile["last_active_date"] = state["last_active_date"]
    profile["current_streak"] = state["current_streak"]


def _memory_context(conv: dict | None) -> tuple[str | None, list[str], list[str]]:
    if conv is None:
        return None, [], []

    summary_row = memory.get_summary(conv["id"])
    based_on_turn = int(summary_row["based_on_turn"]) if summary_row else 0
    recent_turns = memory.get_turns(conv["id"], since_seq=based_on_turn)

    # Keep reconnects faithful even between summary folds. If there is no
    # rolling summary yet, cap the raw replay context so an old conversation
    # cannot make the system prompt grow without bound.
    if not summary_row:
        recent_turns = recent_turns[-16:]

    pieces: list[str] = []
    if summary_row and summary_row.get("summary"):
        pieces.append(summary_row["summary"].strip())

    if recent_turns:
        rendered = []
        for turn in recent_turns:
            speaker = "Student" if turn.get("role") == "user" else "Tutor"
            rendered.append(f"{speaker}: {turn.get('text', '').strip()}")
        pieces.append("Recent conversation after the rolling summary:\n" + "\n".join(rendered))

    durable_context = "\n\n".join(piece for piece in pieces if piece) or None
    return (
        durable_context,
        memory.get_review_candidates(conv["id"]),
        memory.get_taught_vocab(conv["id"]),
    )


def _build_instructions(
    profile: dict,
    conv_config: dict,
    summary_text: str | None,
    review_terms: list[str],
    taught_vocab: list[str],
) -> str:
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
        summary_text=summary_text,
        review_terms=review_terms,
        taught_vocab=taught_vocab,
    )


def _safety_id(profile: dict) -> str:
    stable = str(profile.get("id") or profile.get("name") or "local-user")
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def _pcm16_16k_to_24k_b64(b64_audio: str) -> str:
    """ThirtyTutors captures mono PCM16 at 16 kHz; OpenAI Realtime uses 24 kHz."""
    raw = base64.b64decode(b64_audio)
    samples = np.frombuffer(raw, dtype="<i2")
    if samples.size == 0:
        return ""
    floating = samples.astype(np.float32) / 32768.0
    resampled = resample_poly(floating, 3, 2)
    pcm24 = np.clip(resampled, -1.0, 1.0)
    pcm24 = (pcm24 * 32767.0).astype("<i2")
    return base64.b64encode(pcm24.tobytes()).decode("ascii")


async def _send_openai(ws, event: dict) -> None:
    await ws.send(json.dumps(event, ensure_ascii=False))


async def _connect_openai(api_key: str, model_name: str, profile: dict):
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("No OpenAI API key is set for this profile.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": _safety_id(profile),
    }
    url = _OPENAI_REALTIME_URL.format(model=model_name)

    kwargs = {
        "max_size": None,
        "ping_interval": 20,
        "ping_timeout": 20,
    }
    try:
        return await websockets.connect(url, additional_headers=headers, **kwargs)
    except TypeError:
        # Compatibility with older websockets releases.
        return await websockets.connect(url, extra_headers=headers, **kwargs)


async def _configure_session(
    openai_ws,
    *,
    profile: dict,
    conv_config: dict,
    model_name: str,
    summary_text: str | None,
    review_terms: list[str],
    taught_vocab: list[str],
) -> None:
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    voice = get_api_voice_name(conv_config.get("voice_name") or DEFAULT_VOICE)
    instructions = _build_instructions(
        profile, conv_config, summary_text, review_terms, taught_vocab
    )

    await _send_openai(
        openai_ws,
        {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {
                            "model": "gpt-live-transcribe",
                            "prompt": (
                                f"Language tutoring. The learner is practicing {target_language}; "
                                f"their native language is {native_language}. Preserve names, numbers, "
                                "and natural learner mistakes faithfully."
                            ),
                            "delay": "low",
                        },
                        "turn_detection": None,
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": voice,
                    },
                },
                "tools": [
                    MOOD_TOOL,
                    build_quiz_tool(
                        native_language=native_language,
                        target_language=target_language,
                    ),
                ],
                "tool_choice": "auto",
            },
        },
    )


async def _inject_text(openai_ws, text: str) -> None:
    await _send_openai(
        openai_ws,
        {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        },
    )
    await _send_openai(openai_ws, {"type": "response.create"})


def _error_payload(exc: Exception) -> dict:
    raw = str(exc)
    upper = raw.upper()
    if "429" in upper or "RATE_LIMIT" in upper or "QUOTA" in upper:
        return {
            "type": "error",
            "kind": "rate_limit",
            "message": "OpenAI rate limit or API quota was reached. Please try again shortly.",
        }
    if isinstance(exc, OSError) or "DNS" in upper or "CONNECT" in upper:
        return {
            "type": "error",
            "kind": "network",
            "message": "Couldn't reach OpenAI. Check your internet connection and try again.",
        }
    return {"type": "error", "message": f"OpenAI Realtime error: {raw}"}


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
            await websocket.send_json({
                "type": "error",
                "message": "This profile doesn't have a language set up yet - add one first.",
            })
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

    model_name = conv_config.get("model_name") or DEFAULT_MODEL
    summary_text, review_terms, taught_vocab = _memory_context(conv)

    observability.set_profile_keys(
        profile.get("langfuse_public_key"),
        profile.get("langfuse_secret_key"),
        profile.get("langfuse_base_url"),
    )

    openai_ws = None
    last_exc = None
    model_candidates = [model_name] + [
        option["id"] for option in MODEL_OPTIONS
        if option["id"] != model_name
    ]
    for candidate_model in model_candidates:
        try:
            openai_ws = await _connect_openai(profile.get("api_key"), candidate_model, profile)
            await _configure_session(
                openai_ws,
                profile=profile,
                conv_config=conv_config,
                model_name=candidate_model,
                summary_text=summary_text,
                review_terms=review_terms,
                taught_vocab=taught_vocab,
            )
            model_name = candidate_model
            break
        except Exception as exc:
            last_exc = exc
            print(
                f"[openai_ws_session] model {candidate_model} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if openai_ws is not None:
                try:
                    await openai_ws.close()
                except Exception:
                    pass
            openai_ws = None

    if openai_ws is None:
        exc = last_exc or RuntimeError("No OpenAI Realtime model could be started.")
        await websocket.send_json(_error_payload(exc))
        await websocket.send_json({"type": "session_status", "model_name": None, "unavailable": True})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "session_status",
        "resumed": False,
        "conversation_name": (conv or {}).get("name"),
        "model_name": model_name,
    })

    session_started = time.monotonic()
    quiz_state = {"active": False, "quiz_id": None}
    if conv is not None:
        in_progress = quizzes.get_in_progress_quiz(conv["id"])
        if in_progress is not None:
            quiz_state["active"] = True
            quiz_state["quiz_id"] = in_progress["quiz_id"]
            await websocket.send_json({
                "type": "quiz_resume",
                "quiz_id": in_progress["quiz_id"],
                "quiz_type": in_progress["quiz_type"],
                "items": in_progress["payload"].get("items"),
                "current_index": in_progress["current_index"],
                "answered_items": in_progress["answered_items"],
            })

    tutor_chunks: list[str] = []
    seen_input_items: set[str] = set()
    tool_followup_pending = False

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
    hf_state = {"active": False, "buffer": bytearray(), "turn_active": False}

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

    async def finalize_tutor_turn() -> None:
        text = "".join(tutor_chunks).strip()
        tutor_chunks.clear()
        if conv is not None and text:
            memory.insert_turn(conv["id"], "tutor", text)
        await maybe_summarize()
        await websocket.send_json({"type": "turn_complete"})

    async def process_tool_call(item: dict) -> None:
        nonlocal tool_followup_pending
        name = item.get("name")
        call_id = item.get("call_id")
        try:
            args = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}

        if name == "set_mood":
            mood = args.get("mood", "neutral")
            await websocket.send_json({"type": "mood_change", "mood": mood})
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
            await websocket.send_json({
                "type": "quiz_start",
                "quiz_id": quiz_id,
                "items": payload.get("items"),
            })

        if call_id:
            await _send_openai(
                openai_ws,
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"result": "ok"}),
                    },
                },
            )
            tool_followup_pending = True

    async def close_handsfree_turn() -> None:
        if not hf_state["turn_active"]:
            return
        await _send_openai(openai_ws, {"type": "input_audio_buffer.commit"})
        await _send_openai(openai_ws, {"type": "response.create"})
        hf_state["turn_active"] = False

    async def handle_handsfree_window(window_bytes: bytes) -> None:
        audio = speech_detection.pcm16_bytes_to_float32(window_bytes)
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0

        if rms <= hf_silence_threshold:
            await close_handsfree_turn()
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

        if not hf_state["turn_active"]:
            await _send_openai(openai_ws, {"type": "input_audio_buffer.clear"})
            hf_state["turn_active"] = True

        encoded = base64.b64encode(window_bytes).decode("ascii")
        converted = _pcm16_16k_to_24k_b64(encoded)
        if converted:
            await _send_openai(
                openai_ws,
                {"type": "input_audio_buffer.append", "audio": converted},
            )

    async def browser_to_openai() -> None:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if quiz_state["active"] and msg_type in _VOICE_MESSAGE_TYPES:
                continue

            if msg_type == "start_turn":
                # Barge-in: cancel any active Realtime response before accepting the new turn.
                await _send_openai(openai_ws, {"type": "response.cancel"})
                await _send_openai(openai_ws, {"type": "input_audio_buffer.clear"})
            elif msg_type == "audio_chunk":
                converted = _pcm16_16k_to_24k_b64(msg.get("data") or "")
                if converted:
                    await _send_openai(
                        openai_ws,
                        {"type": "input_audio_buffer.append", "audio": converted},
                    )
            elif msg_type == "turn_complete":
                await _send_openai(openai_ws, {"type": "input_audio_buffer.commit"})
                await _send_openai(openai_ws, {"type": "response.create"})
            elif msg_type == "handsfree_start":
                hf_state["active"] = True
                hf_state["buffer"] = bytearray()
                hf_state["turn_active"] = False
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
                await close_handsfree_turn()
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
                await _inject_text(
                    openai_ws,
                    _quiz_results_summary(quizzes.get_quiz_items(quiz_id)),
                )
            elif msg_type == "quiz_skip":
                quiz_id = msg["quiz_id"]
                quizzes.finalize_quiz_session(quiz_id, status="skipped")
                quiz_state["active"] = False
                quiz_state["quiz_id"] = None
                answered = [
                    item for item in quizzes.get_quiz_items(quiz_id)
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
                await _inject_text(openai_ws, summary)
            elif msg_type == "close":
                return

    async def openai_to_browser() -> None:
        nonlocal tool_followup_pending

        async for raw in openai_ws:
            event = json.loads(raw)
            event_type = event.get("type")

            if event_type == "response.output_audio.delta":
                delta = event.get("delta")
                if delta:
                    await websocket.send_json({"type": "audio", "data": delta})

            elif event_type == "response.output_audio_transcript.delta":
                delta = event.get("delta") or ""
                if delta:
                    tutor_chunks.append(delta)
                    await websocket.send_json({"type": "transcript_out", "text": delta})

            elif event_type == "conversation.item.input_audio_transcription.completed":
                item_id = event.get("item_id") or ""
                transcript = (event.get("transcript") or "").strip()
                if transcript and item_id not in seen_input_items:
                    seen_input_items.add(item_id)
                    if conv is not None:
                        memory.insert_turn(conv["id"], "user", transcript)
                    await websocket.send_json({"type": "transcript_in", "text": transcript})

            elif event_type == "response.done":
                response = event.get("response") or {}
                output = response.get("output") or []
                calls = [
                    item for item in output
                    if isinstance(item, dict) and item.get("type") == "function_call"
                ]
                if calls:
                    for call in calls:
                        await process_tool_call(call)
                    await _send_openai(openai_ws, {"type": "response.create"})
                    continue

                if tool_followup_pending:
                    tool_followup_pending = False

                status = response.get("status")
                if status == "failed":
                    details = response.get("status_details") or {}
                    await websocket.send_json({
                        "type": "error",
                        "message": f"OpenAI response failed: {details}",
                    })
                    continue

                await finalize_tutor_turn()

            elif event_type == "error":
                error = event.get("error") or {}
                code = error.get("code") or ""
                message = error.get("message") or str(error)
                kind = "rate_limit" if "rate" in code.lower() or "429" in message else None
                payload = {"type": "error", "message": message}
                if kind:
                    payload["kind"] = kind
                await websocket.send_json(payload)

    try:
        browser_task = asyncio.create_task(browser_to_openai())
        model_task = asyncio.create_task(openai_to_browser())
        done, pending = await asyncio.wait(
            {browser_task, model_task},
            return_when=asyncio.FIRST_COMPLETED,
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
        print(f"[openai_ws_session] relay failed: {type(exc).__name__}: {exc}")
        try:
            await websocket.send_json(_error_payload(exc))
        except Exception:
            pass
    finally:
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
                print(
                    f"[openai_ws_session] final summarization failed: "
                    f"{type(exc).__name__}: {exc}"
                )
