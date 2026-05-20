# 📻 rumble-py

A DTMF-controlled Mumble client for linking amateur radios over the internet.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

---

> **Status — working, but actively under construction.**
>
> Solid and tested today: DTMF detection from a sound card, full Mumble
> protocol client (connect, join channels, send/receive audio, auto-
> reconnect), command state machine, TTS announcements, web UI.
>
> Coming next (milestone 7): real-time radio audio passthrough into the
> Mumble channel, CW WAV ident playback on a timer, and proper packaging
> for a one-line install on a Raspberry Pi. After that: CSRF on the web
> UI, multi-node admin views, optional bridges to IRLP / EchoLink.

---

## What is this for?

Rumble wraps the open-source [Mumble](https://www.mumble.info/) VOIP system
with a DTMF listener, so you can control a Mumble client over the air using
nothing but the touch-tone keypad on your radio. Tap a few keys, hear a
synthesized voice confirm the channel change, and you're talking to
operators on the other side of the planet through your handheld.

The mental model is the same one IRLP and EchoLink built thirty years of
ham culture around: your radio talks to a small node computer; the node
talks to a hub on the internet; the hub talks to other nodes; other nodes
talk to other radios. You key up here, somebody keys up there, RF on both
ends.

What's different is the plumbing. Rumble doesn't have its own protocol or
its own directory service. The hub is just a Mumble server — open source
software you can run on a $5/mo VPS, an old PC, or a Raspberry Pi in your
shack. The directory of nodes is whatever Mumble channels you choose to
create. The DTMF grammar is yours to configure. None of it depends on a
proprietary company staying in business, and nothing about it phones home.

If you've been waiting for "IRLP, but the modern way" — that's the goal.

## Why would I want to use this?

It's worth being explicit about the alternatives, because they're all good
projects and the right answer depends on what you're trying to do.

| | Rumble | IRLP | EchoLink | AllStar Link | Hamshack Hotline |
|---|---|---|---|---|---|
| Protocol | Mumble (open) | Proprietary | Proprietary | IAX/Asterisk (open) | SIP (open) |
| Directory | None — you choose | Centralized | Centralized | Centralized | Centralized |
| Auth | Server-side, your call | Hardware ID per node | Per-callsign | Asterisk-style | Per-extension |
| Self-hosted hub | Yes (Mumble server) | No | No | Yes (private) | No (Asterisk possible) |
| OS | Linux / Windows | Linux | Windows / mobile | Linux | Mostly hardware boxes |
| Hardware floor | Pi 4 (~$45) | Dedicated IRLP node | Sound card | Pi or PC | Cisco/Yealink phone |
| Use case | Linking, nets, rag-chew | Linking, nets | Casual contacts | Linking, nets, dispatch | Phone-style contacts |

Rumble's pitch in one sentence: **fully open, fully self-hosted, runs on
hardware you already own, and the protocol underneath it is a 20-year-old
VOIP standard that just keeps working**. There's no central server we can
take down. No callsign approval queue. No proprietary client. The trade-
off is that you do more of the setup yourself — though we've tried hard to
make that setup obvious.

## How it works

The data flow, in one picture:

```
       ┌─────────────────────┐
       │   YOUR RADIO        │
       │ (HT, mobile, etc.)  │
       └──┬───────────────┬──┘
          │ RX audio      │ TX audio
          │ (DTMF + voice)│ (voice + ident)
          ▼               ▲
       ┌─────────────────────┐    ┌──────────────────┐
       │  SOUND-CARD         │    │   WEB UI         │
       │  INTERFACE          │    │  (browser →      │
       │  (SignaLink, etc.)  │    │   127.0.0.1:8080)│
       └──┬───────────────┬──┘    └────────┬─────────┘
          │ float32 PCM   ▲                │
          ▼               │ int16 PCM      │
       ┌──────────────────────────────────────┐
       │           RUMBLE-PY NODE             │
       │                                      │
       │  audio capture  →  DTMF detector ──→ │ commands
       │                                      │
       │  TTS synthesis  ──┐                  │
       │                   ▼                  │
       │            Mumble client wrapper     │
       └────────────────┬─────────────────────┘
                        │ TCP+UDP 64738, TLS
                        ▼
       ┌──────────────────────────────────────┐
       │          MUMBLE SERVER               │
       │  (self-hosted, VPS, or public)       │
       └────────────────┬─────────────────────┘
                        │
                        ├── other rumble-py nodes
                        ├── other radios
                        └── plain Mumble desktop clients
```

The DTMF control path is local — operators command **their own node**, not
some remote endpoint. When you key your radio and send `#001#1*`, that
sequence is heard by your node's sound card, decoded by your node's
detector, dispatched to your node's command handler, which then tells
*your* Mumble client where to go. It's the same model as an IRLP touch-
tone control: the commands never leave your shack until they've been
parsed.

For a deeper walk-through of Mumble itself, channels, and how this
compares to other linking systems, see [docs/CONCEPTS.md](docs/CONCEPTS.md).

## Requirements

### Hardware

- **A DTMF-capable radio.** Any modern HT or mobile that can send touch
  tones from its keypad. The original C# project author tested on Baofeng
  UV-5R / UV-82 handhelds; that's also our primary test platform.
- **A sound-card interface between the radio and the PC.** A SignaLink
  USB, DigiRig Mobile, or any of the popular ham digital-mode
  interfaces will work. See [docs/HARDWARE.md](docs/HARDWARE.md) for
  specifics, including jumper settings, audio levels, and the SignaLink
  + Baofeng combination we use.
- **A computer running Linux or Windows.** A Raspberry Pi 4 is the sweet
  spot for a permanent node — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
  for the systemd setup. Anything that can run Python 3.11+ with
  PortAudio works.

> A laptop's built-in microphone is **not** going to work as a substitute
> for a radio interface. The audio path is too noisy and the levels are
> wrong. If you're poking at rumble-py without a radio attached, use the
> web UI's on-screen keypad to inject DTMF directly — that's exactly what
> it's for.

### Software

- **Python 3.11 or newer.** 3.12 and 3.13 both work; we ship a small
  workaround for an [unreleased pymumble bug on 3.12+](CLAUDE.md#known-workarounds).
- **A Mumble server.** For development, the included
  `docker/docker-compose.yml` runs one locally on port 64738. For
  production, you can run [Murmur](https://www.mumble.info/) on the same
  machine, on a VPS, or on a Pi. See
  [docs/CONCEPTS.md](docs/CONCEPTS.md) for hosting guidance.
- **espeak-ng on Linux** for TTS:
  `sudo apt install espeak-ng`. On Windows, pyttsx3 uses SAPI and
  needs no extra install.
- **portaudio19-dev on Linux** for sounddevice:
  `sudo apt install portaudio19-dev`.

## Quick start

This is the 5-minute path from `git clone` to "you're listening for DTMF
on a sound card and connected to a local Mumble server."

```bash
# 1. Install system dependencies (Ubuntu / Debian).
sudo apt install -y python3.11 python3.11-venv git \
                    espeak-ng portaudio19-dev docker.io

# 2. Clone and install rumble-py.
git clone https://github.com/kbennett2000/rumble-py.git
cd rumble-py
python3.11 -m venv .venv
source .venv/bin/activate                # Linux/macOS
# .venv\Scripts\activate                 # Windows PowerShell
pip install -e ".[dev]"

# 3. Bring up the local dev Mumble server (Docker).
cd docker && docker compose up -d && cd ..

# 4. Smoke-test individual components without a radio.
pytest                                   # runs the test suite
python scripts/listen_for_dtmf.py        # interactive: hear DTMF on a sound card
python scripts/mumble_smoke.py           # connects to the dev server, sends a tone
python scripts/web_smoke.py              # starts the web UI without the audio pipeline

# 5. Run the real thing. Copy and edit the example config.
cp config.example.yaml config.dev.yaml
$EDITOR config.dev.yaml                  # set your callsign, audio device, etc.
python -m rumble --config config.dev.yaml
```

That last step:

- Connects to the Mumble server defined in your config (the dev one, by
  default).
- Opens your configured audio input device and starts listening for DTMF.
- Brings up the web UI at <http://127.0.0.1:8080/>.
- Speaks `"AE9S rumble-py listening"` (substituting your callsign) into
  the Mumble channel as a startup announcement.

`Ctrl-C` shuts it down cleanly.

> **FCC reminder (and equivalent rules elsewhere):** under §97.119 you
> must identify your station at least every ten minutes while
> transmitting, and at the end of a communication. Rumble-py plays a
> WAV-file ident on a timer once you set `ident.wav_path` and run a
> ident interval under 600 seconds. Until milestone 7 lands the real
> audio passthrough, that file does not actually transmit; for now you
> ID with your own voice the way you always did. See the
> [Project status](#project-status) section for where each piece sits.

## Configuration

The config lives in a YAML file you pass with `--config`. The structure
is hierarchical, supports multiple "banks" of servers and channels (so
you can switch between, say, a morning net configuration and an evening
rag-chew configuration with one DTMF command), and is validated at load
time so a typo never silently breaks the node.

The top-level shape:

```yaml
callsign: "AE9S"          # your operator callsign
initial_bank: 0            # which bank to load on startup

audio:                     # sound-card settings
  input_device: null
  sample_rate: 8000
  dtmf_min_magnitude: 0.05

ident:                     # periodic station ID
  wav_path: "./ident.wav"
  interval_seconds: 540

web:                       # built-in web UI
  enabled: true
  host: "127.0.0.1"
  port: 8080

banks:
  0:                       # bank 0 — local development
    servers:
      - name: "local-dev"
        host: "127.0.0.1"
        port: 64738
        username: "AE9S"
    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root"
        nickname: "local lobby"
  1:                       # bank 1 — somewhere else
    ...
```

Every field, every validation rule, and a fully-annotated example config
live in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). The example file
itself is [`config.example.yaml`](config.example.yaml) — copy it, edit
it, and point `--config` at the copy.

## Operating

Once the node is up, the operator's interface is the radio keypad. The
command grammar is small and uniform:

| DTMF | Meaning |
|---|---|
| `#*` | Disconnect (move to Root channel) |
| `#N*` | Load configuration bank N (one digit 0-9) |
| `#XX#Y*` | Change admin setting XX to value Y |
| `#XXX#Y*` | Switch to server XXX, channel Y (per the channel map in the active bank) |

A few example sequences, assuming the default bank in `config.example.yaml`:

```
#001#1*    →  switch to "local lobby" (Root channel on local-dev server)
#001#2*    →  switch to "Root/Lobby" on local-dev
#00#0*     →  enable sticky mute (stays muted across commands)
#00#1*     →  clear sticky mute
#*         →  disconnect to Root
```

After every valid command, the node speaks a short confirmation through
the Mumble channel: *"switched to local lobby"*, *"muted"*, *"loaded bank
1"*, etc. The DTMF detector also auto-mutes the Mumble side while a
command is being entered, so the keypad tones don't get relayed to every
other operator on the channel. Unmuting happens automatically when the
command finishes — unless you've engaged sticky mute, in which case you
stay muted until you clear it with `#00#1*`.

The full reference, suitable for printing and clipping to your radio
desk, is [docs/CHEAT_SHEET.md](docs/CHEAT_SHEET.md).

### Your first command, narrated

Here's what actually happens, end to end, the first time you key
`#001#1*` on the radio with a freshly-started rumble-py node:

1. **You key up** and send `#` on your radio's DTMF keypad. The tone
   pair (697 Hz + 1477 Hz) goes out over the air, into your radio's
   receiver (yes, your own), out the speaker, through your sound-card
   interface, and into rumble-py's audio capture.
2. **The detector hears it.** Within ~25 ms of the tone starting,
   rumble-py classifies the audio frame as `#`. It auto-mutes the
   Mumble outbound channel so the tones don't leak to other operators.
3. **The state machine waits** for the rest of the sequence. Operators
   on the channel hear silence from your node — perfect, because they
   shouldn't have to listen to keypad tones.
4. **You finish the command** — `0`, `0`, `1`, `#`, `1`, `*`. Each tone
   is recognized as it arrives. When the trailing `*` lands, the state
   machine emits `ChangeChannel(server="001", channel="1")`.
5. **The dispatcher looks up the mapping** in your active bank's
   channel map. `001/1` → server `local-dev`, channel `Root` (per the
   example config). It tells the Mumble client to move to that channel.
6. **The TTS engine synthesizes** *"switched to local lobby"*, encodes
   it as 48 kHz mono int16 PCM, and hands it to the Mumble client.
   The Mumble client wraps it in Opus and ships it to the server.
7. **The server distributes the announcement** to everyone on
   `Root/Lobby` — including you, since you just landed there. Your
   radio's speaker now plays *"switched to local lobby"* in a slightly
   robotic voice. The node also un-mutes its outbound side.
8. **You're connected.** Anyone else on `Root/Lobby` — whether they're
   another rumble-py node or a regular Mumble desktop client — can
   hear you when (once milestone 7 lands) the audio passthrough is
   live.

End to end, steps 1-7 take roughly 1.5-2 seconds — most of that is the
operator's fingers on the keypad. The detection + dispatch part is
under 100 ms.

## The web UI

While the node is running, a small web interface is available at
<http://127.0.0.1:8080/> by default. It shows:

- **Live status** — connection state, current channel, other operators
  in that channel, current bank, and the DTMF buffer (what you've keyed
  so far in a partial command).
- **An on-screen DTMF keypad** — clicking the buttons sends commands
  through the same path a real radio's tones would. Handy for testing
  channel mappings without keying the radio, and for remote control
  from a phone on the same LAN.
- **The channel map** — every configured `(server_number,
  channel_number)` mapping for the active bank, so you don't have to
  remember which combination goes where.
- **A live log tail** — server-sent events stream the last 500 log
  lines, color-coded by level, with new lines appearing in real time.
- **One-click actions** — switch banks, mute/unmute, disconnect to
  Root, reload the config file from disk.

To disable the web UI entirely, set `web.enabled: false` in your config.

> **LAN exposure caveat.** Setting `web.host: 0.0.0.0` makes the UI
> reachable from other devices on your network. There is no
> authentication. Anyone who can reach port 8080 can change banks,
> disconnect you, and inject DTMF commands. This is fine on a trusted
> home LAN; **don't expose it to the public internet** until CSRF and
> auth land in a later milestone. If you need broader access in the
> meantime, put a reverse proxy with HTTP basic auth in front.

## Project status

We're honest about what works today and what doesn't.

### Working (tested against the dev Mumble server)

- **DTMF state machine.** All four command shapes, full unit-test
  coverage, recognizes back-to-back commands with no gap, rejects
  invalid sequences cleanly.
- **DTMF detection from a sound card.** Goertzel-based per-frame
  classifier with debounce, validated against synthesized tones for all
  16 DTMF keys and against light Gaussian noise.
- **Mumble client wrapper.** Connect/disconnect, channel walking and
  joining, mute/deaf, send/receive PCM audio, auto-reconnect with
  exponential backoff, multi-listener event registration.
- **Config loader.** Validates everything at load time. Supports banks
  with live switching via `#N*`. Reloadable from the web UI.
- **TTS announcements** via pyttsx3 (espeak-ng on Linux, SAPI on
  Windows). Resamples to 48 kHz mono int16 for Mumble.
- **Web UI.** All routes, partials, SSE log tail, action buttons.
- **Two-bank end-to-end integration test** runs `python -m rumble`
  against the docker Mumble server and exercises the dispatch flow.

### Known rough edges

- **Real radio audio is not yet relayed to Mumble.** Today the audio
  capture path feeds the DTMF detector only; the PCM samples don't get
  forwarded to `mumble.send_audio()`. Talking through the link
  end-to-end is milestone 7.
- **CW WAV ident is not on a timer yet.** `ident.wav_path` and
  `ident.interval_seconds` are accepted in the config but no scheduler
  fires them. Until then, ID with your voice as you always have.
- **PTT keying is by VOX or interface PTT.** Hardware/software PTT
  (CAT, GPIO, DTR/RTS) is on the roadmap; for now use your
  interface's PTT line or VOX.
- **A laptop's built-in microphone won't detect DTMF reliably.**
  Especially on modern Lenovos and similar where the input has heavy
  AGC + noise cancellation in firmware. This is expected. Use a USB
  sound card or a real radio interface; the test rigs all do.
- **The web UI has no auth.** Loopback-only by default; if you change
  `web.host`, treat the surface as fully unauthenticated.

### Not yet done

- Hardware PTT (CAT/DTR/GPIO).
- Real-time radio→Mumble audio passthrough.
- Mumble→radio audio passthrough with PTT keying.
- Scheduled CW ident.
- CSRF on web UI POST endpoints.
- Packaging (`pipx install rumble-py`, `apt install rumble-py`).
- Multi-node admin view.

## Roadmap

**Milestone 7 — Make it a real node.**

- Radio audio → Mumble audio passthrough (the missing half of the chain).
- Mumble audio → radio audio with proper PTT keying.
- Scheduled CW ident with WAV playback.
- Hardware PTT: serial DTR/RTS for radios with a COM port, GPIO for
  Raspberry Pi setups.
- Proper packaging so installation is one line.

**Beyond that, in no particular order.**

- CSRF tokens on web UI POST endpoints. Then optional HTTP basic auth
  or per-token auth for LAN exposure.
- Multi-node admin view (one web UI, multiple connected nodes).
- IRLP / EchoLink bridges — protocols are documented, just nobody's
  built the bridge yet. Open question whether the relevant directory
  services will allow a software-only node.
- A log/replay buffer of recent QSOs, with metadata search.
- Per-bank ident schedules (so a Field Day bank IDs every 5 minutes,
  a casual bank every 9).

## Comparison to the original C# Rumble

For hams who knew the [original C# project](https://github.com/kbennett2000/Rumble):

| | C# Rumble (2019-2022) | rumble-py (this) |
|---|---|---|
| Language | C# .NET Framework | Python 3.11+ |
| OS | Windows only | Linux primary, Windows secondary |
| Mumble interface | Controlled the Mumble desktop client via UI automation | Speaks the Mumble protocol directly via pymumble |
| DTMF detection | DtmfDetection library + WinForms | Goertzel + numpy |
| Config | CSV with one row per (server, channel) | YAML with banks, hierarchical |
| UI | WinForms | Web UI (browser) |
| Identifies as | Same operator | Same operator |
| Command grammar | `#*`, `#N*`, `#XX#Y*`, `#XXX#Y*` | **Same.** Operators don't have to relearn anything. |

The command grammar is **identical** to the C# version on purpose. Hams
who already learned the DTMF sequences for the old project don't have to
relearn anything. The implementations are different, but as far as the
radio operator is concerned, this is the same node.

## Troubleshooting

| Symptom | First thing to check |
|---|---|
| Mumble client says "certificate not trusted" | Self-signed cert from your own server. Accept on first-connect; see [Concepts](docs/CONCEPTS.md#authentication-and-certs). |
| Node connects but channel switches fail | Channel doesn't exist on the server, or path is wrong. Walk the tree in a Mumble desktop client and copy the exact path. |
| DTMF tones from radio don't register | Almost always audio level or device selection. Run `python scripts/listen_for_dtmf.py` to see the detector's view. Try `dtmf_min_magnitude: 0.02`. |
| Detected, but only sometimes | Either levels are marginal or the radio's send-DTMF duration is shorter than the detector's debounce. Have the operator hold the key longer. |
| TTS sounds robotic / slurred on Linux | That's espeak-ng. It's intentionally robotic-clear. SAPI on Windows sounds friendlier; both work over RF. |
| Web UI is on but pages don't update | HTMX or browser cache. Hard-refresh. Check `journalctl -u rumble -f` if running under systemd. |
| `ssl.wrap_socket` error on Python 3.12+ | Should be auto-patched by `mumble_client.py`. If you see it anyway, file an issue — see [CLAUDE.md](CLAUDE.md#known-workarounds). |
| Built-in laptop mic doesn't detect tones | Expected. See [hardware notes](docs/HARDWARE.md). Use a USB sound card. |
| Reconnect storms after dropping the LAN | Mumble auto-reconnect uses exponential backoff capped at `reconnect_max_backoff` (default 60 s). It's working as designed; check your network. |
| Bank switch (`#N*`) doesn't seem to do anything | Check the web UI — the active bank field should update immediately. If it does but `#001#1*` still goes to the old place, the new bank doesn't have a mapping for `001/1`. Add one or pick a different code. |
| `python -m rumble` exits with code 2 immediately | Config error — the loader rejects malformed YAML or failed validation rules. The error message names the offending field; see [docs/CONFIGURATION.md](docs/CONFIGURATION.md#validation-rules-summary). |
| The web UI loads but the log panel is empty | The SSE stream needs a Mumble or radio event to populate it. Click a DTMF keypad button or change banks; lines should appear. |
| `espeak-ng` not found on Linux | `sudo apt install espeak-ng`. On other distros: `sudo dnf install espeak-ng`, `sudo pacman -S espeak-ng`, etc. |
| Random pops or clicks in transmitted audio | Sample-rate mismatch between OS and interface, or a USB hub starving the SignaLink. Plug the SignaLink directly into the PC, not through a hub. |

For radio-side issues, [docs/HARDWARE.md](docs/HARDWARE.md) has a "Common
gotchas" section that covers RF on the audio cable, ground loops, PTT
stuck on, and one-way audio.

## Documentation

| File | What's in it |
|---|---|
| [README.md](README.md) | This document. Front door. |
| [docs/CHEAT_SHEET.md](docs/CHEAT_SHEET.md) | One-page printable operating reference card for the shack. |
| [docs/CONCEPTS.md](docs/CONCEPTS.md) | What Mumble is, channels, certs, comparison to IRLP / EchoLink / AllStar / HHotline. For hams new to VoIP. |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Radios, sound-card interfaces, audio levels, PTT options. Practical, not theoretical. |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every field in `config.yaml`, with validation rules and a fully-annotated example. |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Running rumble-py as a 24/7 systemd service on a Raspberry Pi. |
| [docs/issues-to-file.md](docs/issues-to-file.md) | In-tree notes for issues to file once the repo has active issue tracking. |
| [CLAUDE.md](CLAUDE.md) | Project conventions, voice, known workarounds. Background for collaborators. |

## Contributing

PRs welcome. We use [Conventional Commits](https://www.conventionalcommits.org/),
black for formatting (line length 100), ruff for linting, pytest for tests.
Run all three locally before sending — there's no CI to catch what you don't:

```bash
black src tests
ruff check src tests
pytest                                   # unit suite
RUMBLE_INTEGRATION=1 pytest              # with docker Mumble server running
```

The [CLAUDE.md](CLAUDE.md) file describes the project conventions in more
detail, including how to write code comments and where the type-hint
boundaries are.

## Prior art and acknowledgments

This project stands on a lot of other people's work.

- **[Mumble](https://www.mumble.info/)** by the Mumble developers —
  the protocol, the reference server (Murmur), the entire reason this
  was possible without inventing a VOIP stack.
- **[pymumble](https://github.com/azlux/pymumble)** by Azlux — the
  Python client library that lets us speak Mumble directly. Without
  pymumble, this project would have to drive the Mumble desktop client
  via UI automation (which is exactly what the C# original did, and
  exactly what we wanted to escape).
- **[IRLP](https://www.irlp.net/)** by Dave Cameron VE7LTD — set the
  conceptual template for radio linking thirty years ago. Most of the
  mental model in this project is unapologetically borrowed.
- **[EchoLink](https://www.echolink.org/)** by Jonathan Taylor K1RFD
  — brought VOIP linking to a broader audience and proved the
  desktop-client use case.
- **[AllStar Link](https://allstarlink.org/)** — showed that
  Asterisk could do real ham linking and built a directory of
  thousands of nodes.
- **[Hamshack Hotline](https://hamshackhotline.com/)** — the SIP-on-
  IP-phone community proved that VOIP-for-hams could be fun and
  approachable.
- **The DTMF detection literature** that goes back to Goertzel's 1958
  paper. We're using the same algorithm AT&T's switches used 60 years
  ago, in Python.

If any of those projects sound interesting on their own, they
absolutely are. Rumble-py is one point in a much larger design space.

## License

MIT — see [LICENSE](LICENSE).

## Author

**Kris Bennett — AE9S.** Original C# Rumble (2019-2022) and this Python
rewrite. [GitHub: kbennett2000](https://github.com/kbennett2000)

---

**73!**
