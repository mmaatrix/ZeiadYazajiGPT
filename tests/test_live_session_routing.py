"""Integration tests for the ThirtyTutors-derived OpenAI Realtime relay.

The browser protocol remains the original project's protocol, while the
provider boundary is tested against a fake OpenAI Realtime WebSocket. No
real API key or network connection is used.
"""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thirtytutors import live_session, memory, quizzes

pytestmark = pytest.mark.integration


class FakeOpenAIWebSocket:
    """Small event-driven stand-in for the OpenAI Realtime WebSocket."""

    def __init__(self, *, tool_mode=False):
        self.sent = []
        self.closed = False
        self._events = asyncio.Queue()
        self._events.put_nowait({
            "type": "session.created",
            "session": {"type": "realtime", "id": "sess-test"},
        })
        self._response_count = 0
        self._tool_mode = tool_mode
        self._queue_task = None

    async def send(self, payload):
        event = json.loads(payload)
        self.sent.append(event)
        event_type = event.get("type")

        if event_type == "session.update":
            await self._events.put({
                "type": "session.updated",
                "session": event.get("session", {}),
            })
        elif event_type == "input_audio_buffer.commit":
            await self._events.put({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": f"item-user-{self._response_count}",
                "transcript": "Hello, I am ready to practice.",
            })
        elif event_type == "response.create":
            if self._tool_mode and self._response_count == 0:
                await self._events.put({
                    "type": "response.done",
                    "response": {
                        "status": "completed",
                        "output": [{
                            "type": "function_call",
                            "id": "fc-1",
                            "call_id": "call-1",
                            "name": "set_mood",
                            "arguments": json.dumps({"mood": "happy"}),
                        }],
                    },
                })
            else:
                await self._events.put({
                    "type": "response.output_audio_transcript.delta",
                    "delta": "Great! Let's practice in American English.",
                })
                await self._events.put({
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(b"\\x00\\x00" * 120).decode("ascii"),
                })
                await self._events.put({
                    "type": "response.done",
                    "response": {
                        "status": "completed",
                        "output": [],
                    },
                })
            self._response_count += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed and self._events.empty():
            raise StopAsyncIteration
        return json.dumps(await self._events.get())

    async def close(self):
        self.closed = True


@pytest.fixture
def ws_app(monkeypatch):
    monkeypatch.setattr(live_session, "summarize_conversation", lambda *a, **k: None)

    def _make(fake_ws):
        async def fake_connect(*args, **kwargs):
            return fake_ws

        monkeypatch.setattr(live_session, "_connect_openai", fake_connect)
        app = FastAPI()
        app.include_router(live_session.router)
        return TestClient(app)

    return _make


def _init_message(profile, conv):
    return {
        "type": "init",
        "profile_id": profile["id"],
        "profile_name": profile["name"],
        "conversation_id": conv["id"],
    }


def test_normal_turn_flow_routes_audio_and_persists_transcript(
    ws_app, make_profile, make_conversation
):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="American English")
    fake_ws = FakeOpenAIWebSocket()
    client = ws_app(fake_ws)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(_init_message(profile, conv))
        status = ws.receive_json()
        assert status["type"] == "session_status"
        assert status["resumed"] is False
        assert status["model_name"]

        ws.send_json({"type": "start_turn"})
        audio = base64.b64encode(b"\\x00\\x01" * 80).decode("ascii")
        ws.send_json({"type": "audio_chunk", "data": audio})
        ws.send_json({"type": "turn_complete"})

        assert ws.receive_json() == {
            "type": "transcript_in",
            "text": "Hello, I am ready to practice.",
        }
        assert ws.receive_json()["type"] == "transcript_out"
        assert ws.receive_json()["type"] == "audio"
        assert ws.receive_json() == {"type": "turn_complete"}

        ws.send_json({"type": "close"})

    turns = memory.get_turns(conv["id"])
    assert [(t["role"], t["text"]) for t in turns] == [
        ("user", "Hello, I am ready to practice."),
        ("tutor", "Great! Let's practice in American English."),
    ]

    event_types = [event["type"] for event in fake_ws.sent]
    assert event_types[0] == "session.update"
    assert "input_audio_buffer.append" in event_types
    assert "input_audio_buffer.commit" in event_types
    assert "response.create" in event_types

    session = fake_ws.sent[0]["session"]
    assert session["type"] == "realtime"
    assert session["output_modalities"] == ["audio"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}


