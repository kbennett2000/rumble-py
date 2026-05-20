# CLAUDE.md — project context for Claude Code

## What this project is

**rumble-py** is a cross-platform application that links analog amateur radios
over the internet using the Mumble VOIP protocol. A user keys their radio,
sends DTMF tones, and those tones drive commands (connect to server, change
channel, identify, etc.). The radio's voice audio is forwarded into and out of
a Mumble voice channel so multiple operators on different radios — possibly
on different continents — can talk as if they shared a repeater.

This is a ground-up Python rewrite of an older C# Windows-only application.
The original shelled out to the Mumble desktop client; this version speaks the
Mumble protocol directly.

## Target platforms

- **Linux** — primary development and deployment target (Ubuntu 24.04+).
- **Windows** — secondary, must work but receives less testing.
- **macOS** — not supported. Don't add Mac-specific code paths.

## Stack

- Python 3.11+
- [pymumble](https://github.com/azlux/pymumble) — Mumble protocol client
- [sounddevice](https://python-sounddevice.readthedocs.io/) — audio I/O (PortAudio)
- numpy — DSP for DTMF detection
- [FastAPI](https://fastapi.tiangolo.com/) + uvicorn — small web UI for configuration
- pyttsx3 — offline text-to-speech for voice prompts
- pyyaml — config files

## Architecture principle

rumble-py speaks the Mumble protocol **directly** via pymumble. It does **not**
shell out to or otherwise control the Mumble desktop client. The audio flow is:

```
radio mic  →  sounddevice input  →  DTMF analyzer  →  command dispatcher
                                 ↘                  ↘
                                  → pymumble voice stream  →  Mumble server
                                                              ↓
                                  ← pymumble voice stream  ←  Mumble server
sounddevice output  →  radio speaker (PTT-keyed)
```

A small FastAPI app provides a config UI on localhost.

## Code style

- **Formatter:** black, line length **100**.
- **Linter:** ruff, must be clean (`ruff check .`).
- **Type hints** on all public functions and methods.
- **Docstrings:** Google style (Args / Returns / Raises sections).
- One-line module-level comment at the top of each `.py` file describing its purpose.

## Testing

- Test runner: **pytest**.
- The **DTMF state machine** in `src/rumble/dtmf.py` requires **full unit-test
  coverage**. It's the brain of the application — every transition and every
  edge case must be tested.
- Audio I/O and Mumble integration code can have lighter coverage (these need
  real hardware or a network to exercise). Prefer integration tests with the
  local Docker Mumble server over heavy mocking.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add DTMF Goertzel detector
fix: correct sample-rate mismatch in audio pump
docs: explain channel mapping format
test: add coverage for partial DTMF sequences
chore: bump pymumble pin
```

## Working with the user

The user is a Python beginner but an experienced engineer in other languages
(notably C#). When making non-obvious choices, leave a brief comment explaining
**why** — not just what. When asked to do something, end your response with a
short plain-English summary of what you did and why.
