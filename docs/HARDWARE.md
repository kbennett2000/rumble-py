# Hardware: Radios, Interfaces, and Audio Levels

You know radios. You probably know how a sound-card interface works in
principle. This doc is the practical guide to wiring one to rumble-py:
which interfaces play well, what the cables look like, what the audio
levels should be, and the specific traps to avoid.

## The basic chain

```
   ┌───────────┐     ┌─────────────────┐     ┌───────────────────┐
   │  RADIO    │     │  INTERFACE      │     │  COMPUTER         │
   │           │     │  (sound card +  │     │  running          │
   │  speaker──┼────►│  isolation +    │────►│  rumble-py        │
   │   (RX)    │     │  PTT switch)    │ USB │                   │
   │           │     │                 │     │                   │
   │  mic ────┼◄─────┤                 │◄────┤                   │
   │   (TX)    │     │                 │     │                   │
   │           │     │                 │     │                   │
   │  PTT ────┼◄─────┤ (VOX or wired)  │     │                   │
   └───────────┘     └─────────────────┘     └───────────────────┘
```

Three signals cross between the radio and the interface:

- **RX audio** — the radio's received audio coming out of its speaker
  jack, into the interface's input. The interface's USB sound card
  presents this to the PC as a microphone-input stream. Rumble-py
  decodes DTMF tones from it.
- **TX audio** — audio from the PC going out through the interface,
  out to the radio's microphone-input jack. The radio modulates
  whatever audio it sees there onto its transmitter. (In rumble-py
  today, this carries TTS announcements; in milestone 7 it'll carry
  the relayed Mumble audio.)
- **PTT** — a switched contact that grounds the radio's PTT line to
  key the transmitter. The interface either generates this from
  detecting audio on the TX side (VOX) or via a hardware control signal
  from the PC (e.g., a CM108-based GPIO line, or a CAT/serial DTR pin).

Most interfaces also provide **transformer isolation** between the
radio and the PC's USB ground. Without that you'll get hum, RF on the
audio, or worse.

## Recommended interfaces

The four interfaces we know operators are using, with short pros/cons.

### SignaLink USB (~$130 from Tigertronics)

The most popular ham digital-mode interface for a reason. Transformer-
isolated audio, jumper-selectable PTT (VOX-style detection from your TX
audio is the default, or hardwire it for radios that need it), all the
common radio cables sold separately, dead reliable. Lives on the desk
for a decade. **This is what the project author tests with.** If you
want to stop reading and just buy something, buy this.

- Pros: rock solid, isolation done right, jumpers handle weird radios,
  big community.
- Cons: priciest of the four, somewhat bulky for a "permanent install
  on a Pi" deployment, single-purpose (no CAT control).
- Cable for Baofeng / Wouxun / TYT and similar: **SLCAB-6PK** (or
  SLCAB-2 / SLCAB-6, depending on connector layout — see Tigertronics'
  cable selector).

### DigiRig Mobile (~$60)

The compact upstart. USB-C, smaller than a deck of cards, built-in CAT
serial for radios that have a TTL-level data port. Growing fast in the
SOTA / portable community. Some users report fewer level adjustments
than the SignaLink; others report needing more attention to grounding.

- Pros: cheap, compact, has CAT for supported radios, single USB cable.
- Cons: newer, smaller community, no transformer isolation on all
  models (check current product page), cable kits sold separately.

### RIM-Lite / RIM-Mini (RT Systems)

Simple USB sound-card interface with bare TX/RX wiring, sold per-radio
with the right cable already installed. Good for "I just want it to
work, no jumpers, no soldering."

- Pros: turnkey, per-radio, no configuration.
- Cons: less flexibility than a SignaLink, harder to repurpose for a
  different radio.

### RA-35 / RA-50 (Masters Communications)

Repeater Builder community favorite. Built for permanent-install
linking applications. Various opto-isolated PTT options, robust
construction.

- Pros: built for 24/7 linking, durable.
- Cons: harder to source, more complex initial setup.

### Homebrew (~$15 in parts if you're patient)

A transformer-isolated audio interface plus an opto-isolated PTT
plus a USB sound card is roughly $15 in parts. Lots of designs floating
around the QRZ and Repeater Builder forums. Not recommended for a first
build — buy a SignaLink and get on the air. Recommended if you've
already wired your own digital-mode interfaces before; the principles
are identical.

## The combination this project tests with

**SignaLink USB + Baofeng UV-5R / UV-82.**

Cheap, ubiquitous, and the radios are the platonic ideal of "DTMF-
capable HT that any new ham can afford." The author has a stack of UV-5Rs
on the bench and a SignaLink at every spot the bench-test rig moves to.

### Wiring

