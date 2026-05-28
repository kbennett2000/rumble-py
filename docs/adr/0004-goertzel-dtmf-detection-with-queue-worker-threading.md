# 0004. Goertzel algorithm for DTMF detection with a queue/worker threading model

Date: 2026-05-28
Status: Accepted

## Context

DTMF tones need to be detected in a continuous audio stream coming in from a
radio microphone. The detection runs inside sounddevice's audio callback, which
the PortAudio library calls from a real-time thread. That thread has a hard
latency budget: any work that takes longer than one callback period causes an
overrun (audible glitch or dropped samples). Detection work cannot block,
allocate heavily, or do I/O.

At the same time, the detection result needs to flow to the command dispatcher,
which may log to disk, send Mumble protocol messages, or call TTS — all of
which can block arbitrarily.

## Decision

**Algorithm:** The Goertzel algorithm (`goertzel_magnitude()` in
`src/rumble/dtmf_detector.py`) is used to measure the spectral magnitude at
each of the eight DTMF frequencies. The audio stream is chunked into 205-sample
frames (~25.6 ms at 8 kHz). Each frame runs eight Goertzel filters and the
dominant low/high frequency pair identifies the digit. A two-frame debounce
rejects single-frame glitches.

**Threading:** The sounddevice callback (`AudioCapture._audio_callback`) is a
thin producer: it copies the incoming sample buffer and enqueues it onto a
`queue.Queue`. A separate worker thread (`AudioCapture._run_worker`) drains the
queue, calls `DtmfDetector.process()`, and fires the `on_tone` callback. The
callback therefore runs on the worker thread, not the audio thread, and may
block freely.

## Alternatives considered

- **Short-time Fourier transform (FFT)** — the FFT gives the full spectrum at
  once and can detect all eight DTMF frequencies in a single pass. But for
  DTMF we know the exact frequencies of interest in advance; running an
  FFT and then discarding all bins except eight wastes work. Goertzel
  computes only the bins we care about and runs in O(N) time (same
  asymptotic complexity but with a much smaller constant). Rejected in favor
  of Goertzel for this narrow use case.

- **LibreSSL / FFTW / SciPy spectrum analysis** — third-party DSP libraries
  could handle the detection, but they add a dependency for a task that is
  straightforward to implement. The Goertzel recurrence is twelve lines of
  Python; using a heavy library to avoid twelve lines is not a good trade.
  Rejected.

- **Run detection directly in the audio callback** — eliminates the queue and
  worker thread. The detection itself (eight Goertzel filters over 205 samples)
  is fast enough in practice, but the `on_tone` callback may not be: it can
  trigger TTS, file I/O, or Mumble protocol frames. Allowing those side effects
  on the audio thread risks buffer overruns. Rejected.

- **Use sounddevice's blocking mode (`sd.rec()` / `sd.wait()`)** — simpler
  code, no explicit threading. But blocking mode uses internal threads anyway
  and does not give us a queue we can flush on shutdown. Rejected.

## Consequences

What we gained:

- The audio callback is as thin as possible (a copy and a queue put), which
  minimizes the risk of buffer overruns under CPU load.
- Detection is completely decoupled from audio I/O: `DtmfDetector` has no
  imports from sounddevice and can be tested against synthesized audio arrays
  without touching hardware.
- The on_tone callback can do anything — log, sleep, send network frames —
  without affecting audio capture.

What we accepted:

- The queue adds one callback-cycle of latency between a tone arriving and
  the dispatcher acting on it. In practice this is one 25 ms frame — well
  within the tolerances of DTMF command entry from a hand microphone.
- The worker thread exists for the lifetime of the `AudioCapture` object.
  It is a daemon thread, so it does not block process exit, but it is extra
  state to manage (start/stop/join).
- The Goertzel loop iterates a numpy array in Python, which is slower than a
  vectorized numpy operation. At 8 kHz with 205-sample frames the timing is
  well inside budget, but the code comment in `dtmf_detector.py` notes the
  escape hatch if this ever becomes a bottleneck.

## Revisit if

- Per-frame Goertzel processing consistently exceeds the available 25 ms
  budget on the target hardware (Raspberry Pi), causing the queue to grow
  without bound and audio events to arrive late. At that point, replace the
  Python loop in `goertzel_magnitude()` with a precomputed-phasor `np.dot`
  (noted in the inline comment) or move detection to a compiled extension.
- The DTMF standard changes or the project needs to detect additional tone
  pairs (e.g., Selcall, CTCSS squelch tones). Goertzel generalizes cleanly;
  just add more target frequencies.
