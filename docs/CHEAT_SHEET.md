# rumble-py — Operating Reference Card

**Operator:** `<YOUR CALLSIGN>` &nbsp;&nbsp; **Node version:** v0.x &nbsp;&nbsp; **Date:** ______________

---

## Command syntax

| Sequence | Meaning |
|---|---|
| `#*` | Disconnect — move to Root |
| `#N*` | Load configuration bank N (N = 0–9) |
| `#XX#Y*` | Admin setting XX → value Y |
| `#XXX#Y*` | Switch to server XXX, channel Y (per channel map) |

## Examples

| Send | Result |
|---|---|
| `#001#1*` | Switch to server 001, channel 1 |
| `#001#2*` | Switch to server 001, channel 2 |
| `#00#0*` | Mute (sticky — stays muted) |
| `#00#1*` | Unmute (clears sticky) |
| `#*` | Disconnect to Root |

## Admin settings

| Code | Action | Status |
|---|---|---|
| `00 / 0` | Sticky mute on | implemented |
| `00 / 1` | Sticky mute off (unmute) | implemented |
| `01 / 0` | Self-deafen | reserved (milestone 7) |
| `01 / 1` | Un-deafen | reserved (milestone 7) |
| `03 / 0` | Speak current status over TTS | reserved (milestone 7) |
| other | Reserved for future use | reserved |

## Notes

- Any invalid tone aborts the command in progress. Start over with a fresh `#`.
- Wait for the TTS confirmation before sending the next command.
- Sticky mute (`#00#0*`) survives across other commands; clear it with `#00#1*`.
- The node auto-mutes while you're keying a command so the tones don't relay.
- Bank switch (`#N*`) is instant; the next `#XXX#Y*` uses the new bank's map.

## Station ID

Per FCC **§97.119**, you must identify your station at least every 10 minutes
while transmitting and at the end of a communication. Operators outside the
U.S.: your equivalent rules apply.

Rumble-py plays the CW WAV at `ident.wav_path` every `ident.interval_seconds`
(default **600**, recommend **540** for margin). Scheduled playback lands in
milestone 7 — until then, ID with your voice.

## Web UI

`http://<node-ip>:8080/` — status, on-screen keypad, log tail, bank switch,
mute/disconnect, config reload. Loopback only by default.

## Help

**GitHub:** github.com/kbennett2000/rumble-py — issues welcome.
**Docs:** see [`README.md`](../README.md), [`CONCEPTS.md`](CONCEPTS.md),
[`HARDWARE.md`](HARDWARE.md), [`CONFIGURATION.md`](CONFIGURATION.md),
[`DEPLOYMENT.md`](DEPLOYMENT.md).
