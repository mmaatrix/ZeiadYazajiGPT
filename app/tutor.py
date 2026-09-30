import aiosqlite

from app.db import DB_PATH
from app.providers.base import LLMProvider, TutorReply


class TutorService:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def create_session(self, scenario: str, level: str) -> int:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO sessions (scenario, level) VALUES (?, ?)",
                (scenario, level),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def send_text(
        self,
        session_id: int,
        user_text: str,
        *,
        scenario: str,
        level: str,
    ) -> TutorReply:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO conversation_turns (session_id, role, text) VALUES (?, 'user', ?)",
                (session_id, user_text),
            )
            await db.commit()

        reply = await self.provider.reply(user_text, scenario=scenario, level=level)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO conversation_turns (session_id, role, text) VALUES (?, 'assistant', ?)",
                (session_id, reply.text),
            )
            if reply.correction or reply.explanation_ar:
                await db.execute(
                    """
                    INSERT INTO learning_events
                    (session_id, event_type, original_text, corrected_text, explanation_ar)
                    VALUES (?, 'language_correction', ?, ?, ?)
                    """,
                    (session_id, user_text, reply.correction, reply.explanation_ar),
                )
            await db.commit()

        return reply
