from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class TutorReply:
    text: str
    correction: str | None = None
    explanation_ar: str | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def reply(self, user_text: str, *, scenario: str, level: str) -> TutorReply:
        raise NotImplementedError


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: str) -> str:
        raise NotImplementedError


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, *, speed: float = 1.0) -> bytes:
        raise NotImplementedError


class PronunciationProvider(ABC):
    @abstractmethod
    async def assess(self, audio_path: str, reference_text: str) -> dict:
        """Return real pronunciation measurements, not merely STT similarity."""
        raise NotImplementedError
