# 0003. Store all config banks in a single YAML file

Date: 2026-05-28
Status: Accepted

## Context

rumble-py supports multiple configuration "banks." An operator who works
multiple nets — say, a weekly 80-meter ragchew and a county ARES group — can
define each as a numbered bank and switch between them at runtime by sending a
DTMF `#N*` sequence from the radio. The question was how to lay that out on
disk.

The earlier specification described a flat YAML shape where only one bank's
servers and channels lived in the file at a time. That implied a file-per-bank
scheme or a reload-from-disk on every `LoadConfig` command. Either way, it
raised questions about partial loads, concurrent writes from the web UI, and
operator error (misnamed files, mismatched numbers).

## Decision

A single YAML file holds all banks under a top-level `banks:` dict, keyed by
integer bank number. Audio, ident, and web settings are shared across all banks
at the same level; only `servers` and `channels` differ between banks. The
loader parses the entire file once and returns a `RumbleConfig` whose `banks`
field is a `dict[int, Bank]`. Switching banks at runtime (`LoadConfig(bank=N)`)
is an in-memory dict lookup with no disk I/O.

```yaml
callsign: AE9S
initial_bank: 0

banks:
  0:
    servers: [...]
    channels: [...]
  1:
    servers: [...]
    channels: [...]

audio:
  sample_rate: 8000
ident:
  interval_seconds: 600
web:
  host: 127.0.0.1
```

## Alternatives considered

- **One file per bank (`config_0.yaml`, `config_1.yaml`, ...)** — each
  `LoadConfig(bank=N)` call re-reads from disk and reloads the parser. Simple
  to implement, but introduces race conditions if the operator edits a file
  between a bank switch and the next DTMF command. It also requires the
  operator to keep file names in sync with bank numbers, which is an easy
  mistake to make. Rejected.

- **Flat single file, one bank active at a time (original spec shape)** —
  the file contains one bank's data and `LoadConfig` replaces it with a
  different file. Same disk I/O and race concerns as above, plus it makes
  the web UI's "reload config" action ambiguous (reload which bank?).
  Rejected.

## Consequences

What we gained:

- `LoadConfig(bank=N)` is an in-memory swap. No disk reads, no parse errors,
  no partial-load window.
- The web UI's "reload config" button re-reads the single file and atomically
  replaces the in-memory `RumbleConfig`. All banks update at once.
- Configuration for all banks is visible in one place, making it easier to
  spot cross-bank inconsistencies (e.g., the same server appearing under
  different names in two banks).
- Strict validation runs at load time across all banks simultaneously, not
  one-at-a-time as banks are switched.

What we accepted:

- The file grows proportionally with the number of banks. For typical amateur
  radio use (2-5 banks, a handful of servers each) this is not a problem, but
  it is worth noting.
- Audio, ident, and web settings cannot differ between banks. If an operator
  ever needs per-bank audio device selection (e.g., a different USB interface
  for a different radio), that would require redesigning this.

## Revisit if

- An operator legitimately needs different audio settings per bank (different
  input device, different sample rate). At that point, consider moving
  `AudioConfig` inside `Bank` rather than at the root level.
- The config file grows large enough (many banks, many servers) that a single
  YAML file becomes difficult to maintain. A directory-per-bank layout with
  a root `config.yaml` importing them would be worth evaluating.
