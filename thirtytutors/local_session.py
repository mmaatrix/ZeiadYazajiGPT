"""Local Free voice backend: faster-whisper + Ollama + Windows speech synthesis."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from scipy.signal import resample_poly

from . import memory, quizzes, speech_detection
from .constants import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_MIC_CALIBRATION_KEY,
    HANDSFREE_SILENCE_RMS_THRESHOLD,
    HANDSFREE_WINDOW_BYTES,
    SPEAKER_VERIFICATION_THRESHOLD,
    VOICE_OPTIONS,
)

_OLLAMA_BASE_URL = os.environ.get("ZEIAD_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
_WHISPER_MODEL_NAME = os.environ.get("ZEIAD_WHISPER_MODEL", "small")
_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()
_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


class LocalSetupError(RuntimeError):
    """Raised when the one-time Local Free setup is incomplete."""


def _require_faster_whisper() -> None:
    if importlib.util.find_spec("faster_whisper") is None:
        raise LocalSetupError(
            "Local Free mode needs one-time speech setup. Close the app and run "
            "Setup_Local_Free_Mode.bat from the ZeiadYazajiGPT folder."
        )


def _ollama_request(path: str, payload: dict | None = None, timeout: int = 20) -> dict:
    url = f"{_OLLAMA_BASE_URL}{path}"
    data = None
    method = "GET"
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LocalSetupError(f"Ollama returned HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LocalSetupError(
            "Ollama is not reachable. Open Ollama (or run 'ollama serve') and try again."
        ) from exc


def _check_ollama_model(model_name: str) -> None:
    models = _ollama_request("/api/tags").get("models") or []
    names = {
        str(item.get("name") or item.get("model") or "")
        for item in models
        if isinstance(item, dict)
    }
    if model_name not in names:
        raise LocalSetupError(
            f"Local model {model_name!r} is not installed. Run: ollama pull {model_name}"
        )


def _get_whisper_model():
    global _WHISPER_MODEL
    _require_faster_whisper()
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel

        _WHISPER_MODEL = WhisperModel(
            _WHISPER_MODEL_NAME,
            device="cpu",
            compute_type="int8",
        )
    return _WHISPER_MODEL


def _transcribe_pcm16(pcm_bytes: bytes) -> str:
    if not pcm_bytes:
        return ""
    audio = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
    if audio.size < 800:
        return ""
    with _WHISPER_LOCK:
        model = _get_whisper_model()
        segments, _ = model.transcribe(
            audio,
            beam_size=3,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(
            segment.text.strip() for segment in segments if segment.text.strip()
        ).strip()


def _recent_messages(conversation_id: str | None, system_instruction: str) -> list[dict]:
    local_instruction = (
        system_instruction
        + "\n\n# LOCAL FREE MODE\n"
        "Reply with plain natural tutoring text only. Do not output internal reasoning, "
        "<think> blocks, JSON, function calls, or tool-call narration. The live tool "
        "functions mentioned elsewhere are unavailable in this mode. Keep ordinary "
        "spoken replies concise, usually 1-4 sentences."
    )
    messages = [{"role": "system", "content": local_instruction}]
    if conversation_id:
        for turn in memory.get_turns(conversation_id)[-16:]:
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if turn.get("role") == "tutor" else "user"
            messages.append({"role": role, "content": text})
    return messages


def _ollama_chat(model_name: str, messages: list[dict]) -> str:
    data = _ollama_request(
        "/api/chat",
        {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0.65, "num_ctx": 4096},
        },
        timeout=180,
    )
    content = str((data.get("message") or {}).get("content") or "").strip()
    content = _THINK_RE.sub("", content).strip()
    if not content:
        raise RuntimeError("The local model returned an empty reply.")
    return content


def _voice_gender(voice_name: str | None) -> str:
    for voice in VOICE_OPTIONS:
        if voice.get("name") == voice_name:
            return "Male" if voice.get("gender") == "Male" else "Female"
    return "Female"


def _synthesize_windows_pcm24(text: str, gender: str) -> bytes:
    powershell = (
        shutil.which("powershell")
        or shutil.which("powershell.exe")
        or shutil.which("pwsh")
    )
    if not powershell:
        raise LocalSetupError("Windows speech synthesis could not find PowerShell.")

    script = r"""
