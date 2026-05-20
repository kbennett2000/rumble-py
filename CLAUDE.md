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

## Known workarounds

### pymumble + Python 3.12 SSL shim

pymumble 1.6.1 (the current PyPI release) calls `ssl.wrap_socket()`, which
Python 3.12 removed. Upstream master has been fixed but no release has
shipped. To unblock Python 3.12 + 3.13 we install a shim at the top of
`src/rumble/mumble_client.py` that re-implements `ssl.wrap_socket` using
`ssl.SSLContext`. The shim runs at module import time, before
`pymumble_py3` is imported, and is guarded by `if not hasattr(ssl,
"wrap_socket")` so it's a no-op on Python ≤ 3.11.

`pymumble` is pinned to `==1.6.1` in `pyproject.toml` so a new release
can't silently change the SSL setup out from under the shim.

**To remove this workaround once upstream cuts a fixed release:**

1. Delete the `if not hasattr(ssl, "wrap_socket"): …` block (and the
   surrounding comment) from the top of `src/rumble/mumble_client.py`.
2. Drop the `import ssl` if nothing else in the file uses it.
3. Bump the pinned `pymumble==1.6.1` in `pyproject.toml` to the new
   version, and update the comment block above the dependency.
4. Re-install (`pip install -e ".[dev]"`) and run the full integration
   suite (`cd docker && docker compose up -d && cd .. &&
   RUMBLE_INTEGRATION=1 pytest -v -k mumble`). All 43 tests must pass.

## Documentation conventions

### Audience and voice

The user-facing docs are written for a **licensed amateur radio operator**
who:

- Knows radio (bands, RF, PTT, repeaters, callsigns, FCC §97.119 or
  equivalent, Morse code identification requirements).
- Knows in principle how to wire a sound-card interface to a radio.
- **May not** know modern VOIP concepts (what Mumble is, what a
  "channel" means in Mumble, TLS certs, port forwarding for VOIP).
- **May not** be deep in Linux command-line tools beyond the basics.
- Could be a Tech-class, brand new, or a 50-year Extra — write for the
  middle.

Voice is **friendly-expert**. Warm enough that a new Tech doesn't bounce,
technical enough that an Extra isn't talked down to. Active voice. Use
**"we"** for the project (*"we built this because…"*) and **"you"** for
the reader (*"when you connect your radio…"*). Never **"the user"** in
operating contexts — always **"operator"**.

Dry humor is fine in prose; never in reference tables, command syntax, or
troubleshooting steps. No memes, no marketing fluff, no "blazingly fast."
No emoji except 📻 in the README title and **73!** as the README sign-off.

### Amateur radio register

Lean into the ham culture. Don't soften the vocabulary:

- "Shack reference card" or "operating card," not "quick reference."
- "Operator" instead of "user" in operating contexts.
- Reference FCC §97.119 where appropriate; acknowledge operators
  outside the U.S. have equivalent rules.
- Verbs like "kerchunk," "key up," "key down" used naturally.
- Concepts like simplex, repeater, net, courtesy tone, deviation, used
  without apology.

### Callsigns in examples

The project author's callsign is **AE9S**. Use it for "the node speaking"
and for any example that needs a specific real-feeling identity.

For examples involving other operators or other nodes, use **fictional
callsigns**:

- `W0XYZ`
- `N1ABC`
- `K9TST`

Never use a real, currently-licensed callsign for a hypothetical scenario.

### The seven documents

The user-facing documentation set is seven files. Each has a specific
job; keep them in their lane.

1. **[README.md](README.md)** — the front door. Long-form. What rumble-py
   is, why it exists, how to install it, how to operate it, status,
   roadmap. Links out to everything else.
2. **[docs/CHEAT_SHEET.md](docs/CHEAT_SHEET.md)** — one-page printable
   reference card for the shack. Dense, no prose, fits on a single 8.5×11
   page.
3. **[docs/CONCEPTS.md](docs/CONCEPTS.md)** — Mumble, VOIP, channels,
   certs, comparison to IRLP / EchoLink / AllStar / Hamshack Hotline. For
   hams new to VOIP.
4. **[docs/HARDWARE.md](docs/HARDWARE.md)** — radios, sound-card
   interfaces, audio levels, PTT options, common gotchas. Practical, not
   theoretical.
5. **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** — every field in
   `config.yaml`, what it does, what the validator will reject, plus a
   fully-annotated example.
6. **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — Raspberry Pi setup,
   systemd unit, log rotation, network considerations, multi-node
   topologies. For "I'm putting this in a real shack."
7. **[CLAUDE.md](CLAUDE.md)** — this file. Internal conventions and
   collaborator context.

### Consistent terminology

Use these terms exactly; pick one and stick with it.

| Use | Not |
|---|---|
| Mumble server | Murmur (except once in CONCEPTS, to help Google) |
| Node | rumble-py instance / install / box |
| Operator | User (only in operating contexts) |
| Bank | Profile / mode / configuration |
| Channel mapping | Channel entry / route / DTMF route |
| DTMF command, command sequence | Control code / key combo |

### Do not write like this

Negative examples to keep future doc edits consistent:

> "🚀 Rumble is a blazingly fast, modern, cross-platform solution for
> linking your analog radios over the internet using cutting-edge
> technology!"

No emoji bombs. No "blazingly fast." No marketing adjectives.

> "In order to facilitate the user's interaction with the Mumble
> protocol, the application provides a comprehensive interface that
> exposes all available functionality through a well-designed API."

No corporate fluff. Say what it does in plain words.

> "The user should ensure that the configuration file is properly
> formatted before attempting to start the application."

Active voice. "You" not "the user." Concrete: *"Validate the config with
`python -m rumble --config path/to/file.yaml`; the loader will tell you
which field is wrong."*

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