The UV-5R / UV-82 use a **Kenwood-style 2-pin** connector: a 3.5mm TRS
for the speaker/PTT side and a 2.5mm TS for the microphone side, in a
single cable assembly. The SignaLink cable for this is **SLCAB-6PK**
(double-check current Tigertronics part numbers — they're sometimes
re-numbered).

That cable goes:

- Radio's speaker / PTT plug (3.5mm TRS) → SignaLink "SPK / PTT"
- Radio's mic plug (2.5mm TS) → SignaLink "MIC"

Then USB from SignaLink to the PC.

### Jumpers inside the SignaLink

The SignaLink has a small jumper block (JP1 — JP4ish, depending on
revision) that selects which pins on the radio cable carry which signal.
**The exact jumper map for the UV-5R / UV-82 is documented in the
SignaLink Quick Start Guide that ships in the box** — Tigertronics
ships radio-specific jumper diagrams, and they update them when radios
change connector pinouts. Don't trust a forum post; trust the printed
guide.

The choice that matters most:

- **VOX PTT (jumper to VOX position):** SignaLink keys PTT when it
  detects audio coming from the PC. Simplest. Works fine for rumble-py
  today since TTS audio triggers it.
- **Hard-wired PTT (jumper to PTT position):** SignaLink keys PTT via
  a hardware control line. Cleaner but requires the radio-specific
  cable to carry that line correctly.

For first power-on with rumble-py we recommend **VOX**. If you get
trailing audio you don't want, or chopped transmissions, switch to
hard-wired PTT then.

### Audio level knobs

The SignaLink has two knobs on the front: **TX** (audio going to the
radio) and **RX** (audio coming from the radio). Both start at 12
o'clock; we recommend backing them off to **10 o'clock** as a starting
point and adjusting by ear.

