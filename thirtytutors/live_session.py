"""Compatibility import for the original ThirtyTutors module name.

The active live-session implementation is now OpenAI Realtime. Keeping this
module name avoids breaking third-party imports while removing the Gemini SDK
from the runnable package.
"""

from .openai_live_session import *  # noqa: F403