param(
  [Parameter(Mandatory=$true)][string]$TextPath,
  [Parameter(Mandatory=$true)][string]$WavPath,
  [Parameter(Mandatory=$true)][string]$Gender
)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $culture = New-Object System.Globalization.CultureInfo("en-US")
  $voiceGender = if ($Gender -eq "Male") {
    [System.Speech.Synthesis.VoiceGender]::Male
  } else {
    [System.Speech.Synthesis.VoiceGender]::Female
  }
  try {
    $synth.SelectVoiceByHints(
      $voiceGender,
      [System.Speech.Synthesis.VoiceAge]::Adult,
      0,
      $culture
    )
  } catch {}
  $synth.Rate = 0
  $synth.Volume = 100
  $synth.SetOutputToWaveFile($WavPath)
  $synth.Speak([System.IO.File]::ReadAllText($TextPath))
} finally {
  $synth.Dispose()
}
"""
    with tempfile.TemporaryDirectory(prefix="zeiad-local-tts-") as tmp:
        tmp_path = Path(tmp)
        text_path = tmp_path / "reply.txt"
        wav_path = tmp_path / "reply.wav"
        script_path = tmp_path / "speak.ps1"
        text_path.write_text(text, encoding="utf-8")
        script_path.write_text(script, encoding="utf-8")

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-TextPath",
                str(text_path),
                "-WavPath",
                str(wav_path),
                "-Gender",
                gender,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not wav_path.exists():
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"Windows local voice failed: {detail or 'unknown error'}"
            )

        with wave.open(str(wav_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise RuntimeError(
            f"Unsupported Windows speech sample width: {sample_width * 8}-bit"
        )

    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        samples = samples.astype(np.float32)

    if sample_rate != 24000 and samples.size:
        divisor = math.gcd(sample_rate, 24000)
        samples = resample_poly(
            samples,
            24000 // divisor,
            sample_rate // divisor,
        )

    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


async def _send_audio_chunks(websocket: WebSocket, pcm24: bytes) -> None:
    chunk_bytes = 24000
    for offset in range(0, len(pcm24), chunk_bytes):
        chunk = pcm24[offset : offset + chunk_bytes]
        if chunk:
            await websocket.send_json(
                {"type": "audio", "data": base64.b64encode(chunk).decode("ascii")}
            )
            await asyncio.sleep(0)


async def run_local_session(
    websocket: WebSocket,
    *,
    profile: dict,
    conv: dict | None,
    conv_config: dict,
    system_instruction: str,
) -> None:
    """Run one Local Free websocket session using the existing browser protocol."""
    local_model = (profile.get("local_model") or DEFAULT_LOCAL_MODEL).strip()
    try:
        _require_faster_whisper()
        await asyncio.to_thread(_check_ollama_model, local_model)
    except Exception as exc:
        await websocket.send_json(
            {"type": "error", "kind": "local_setup", "message": str(exc)}
        )
        await websocket.send_json(
            {"type": "session_status", "model_name": None, "unavailable": True}
        )
        await websocket.close()
        return

    await websocket.send_json(
        {
            "type": "session_status",
            "resumed": False,
            "conversation_name": (conv or {}).get("name"),
            "model_name": f"Local Free · {local_model}",
        }
    )

    session_started = time.monotonic()
    response_task: asyncio.Task | None = None
    turn_audio = bytearray()
    quiz_state = {"active": False, "quiz_id": None}

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
    hf_state = {
        "active": False,
        "window_buffer": bytearray(),
        "turn_buffer": bytearray(),
    }

    async def stop_active_response() -> None:
        nonlocal response_task
        if response_task and not response_task.done():
            response_task.cancel()
            await websocket.send_json({"type": "interrupted"})
        response_task = None

    async def process_text_turn(text: str, *, store_user: bool = True) -> None:
        text = text.strip()
        if not text:
            await websocket.send_json({"type": "turn_complete"})
            return

        await websocket.send_json({"type": "waiting_long", "active": True})
        try:
            if conv is not None and store_user:
                memory.insert_turn(conv["id"], "user", text)
            if store_user:
                await websocket.send_json({"type": "transcript_in", "text": text})

            messages = _recent_messages(
                conv["id"] if conv is not None else None,
                system_instruction,
            )
            if conv is None or not store_user:
                messages.append({"role": "user", "content": text})

            reply = await asyncio.to_thread(_ollama_chat, local_model, messages)

            if conv is not None:
                memory.insert_turn(conv["id"], "tutor", reply)
            await websocket.send_json({"type": "transcript_out", "text": reply})

            try:
                pcm24 = await asyncio.to_thread(
                    _synthesize_windows_pcm24,
                    reply,
                    _voice_gender(conv_config.get("voice_name")),
                )
                await _send_audio_chunks(websocket, pcm24)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "kind": "local_tts",
                        "message": f"Local voice could not speak this reply: {exc}",
                    }
                )

            await websocket.send_json({"type": "turn_complete"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "kind": "local_runtime",
                    "message": f"Local Free mode error: {exc}",
                }
            )
            await websocket.send_json({"type": "turn_complete"})
        finally:
            try:
                await websocket.send_json({"type": "waiting_long", "active": False})
            except Exception:
                pass

    async def process_audio_turn(audio_bytes: bytes) -> None:
        await websocket.send_json({"type": "waiting_long", "active": True})
        try:
            transcript = await asyncio.to_thread(_transcribe_pcm16, audio_bytes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "kind": "local_stt",
                    "message": f"Local speech recognition failed: {exc}",
                }
            )
            await websocket.send_json({"type": "turn_complete"})
            return
        finally:
            try:
                await websocket.send_json({"type": "waiting_long", "active": False})
            except Exception:
                pass

        if not transcript:
            await websocket.send_json({"type": "turn_complete"})
            return
        await process_text_turn(transcript)

    def launch_audio_turn(audio_bytes: bytes) -> None:
        nonlocal response_task
        if audio_bytes:
            response_task = asyncio.create_task(process_audio_turn(audio_bytes))

    async def close_handsfree_turn() -> None:
        data = bytes(hf_state["turn_buffer"])
        hf_state["turn_buffer"] = bytearray()
        if data:
            await stop_active_response()
            launch_audio_turn(data)

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
        hf_state["turn_buffer"].extend(window_bytes)

    try:
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

        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if quiz_state["active"] and msg_type in {
                "start_turn",
                "audio_chunk",
                "turn_complete",
                "handsfree_start",
                "handsfree_chunk",
                "handsfree_stop",
            }:
                continue

            if msg_type == "start_turn":
                await stop_active_response()
                turn_audio = bytearray()
            elif msg_type == "audio_chunk":
                turn_audio.extend(base64.b64decode(msg.get("data") or ""))
            elif msg_type == "turn_complete":
                data = bytes(turn_audio)
                turn_audio = bytearray()
                await stop_active_response()
                launch_audio_turn(data)
            elif msg_type == "handsfree_start":
                hf_state["active"] = True
                hf_state["window_buffer"] = bytearray()
                hf_state["turn_buffer"] = bytearray()
            elif msg_type == "handsfree_chunk":
                if not hf_state["active"]:
                    continue
                hf_state["window_buffer"].extend(
                    base64.b64decode(msg.get("data") or "")
                )
                while len(hf_state["window_buffer"]) >= HANDSFREE_WINDOW_BYTES:
                    window = bytes(
                        hf_state["window_buffer"][:HANDSFREE_WINDOW_BYTES]
                    )
                    del hf_state["window_buffer"][:HANDSFREE_WINDOW_BYTES]
                    await handle_handsfree_window(window)
            elif msg_type == "handsfree_stop":
                hf_state["active"] = False
                hf_state["window_buffer"] = bytearray()
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
                items = quizzes.get_quiz_items(quiz_id)
                correct = sum(1 for item in items if item["is_correct"])
                await stop_active_response()
                response_task = asyncio.create_task(
                    process_text_turn(
                        f"[Quiz results: {correct}/{len(items)} correct. "
                        "Respond naturally and continue the lesson.]",
                        store_user=False,
                    )
                )
            elif msg_type == "quiz_skip":
                quiz_id = msg["quiz_id"]
                quizzes.finalize_quiz_session(quiz_id, status="skipped")
                quiz_state["active"] = False
                quiz_state["quiz_id"] = None
            elif msg_type == "close":
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        if response_task and not response_task.done():
            response_task.cancel()
        if profile.get("id"):
            elapsed = max(0, int(time.monotonic() - session_started))
            memory.add_seconds_studied(profile["id"], elapsed)