See [Setting audio levels](#setting-audio-levels) below for the actual
procedure.

## Setting audio levels

There are two sides to get right. Both can be wrong independently.

### RX side: radio → PC

The DTMF detector wants audio that's:

- **Loud enough** that real DTMF tones produce a Goertzel magnitude
  above the `dtmf_min_magnitude` threshold (default `0.05`, see
  [`CONFIGURATION.md`](CONFIGURATION.md#audio)). If the level's too
  low, you'll see operators key DTMF and rumble-py just ignore it.
- **Quiet enough** that voice audio doesn't clip the sound card's
  input. Clipped voice doesn't break DTMF (the tones are still in
  there) but it sounds awful on the other end of the link once
  passthrough audio is in.

**Procedure** (no radio passthrough yet, so we're just tuning DTMF):

1. Start with the SignaLink RX knob at 10 o'clock.
2. Run `python scripts/listen_for_dtmf.py` and pick the SignaLink
   sound device when prompted.
3. Key DTMF tones from another radio (or your radio in another mode,
   or use the DTMF generator on your phone). Speak normally between
   tones.
4. Watch the script's output. You want every clean DTMF press to print
   a `("start", char)` line. If some get missed, **turn the RX knob
   up** by about an hour position and try again.
5. Once you're catching every press, key voice into the radio. Don't
   shout, just normal voice. If the sound card is clipping you'll
   hear it in the OS's level meter (Linux: `pavucontrol` or
   `pamix`; Windows: the sound control panel). **Turn RX down** until
   normal voice peaks around -6 dBFS.

You're aiming for the audio level where DTMF tones detect reliably AND
voice audio fits in the available headroom. There's usually a comfort
zone of 2-3 hours of knob travel where both work; settle in the middle.

### TX side: PC → radio

The TTS audio (and, in milestone 7, the relayed Mumble audio) wants to
fully modulate the transmitter. For narrowband FM that's roughly **±2.5
kHz deviation**; for wide FM (the old 25 kHz channels) it's **±5 kHz**.
Most HTs default to narrowband.

**Procedure:**

1. Start with the SignaLink TX knob at 10 o'clock and the radio's TX
   audio gain (if it has one) at default.
2. With your node connected to the dev Mumble server, trigger a TTS
   announcement — the `rumble-py listening` startup message, or a
   bank switch (`#1**`).
3. Monitor the transmitted audio on a second receiver. (A second HT
   on the same frequency, an SDR like an RTL-SDR running SDR# or
   `gqrx`, anything that can demodulate FM.)
4. The TTS should sound natural — not faint, not distorted. If it's
   faint, turn TX up. If it's distorting or fuzzy, turn TX down.
5. Optional and recommended: borrow a deviation meter or use the
   waterfall display on an SDR to confirm you're within ±2.5 kHz on
   narrowband.

> If you can't easily monitor the TX audio, ask a buddy down the road
> or another club member. "Hey, listen to my node on 446.000 simplex
> and tell me how the TTS sounds" gets you a useful answer fast.

## PTT keying — three options

### VOX (radio's or interface's)

The simplest. Either the radio's own VOX detects audio from the
microphone input, or the SignaLink's VOX detects audio on the USB sound
card output. Either way, no extra wiring.

- **Pro:** zero setup.
- **Con:** 200-500 ms of "leading" silence is needed for VOX to engage;
  some VOX configurations also have 200-500 ms of "trailing" audio
  before they release. Slow, but tolerable.
- **Con:** spurious audio (noise, room sounds picked up by a bad
  cable) can spuriously key the transmitter.

### Hardware PTT via interface

SignaLink with the PTT jumper in the hardwired position will key
PTT itself based on whether audio is present on the USB output. No PC
software involvement. Cleaner than VOX, no trailing audio, deterministic.

- **Pro:** clean keying, no VOX delay.
- **Con:** requires the right cable, sometimes a soldering iron, and
  the right SignaLink jumper position.

### Software PTT (CAT / DTR / RTS / GPIO)

The radio is keyed by an explicit signal from the PC. CAT-capable radios
accept a serial command. Many sound-card interfaces let the PC pull
DTR or RTS to ground a PTT line. On a Raspberry Pi, you can dedicate a
GPIO pin to PTT.

- **Pro:** cleanest of all — only keys when the software wants.
- **Con:** **Not implemented in rumble-py yet.** This is milestone 7
  work. Until then, use VOX or interface-based PTT.

## Radio settings

A short list of things to set on the radio side:

- **VOX off** — let the interface or the audio handle keying. If both
  the radio's VOX and the interface's are active you'll get
  unpredictable behavior.
- **Squelch on, set high enough to silence noise** — you do **not**
  want open-squelch audio constantly flooding the DTMF detector. The
  detector's reasonably noise-tolerant for tone presence, but it
  shouldn't have to be.
- **Narrow vs. wide FM** — match your local repeater (most are 12.5
  kHz narrow these days). For simplex, narrow is fine and recommended.
- **No compander / no scrambler** — these mangle audio in ways that
  break both DTMF and codec assumptions.
- **No TOT** (or set very long) — if your radio times out the
  transmitter at 60 seconds, you'll cut off long QSOs.
- **DTMF tone duration** — most radios let you set this. **150-200 ms
  per tone** is plenty for the rumble-py detector. If you want
  bulletproof recognition, **250 ms** is better still.

## Common gotchas

| Symptom | Likely cause |
|---|---|
| Buzzing hum on transmitted audio | Ground loop. The SignaLink's transformer isolation handles this; if you don't have transformer isolation, add some. Or move the PC PSU farther from the radio. |
| RF on the audio cable when you transmit | Cable's acting as an antenna. Wrap a snap-on ferrite around it close to one or both ends. Cheap and effective. |
| PTT keys itself, won't release | VOX threshold too low; or constant noise on the TX side. Turn TX knob down or disable VOX and hard-wire PTT. |
| One-way audio (you transmit but nobody hears the link) | Wrong sound card selected in OS, or TX knob at zero. Run `python -m rumble --list-audio-devices` and confirm `audio.input_device` matches. |
| One-way audio (link transmits, you don't hear it) | Wrong output device, or speakers muted in OS. Linux: check `pavucontrol`'s Output Devices tab. |
| DTMF detects sometimes, not always | Levels marginal. Bump RX knob slightly, or have operators hold tones longer (250 ms). |
| TTS announcements sound chopped | VOX cutting off the leading edge. Switch to hardware PTT or increase VOX hang time. |
| Random "phantom" DTMF detections from voice | Speech has tonal content that occasionally overlaps DTMF bins. Set `dtmf_min_magnitude` higher (`0.1` is safe), or set squelch tighter. |

## A note on dedicated boxes

If you're committing to a real 24/7 link node, the project's
recommendation is **a Raspberry Pi 4 (or 5) in a small enclosure, with
the SignaLink USB-mounted on the back**. Cheaper than leaving a desktop
PC running, much quieter, draws roughly 5 W, never needs a screen.

Full setup is in [`DEPLOYMENT.md`](DEPLOYMENT.md), including a systemd
unit file and storage / log-rotation guidance.

The author's bench rig is a Pi 4 + SignaLink + UV-5R, exactly as
described above, with the radio's antenna on a dummy load for bench
testing and a real antenna for on-air.

See also: [`CONFIGURATION.md`](CONFIGURATION.md) for the audio device
selection field, [`DEPLOYMENT.md`](DEPLOYMENT.md) for systemd setup,
[`CONCEPTS.md`](CONCEPTS.md) for the Mumble side of the chain.
