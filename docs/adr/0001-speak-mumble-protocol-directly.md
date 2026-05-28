# 0001. Speak the Mumble protocol directly via pymumble

Date: 2026-05-28
Status: Accepted

## Context

The original Rumble application, written in C# for Windows, controlled the
Mumble desktop client through UI automation — it launched the GUI, manipulated
its window, and relied on the Mumble desktop client to do all protocol and
audio work. That approach locked the software to Windows, required a graphical
session even on headless servers, and made it brittle against any Mumble GUI
update.

rumble-py is a ground-up Python rewrite. The target deployments are headless
Linux systems — typically a Raspberry Pi bolted to a shelf in a shack, running
without a monitor. A GUI dependency would be a non-starter for that use case.

## Decision

rumble-py speaks the Mumble protocol directly using the pymumble library
(`src/rumble/mumble_client.py`). It does not launch, shell out to, or otherwise
control the Mumble desktop client. `MumbleClient` is the single point of
contact with pymumble; everything else in the project talks to Mumble through
its public API.

## Alternatives considered

- **Shell out to the Mumble desktop client (as the C# version did)** — requires
  a GUI session, is Windows-specific in practice, brittle against GUI changes,
  and cannot be reliably tested without a display. Rejected for all of those
  reasons.

- **Shell out to murmur-record / headless Mumble CLI tools** — a handful of
  unmaintained projects exist, but none expose the full event and audio stream
  needed here. Audio routing through a subprocess boundary adds latency and
  makes PTT control significantly harder. Rejected as too fragile.

- **Implement the Mumble protocol from scratch** — the Mumble protocol is
  well-documented and uses Protobuf, so this is feasible. But pymumble already
  handles the protocol state machine, TLS negotiation, Opus audio encoding, and
  the threading model. Reinventing that buys us nothing while costing
  significant development time. Rejected.

## Consequences

What we gained:

- No GUI dependency. The application runs headless, under systemd, with no
  display server needed.
- Cross-platform: Linux, Windows, and in principle any OS that can run Python
  and PortAudio.
- We own the complete audio pipeline — sounddevice in, DTMF detector, pymumble
  out. No subprocess boundary, no audio routing tricks.

What we accepted:

- We own the reconnect loop, the mute/deaf state caching, and all event
  plumbing that the GUI would have handled implicitly. This is non-trivial code
  (see `mumble_client.py`, especially `_reconnect_loop` and
  `_on_unexpected_disconnect`).
- pymumble's threading model imposes constraints on our callbacks (they run on
  pymumble's internal thread and must not block — see the module docstring in
  `mumble_client.py`).
- If pymumble is abandoned or falls too far behind the Mumble protocol spec, we
  would need to either fork it or rewrite this layer.

## Revisit if

- pymumble stops maintaining compatibility with the current Mumble protocol
  version and the maintainer does not respond to issues within a reasonable
  timeframe. At that point, evaluate replacing pymumble with a fresh
  protocol implementation or a different library.
- The project needs to target a Mumble protocol version that pymumble does not
  support (e.g., an as-yet-unimplemented voice activity feature).
