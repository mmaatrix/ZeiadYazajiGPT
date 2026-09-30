import base64
import struct

from thirtytutors.constants import (
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    VOICE_NAME_TO_API,
)
from thirtytutors.openai_live_session import _pcm16_16k_to_24k_b64


def test_openai_american_english_defaults():
    assert DEFAULT_MODEL == "gpt-realtime-2.1"
    assert DEFAULT_NATIVE_LANGUAGE == "Arabic"
    assert DEFAULT_TARGET_LANGUAGE == "American English"


def test_all_avatar_voice_mappings_use_supported_realtime_voices():
    supported = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
    assert VOICE_NAME_TO_API
    assert set(VOICE_NAME_TO_API.values()) <= supported


def test_pcm_16khz_is_resampled_to_24khz():
    samples = list(range(160))
    raw = struct.pack("<160h", *samples)
    encoded = base64.b64encode(raw).decode("ascii")
    converted = base64.b64decode(_pcm16_16k_to_24k_b64(encoded))
    assert len(converted) == 240 * 2
