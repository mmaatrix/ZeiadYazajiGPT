import base64

import numpy as np

from thirtytutors import constants
from thirtytutors.openai_live_session import _pcm16_16k_to_24k_b64
from thirtytutors.tutor_tools import MOOD_TOOL, build_quiz_tool


def test_default_model_is_openai_realtime():
    assert constants.DEFAULT_MODEL == "gpt-realtime-2.1"


def test_avatar_voice_maps_to_supported_openai_voice():
    supported = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
    assert constants.get_api_voice_name(constants.DEFAULT_VOICE) in supported


def test_pcm16_resampler_16k_to_24k():
    samples = (np.sin(np.linspace(0, 10, 1600)) * 12000).astype("<i2")
    encoded = base64.b64encode(samples.tobytes()).decode("ascii")
    converted = _pcm16_16k_to_24k_b64(encoded)
    output = np.frombuffer(base64.b64decode(converted), dtype="<i2")
    assert 2395 <= len(output) <= 2405


def test_openai_tool_schemas():
    assert MOOD_TOOL["type"] == "function"
    assert MOOD_TOOL["name"] == "set_mood"
    quiz = build_quiz_tool(native_language="Arabic", target_language="English")
    assert quiz["type"] == "function"
    assert quiz["name"] == "start_quiz"


class _FakeRealtimeSocket:
    def __init__(self):
        self.events = []

    async def send(self, payload):
        import json
        self.events.append(json.loads(payload))


def test_realtime_session_config_uses_24khz_audio():
    import asyncio

    from thirtytutors.openai_live_session import _configure_session

    sock = _FakeRealtimeSocket()
    profile = {"id": "test-profile", "name": "Zeiad"}
    config = {
        "native_language": "Arabic",
        "target_language": "English",
        "voice_name": constants.DEFAULT_VOICE,
        "scenario": "free_learning",
        "difficulty": "intermediate",
    }

    asyncio.run(
        _configure_session(
            sock,
            profile=profile,
            conv_config=config,
            model_name="gpt-realtime-2.1",
            summary_text=None,
            review_terms=[],
            taught_vocab=[],
        )
    )

    event = sock.events[0]
    assert event["type"] == "session.update"
    assert "model" not in event["session"]
    assert event["session"]["audio"]["input"]["format"]["rate"] == 24000
    assert event["session"]["audio"]["input"]["turn_detection"] is None
    assert event["session"]["audio"]["output"]["format"]["rate"] == 24000
    assert event["session"]["audio"]["input"]["transcription"]["model"] == "gpt-4o-transcribe"
