# Zeiad English Coach

A real-time AI language tutor for speaking practice, maintained by **Zeiad Yazaji**.

This repository is a derivative of the **ThirtyTutors** project. The goal is to preserve the original ThirtyTutors experience and features while replacing its Gemini Live backend with **OpenAI Realtime**.

## Current migration

- Full ThirtyTutors source/UI is the project baseline.
- Profiles, 3D avatars, memory, quizzes, statistics, scenarios, backups, and hands-free speaker verification are retained.
- Live voice sessions now route through `gpt-realtime-2.1`.
- The existing 16 kHz microphone stream is resampled to 24 kHz for OpenAI Realtime.
- OpenAI audio output remains 24 kHz PCM and feeds the existing avatar/lip-sync playback.
- Rolling summaries use `gpt-5.6-luna`.
- OpenAI API credentials replace Gemini API credentials.
- Default learner language is Arabic and the target is American English.
- Current Realtime choices are `gpt-realtime-2.1` and `gpt-realtime-2.1-mini`.

## Developer

**Zeiad Yazaji**  
GitHub: **@mmaatrix**

## Windows development

```powershell
git clone https://github.com/mmaatrix/ZeiadYazajiGPT.git
cd ZeiadYazajiGPT
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
thirtytutors setup
thirtytutors
```

The internal Python module/command remains `thirtytutors` for compatibility with the upstream codebase; the visible product name is **Zeiad English Coach**.

## OpenAI API

Realtime voice uses an OpenAI API key. ChatGPT subscription billing and API billing are separate.

## License and attribution

This derivative remains subject to the upstream **PolyForm Noncommercial License 1.0.0** in `LICENSE`.

The upstream-required copyright notice remains in that license. Current modifications and maintenance of this repository are by **Zeiad Yazaji**.

Upstream project: `wiss84/thirtytutors`.
