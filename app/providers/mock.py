from app.providers.base import LLMProvider, TutorReply


class MockTutorProvider(LLMProvider):
    """Development provider used before a real LLM adapter is configured."""

    async def reply(self, user_text: str, *, scenario: str, level: str) -> TutorReply:
        cleaned = user_text.strip()
        return TutorReply(
            text=(
                f"I heard: {cleaned}. The tutor engine is running in development mode. "
                "A real OpenAI, Gemini, or local LLM adapter will replace this response."
            ),
            explanation_ar="المحرك يعمل حاليًا بوضع الاختبار، ولم يتم ربط نموذج المحادثة الحقيقي بعد.",
        )
