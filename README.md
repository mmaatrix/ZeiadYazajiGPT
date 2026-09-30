# Zeiad English Coach

Windows-first American-English voice tutor for Arabic speakers.

## Target experience

- Real-time microphone conversation in American English
- Arabic explanations on demand
- Grammar correction and natural American phrasing
- Adjustable speech speed, replay, and interruption support
- Daily-life roleplay: work, interviews, shopping, school, phone calls, introductions
- Persistent progress: recurring mistakes, vocabulary, retries, and sessions
- Real pronunciation assessment at phoneme/stress/rhythm level
- Local-first components where practical, with optional cloud providers

## Architecture

```text
Microphone
   -> STT adapter
   -> Tutor / LLM adapter
   -> TTS adapter
   -> Speaker

Learner recording
   -> Pronunciation adapter
   -> phoneme / stress / rhythm feedback
   -> retry + progress
```

The core design rule is provider independence: Gemini, OpenAI, and local engines can be swapped without rewriting the entire application.

## Initial stack

- Backend: FastAPI
- Database: SQLite
- STT: faster-whisper adapter
- LLM: OpenAI / Gemini / local llama.cpp-compatible adapters
- TTS: Kokoro / Piper adapters
- Pronunciation: pluggable phoneme-level assessment adapter
- UI: local Windows-friendly web app

## Status

Phase 1 scaffold is being built now.