def test_openai_function_call_is_executed_and_followed_by_response(
    ws_app, make_profile, make_conversation
):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="American English")
    fake_ws = FakeOpenAIWebSocket(tool_mode=True)
    client = ws_app(fake_ws)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(_init_message(profile, conv))
        ws.receive_json()

        ws.send_json({"type": "start_turn"})
        ws.send_json({
            "type": "audio_chunk",
            "data": base64.b64encode(b"\\x00\\x01" * 80).decode("ascii"),
        })
        ws.send_json({"type": "turn_complete"})

        assert ws.receive_json()["type"] == "mood_change"
        assert ws.receive_json()["type"] == "transcript_out"
        assert ws.receive_json()["type"] == "audio"
        assert ws.receive_json() == {"type": "turn_complete"}
        ws.send_json({"type": "close"})

    tool_outputs = [
        event for event in fake_ws.sent
        if event["type"] == "conversation.item.create"
        and event.get("item", {}).get("type") == "function_call_output"
    ]
    assert tool_outputs
    assert tool_outputs[0]["item"]["call_id"] == "call-1"


def test_missing_api_key_reports_friendly_error(make_profile, make_conversation):
    profile = make_profile(api_key=None)
    conv = make_conversation(profile["id"], target_language="American English")

    app = FastAPI()
    app.include_router(live_session.router)
    client = TestClient(app)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(_init_message(profile, conv))
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "API key" in msg["message"]


def test_init_must_be_the_first_message(ws_app):
    client = ws_app(FakeOpenAIWebSocket())
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start_turn"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_unknown_profile_id_can_start_ephemeral_session(ws_app):
    fake_ws = FakeOpenAIWebSocket()
    client = ws_app(fake_ws)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({
            "type": "init",
            "profile_id": "does-not-exist",
            "profile_name": "Guest",
            "target_language": "American English",
        })
        status = ws.receive_json()
        assert status["type"] == "session_status"
        assert status["resumed"] is False
        ws.send_json({"type": "start_turn"})
        ws.send_json({
            "type": "audio_chunk",
            "data": base64.b64encode(b"\\x00\\x01" * 80).decode("ascii"),
        })
        ws.send_json({"type": "turn_complete"})
        assert ws.receive_json()["type"] == "transcript_in"
        assert ws.receive_json()["type"] == "transcript_out"
        assert ws.receive_json()["type"] == "audio"
        assert ws.receive_json()["type"] == "turn_complete"
        ws.send_json({"type": "close"})


def test_quiz_answer_persists_incrementally(ws_app, make_profile, make_conversation):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="American English")
    quiz_id = quizzes.start_quiz_session(
        conv["id"],
        "multiple_choice",
        {
            "items": [{
                "target_term": "branch",
                "question": "Choose the meaning.",
                "item_type": "multiple_choice",
                "choices": ["فرع", "رصيد"],
                "correct_choice_index": 0,
                "text_with_blanks": "",
                "correct_answers": [],
                "word_bank": [],
            }]
        },
    )
    fake_ws = FakeOpenAIWebSocket()
    client = ws_app(fake_ws)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(_init_message(profile, conv))
        assert ws.receive_json()["type"] == "session_status"
        resume = ws.receive_json()
        assert resume["type"] == "quiz_resume"
        assert resume["quiz_id"] == quiz_id

        ws.send_json({
            "type": "quiz_answer",
            "quiz_id": quiz_id,
            "item_index": 0,
            "target_term": "branch",
            "prompt_or_text": "Choose the meaning.",
            "correct_answer": "فرع",
            "student_answer": "فرع",
            "is_correct": True,
        })
        ws.send_json({"type": "quiz_skip"})
        ws.receive_json()
        ws.send_json({"type": "close"})

    items = quizzes.get_quiz_items(quiz_id)
    assert items[0]["student_answer"] == "فرع"
    assert items[0]["is_correct"] is True
