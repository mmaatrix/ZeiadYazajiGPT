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
