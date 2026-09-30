# Upstream Migration Notes

## Baseline

- Upstream project: ThirtyTutors
- Upstream repository: https://github.com/wiss84/thirtytutors
- Baseline release reviewed: v1.2.1
- Upstream author: Wissam Metawee
- Upstream license: PolyForm Noncommercial License 1.0.0

## Zeiad Yazaji modifications

1. Replaced Gemini Live integration with OpenAI Realtime.
2. Current Realtime model choices: gpt-realtime-2.1 and gpt-realtime-2.1-mini.
3. Mapped the existing 30 avatar/persona entries to current OpenAI built-in Realtime voices.
4. Default learner language: Arabic.
5. Default target: American English.
6. Rolling memory summaries use gpt-5.6-luna through the OpenAI Responses API.
7. Preserved the existing local SQLite profile/conversation/quiz/statistics architecture.
8. Preserved the existing browser websocket protocol so the existing avatar/UI layer can continue to be reused.
9. Preserved upstream attribution and the upstream noncommercial license notice.

## Security

- API keys are stored per local profile and are not committed to the repository.
- .env is ignored by Git.
- Do not place an OpenAI API key in frontend JavaScript.
