# Configuration Reference

Every field in `config.yaml`, what it means, and what the loader will
reject if you get it wrong.

If you're just trying to get up and running, copy
[`config.example.yaml`](../config.example.yaml) and skim the inline
comments — the example file is the friendliest version of this
reference. This document is the precise one.

## Where the config lives

Wherever you point `--config` at:

```bash
python -m rumble --config /etc/rumble/config.yaml
python -m rumble --config config.dev.yaml
```

Convention in this repo:

- `config.example.yaml` — checked in, never read by a running node.
- `config.yaml` — gitignored, intended for production.
- `config.dev.yaml` — gitignored, intended for local development.

The `.gitignore` excludes `config.yaml`, `config.yml`, and the
`config.*.yaml` / `config.*.yml` patterns. The example is allowlisted.

## Top-level keys

| Key | Type | Required? | What it controls |
|---|---|---|---|
| [`callsign`](#callsign) | string | **yes** | Your operator callsign. Used in TTS announcements. |
| [`initial_bank`](#initial_bank) | int | no (default `0`) | Which bank to load at startup. |
| [`banks`](#banks) | dict[int, Bank] | **yes** | Sets of servers + channel mappings the node can switch between. |
| [`audio`](#audio) | object | no | Sound-card and DTMF detection settings. |
| [`ident`](#ident) | object | no | Periodic station identification. |
| [`web`](#web) | object | no | Built-in web UI settings. |

The order in the file doesn't matter; YAML is order-insensitive.

---

## `callsign`

```yaml
callsign: "AE9S"
```

**Required.** Your operator callsign, exactly as you'd ID with it on the
air. Used as:

- The TTS startup announcement (*"AE9S rumble-py listening"*).
- The default Mumble username if a server entry doesn't override it.
- The station-ID prefix on logged messages and the web UI.

**Validation:** must be present and non-empty.

## `initial_bank`

```yaml
initial_bank: 0
```

Which bank from the `banks:` map to load when the node starts up.
Default is `0`. Must match one of the keys under `banks:`; otherwise
the loader rejects the config.

Operators can switch to a different bank live with the `#N*` DTMF
command — see [`CHEAT_SHEET.md`](CHEAT_SHEET.md).

## `banks`

```yaml
banks:
  0:
    servers: [...]
    channels: [...]
  1:
    servers: [...]
    channels: [...]
```

**Required**, non-empty. A dict of integer bank numbers to bank
definitions. Each bank is a fully independent set of Mumble servers and
channel mappings; switching banks at runtime swaps the whole map at
once.

Use cases for multiple banks:

- Different nets: morning weather net (bank 0), evening rag-chew
  (bank 1), Field Day (bank 2), emergency comms (bank 9).
- Staging vs. production: bank 0 points at your local dev server,
  bank 1 at the real one.
- Different channel layouts: same servers, different DTMF code → path
  mappings, so you can present a simpler keypad layout during a net
  and a more elaborate one for casual use.

The integer keys do **not** have to be contiguous. `banks: {0, 1, 9}`
is fine. They must be parseable as integers (so `"3"` works but `"three"`
doesn't).

### `servers` (within a bank)

```yaml
banks:
  0:
    servers:
      - name: "local-dev"
        host: "127.0.0.1"
        port: 64738
        username: "AE9S"
        password: null
        certfile: null
        keyfile: null
```

A list of Mumble servers this bank knows about. Channel mappings (see
below) reference a server by its `name`.

| Field | Type | Default | What it does |
|---|---|---|---|
| `name` | string | required | Short label used in channel mappings (e.g. `"local-dev"`). Must be unique within the bank. |
| `host` | string | required | Hostname or IP. `127.0.0.1` for a local server, `mumble.example.org` for a remote one. |
| `port` | int | `64738` | TCP/UDP port. Mumble's standard. |
| `username` | string | `"rumble-py"` | Username the node identifies as. Override per-server if needed. |
| `password` | string | `null` | Server password. Leave null for open servers. |
| `certfile` | string | `null` | Path to a client TLS cert (PEM). Optional. See [Concepts: certs](CONCEPTS.md#authentication-and-certs). |
| `keyfile` | string | `null` | Path to the client key (PEM). Required if `certfile` is set. |

**Validation:**

- `name` is required and must be unique within the bank's `servers` list.
- `host` is required.
- `port` defaults to 64738 if omitted.

### `channels` (within a bank)

```yaml
banks:
  0:
    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root"
        nickname: "local lobby"
```

A list of channel mappings. Each one wires a DTMF command (the
`#XXX#Y*` form) to a specific channel on one of the bank's servers,
with a friendly name the node speaks over TTS when joining.

| Field | Type | Default | What it does |
|---|---|---|---|
| `server_number` | string | required | The `XXX` of the `#XXX#Y*` command. Exactly **3 digits**. Leading zeros required. |
| `channel_number` | string | required | The `Y` of the `#XXX#Y*` command. Exactly **1 digit**. |
| `server_ref` | string | required | The `name` of a server in this bank's `servers:` list. |
| `channel_path` | string | required | Mumble channel path, e.g. `Root/Repeaters/W0XYZ`. See [channel paths](CONCEPTS.md#channels-and-channel-paths). |
| `nickname` | string | required | Spoken via TTS when joining (*"switched to local lobby"*). Keep it short and pronounceable. |

> **Quote your digits.** YAML interprets unquoted `001` as the integer
> `1`. The loader detects this and re-pads back to three digits, but
> you'll save yourself a debugging session by writing `"001"` with
> quotes. The example config does this consistently.

**Validation:**

- `server_number` must coerce to exactly 3 digits. `001` (int) is
  accepted and re-padded. `1234` is rejected. Letters are rejected.
- `channel_number` must coerce to exactly 1 digit (0-9).
- `server_ref` must match a `name` of a server in the same bank.
- `(server_number, channel_number)` pairs must be unique within a bank.
  Two mappings with `001/1` is a config error.
- All four other string fields are required and non-empty.

## `audio`

```yaml
audio:
  input_device: null
  output_device: null
  sample_rate: 8000
  dtmf_min_magnitude: 0.05
```

Sound-card settings for the DTMF detector and (in milestone 7) the
relayed audio.

| Field | Type | Default | What it does |
|---|---|---|---|
| `input_device` | string \| null | `null` | Sound device to listen on. `null` or `"default"` means system default. A non-empty string is matched as a **substring** of device names: `"SignaLink"` will find a SignaLink USB. |
| `output_device` | string \| null | `null` | Same idea, for the TX side. |
| `sample_rate` | int | `8000` | Sampling rate for capture and DTMF detection. 8 kHz is plenty for DTMF and matches narrowband FM. Don't change unless you know why. |
| `dtmf_min_magnitude` | float | `0.05` | Goertzel-magnitude threshold for "tone present." Lower is more sensitive (detects fainter tones, more false positives). Higher is stricter. See [setting audio levels](HARDWARE.md#setting-audio-levels). |

To list available devices on your system:

```bash
python -m rumble --list-audio-devices
```

That prints a numbered list with names; copy the substring you want into
`input_device`. For radio-side hardware specifics, see
[`HARDWARE.md`](HARDWARE.md).

## `ident`

```yaml
ident:
  wav_path: "./ident.wav"
  interval_seconds: 540
```

Periodic station identification.

| Field | Type | Default | What it does |
|---|---|---|---|
| `wav_path` | string \| null | `null` | Path to your CW or voice ident WAV file. Relative paths are relative to the rumble-py working directory. |
| `interval_seconds` | int | `600` | How often to play the ident, in seconds. 600 = 10 minutes (the FCC §97.119 maximum); 540 = 9 minutes (recommended for margin). |

> **§97.119 reminder (U.S.):** you must identify your station at least
> every 10 minutes during a communication and at the end of the
> communication. Operators elsewhere: your equivalent rules apply.
>
> **Status note:** scheduled ident playback is milestone 7 work. Today
> these fields are accepted but no scheduler fires the WAV. Until then,
> ID with your voice as you always have. See the
> [Project status section of the README](../README.md#project-status).

## `web`

```yaml
web:
  enabled: true
  host: "127.0.0.1"
  port: 8080
```

The built-in web UI for status and control. See [the web UI section of
the README](../README.md#the-web-ui).

| Field | Type | Default | What it does |
|---|---|---|---|
| `enabled` | bool | `true` | Whether to start the web UI at all. Set `false` to skip it (handy for headless nodes that don't need a UI). |
| `host` | string | `"127.0.0.1"` | Address to bind. `127.0.0.1` = loopback only (default). `0.0.0.0` = all interfaces. |
| `port` | int | `8080` | TCP port. Pick anything; nothing standard uses 8080 in this context. |

> **Security warning, repeated because it matters.** The web UI has no
> authentication. Setting `web.host: 0.0.0.0` makes the UI reachable
> from anyone who can reach the node's port. **Do this only on a
> trusted LAN**, and don't ever expose it to the public internet until
> CSRF + auth land in a later milestone (see
> [`issues-to-file.md`](issues-to-file.md)).

## Validation rules summary

The loader enforces these and raises `ConfigError` with a useful message
if anything's off:

1. `callsign` is required and non-empty.
2. `banks` is required and non-empty.
3. `initial_bank` must match one of the keys under `banks`.
4. Each bank's `servers` list must have unique `name` values.
5. Every `channel.server_ref` must point at a server in the same bank.
6. Each bank's `(server_number, channel_number)` pairs must be unique.
7. `server_number` must be exactly 3 digits after normalization.
8. `channel_number` must be exactly 1 digit after normalization.

Validation runs at startup. If anything fails, `python -m rumble` exits
with code 2 and prints the offending field. The web UI's *Reload config*
button runs the same validation; on failure, the running config is
unchanged and a red banner appears in the UI.

## A complete annotated example

This is `config.example.yaml`, reproduced here for the convenience of
operators who'd rather not flip back and forth. **If this drifts from
the file on disk, the file on disk is canonical.**

```yaml
# =============================================================================
# rumble-py — example configuration
# =============================================================================
#
# Copy to `config.dev.yaml` (gitignored) or `config.yaml` and edit. Run with:
#
#     python -m rumble --config config.dev.yaml
#
# Layout notes:
#
# * Audio, ident, and web settings are shared across all banks (they describe
#   *this* node, not a particular destination).
# * Each bank is a fully-independent set of Mumble servers + DTMF channel
#   mappings. The DTMF command `#N**` (where N is 0-9) swaps to bank N at
#   runtime without restarting the program.
# * Channel mappings reference servers by `name`, so the same channel-map
#   shape can be reused for staging vs. production by editing only the
#   server entries.
# =============================================================================


# ---------------------------------------------------------------------------
# Operator identity
# ---------------------------------------------------------------------------
# Required. Your amateur radio callsign — spoken in TTS announcements and
# used as the default Mumble username if a server entry doesn't override it.
callsign: "AE9S"

# Bank loaded at program start. Must be one of the keys under `banks:` below.
initial_bank: 0


# ---------------------------------------------------------------------------
# Audio I/O (shared across banks)
# ---------------------------------------------------------------------------
audio:
  # Use `null` (or omit) for system default. A string is treated as a
  # substring match against sounddevice device names. Run
  # `python -m rumble --list-audio-devices` to see what's available.
  input_device: null
  output_device: null

  # Sample rate the radio audio is captured at. 8 kHz is plenty for DTMF and
  # matches narrowband VOIP, so no resampling is needed.
  sample_rate: 8000

  # Minimum Goertzel magnitude (after N-normalization) to count a tone as
  # present. 0.05 catches half-scale signals comfortably; lower it if your
  # line-level input is quiet, raise it if you're seeing false detections.
  dtmf_min_magnitude: 0.05


# ---------------------------------------------------------------------------
# Station identification (shared across banks)
# ---------------------------------------------------------------------------
# Per FCC §97.119, you must ID at least every 10 minutes while transmitting.
ident:
  wav_path: null              # path to a pre-recorded CW or voice WAV
  interval_seconds: 540       # 9 minutes — comfortably under the 10-minute rule


# ---------------------------------------------------------------------------
# Local web UI (shared across banks)
# ---------------------------------------------------------------------------
web:
  enabled: true
  host: "127.0.0.1"           # bind only to localhost unless you really know
  port: 8080


# ---------------------------------------------------------------------------
# Banks
# ---------------------------------------------------------------------------
# Each bank is a snapshot of (servers + DTMF→channel map). The DTMF command
# `#N**` swaps the active bank to N. To switch banks back, type `#M**`.
banks:

  # ---- Bank 0: default / local development -----------------------------------
  0:
    servers:
      # Each server gets a short `name` that channel mappings reference.
      - name: "local-dev"
        host: "127.0.0.1"
        port: 64738
        username: "AE9S"
        # password: ""           # optional; omit for open servers
        # certfile: "./client.pem"  # optional; for cert auth
        # keyfile: "./client.key"

    channels:
      # DTMF "#001#1*" → connect to server "local-dev", channel "Root", and
      # TTS-announce "local lobby".
      #
      # server_number and channel_number are kept as STRINGS so leading zeros
      # are preserved — quote them, or the YAML parser may swallow the zero.
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root"
        nickname: "local lobby"

  # ---- Bank 1: example production --------------------------------------------
  1:
    servers:
      - name: "example-repeater-net"
        host: "mumble.example.org"
        port: 64738
        username: "AE9S"

    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "example-repeater-net"
        channel_path: "Root/Repeaters/Midwest"
        nickname: "Midwest"
      - server_number: "001"
        channel_number: "2"
        server_ref: "example-repeater-net"
        channel_path: "Root/Repeaters/Mountain"
        nickname: "Mountain"
```

## Reloading config

You don't have to restart the node to change banks or to swap the file
on disk for a new one:

- **Live bank switch** — `#N*` from the radio side (or click the bank
  dropdown in the web UI). Instant.
- **Reload from disk** — `POST /actions/reload-config` (the *Reload
  config* button in the web UI). Re-reads the file, validates, and
  swaps in if valid. The active bank must still exist in the new file;
  if it doesn't, the reload is refused and the old config stays.

For anything that requires more than a config swap — changing the
`web.host`, changing `audio.input_device`, etc. — restart the node.

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the systemd-based restart
recipe.
