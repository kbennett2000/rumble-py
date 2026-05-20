# Audio I/O — sounddevice wrapper that feeds a DtmfDetector and dispatches
# the resulting ToneEvents.
#
# Threading model
# ---------------
# sounddevice runs its `callback=` argument in a real-time audio thread.
# Anything done there must be O(1) and lock-free, or we risk buffer
# underruns and audio drop-outs. So the callback's only job is to copy the
# incoming sample buffer into a thread-safe queue and return.
#
# A separate worker thread drains the queue, runs the (potentially slow)
# Goertzel/DTMF detection on each chunk, and finally calls the user-supplied
# `on_tone` callback. That callback is therefore allowed to log, write to
# disk, send protocol frames, etc. without risking audio glitches.
#
#     audio thread ──(np.ndarray)──> Queue ──> worker thread ──> on_tone()

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any

import numpy as np
import sounddevice as sd

from rumble.dtmf_detector import DEFAULT_SAMPLE_RATE, DtmfDetector, ToneEvent


def list_input_devices() -> list[dict]:
    """Return metadata for every audio device that supports input capture.

    Intended for use by the web UI's device picker and by the manual
    ``listen_for_dtmf.py`` harness.

    Returns:
        A list of ``{"index", "name", "channels", "sample_rate"}`` dicts —
        one entry per device with ``max_input_channels > 0``. Empty list on
        systems with no audio hardware (e.g., bare-bones CI runners).
    """
    devices = sd.query_devices()
    return [
        {
            "index": i,
            "name": d["name"],
            "channels": d["max_input_channels"],
            "sample_rate": d["default_samplerate"],
        }
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def _noop(_event: ToneEvent) -> None:
    """Default ``on_tone`` callback that drops events on the floor."""


class AudioCapture:
    """Capture audio from a sounddevice input and emit DTMF ToneEvents.

    Usage::

        def handle(event: ToneEvent) -> None:
            print(event)

        with AudioCapture(device=None, on_tone=handle) as cap:
            cap.start()
            # ... do whatever ...

    The context manager makes ``stop()`` automatic on exit. Calling
    ``stop()`` without a prior ``start()`` is safe (it's a no-op). Calling
    ``start()`` while already running is also a no-op.
    """

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        on_tone: Callable[[ToneEvent], None] = _noop,
    ) -> None:
        """Construct an AudioCapture.

        Args:
            device: sounddevice device selector. Pass an ``int`` to use the
                device at that index, a ``str`` to match by name substring,
                or ``None`` to use the system default input device.
            sample_rate: Sampling rate in Hz. Defaults to 8 kHz, which is the
                standard rate for DTMF / narrowband VOIP.
            on_tone: Callback invoked once per emitted ToneEvent. Runs on the
                worker thread (NOT the audio callback thread), so it's safe
                to do I/O and other blocking work here.
        """
        self._device = device
        self._sample_rate = sample_rate
        self._on_tone = on_tone

        self._detector = DtmfDetector(sample_rate=sample_rate)
        # None in the queue is the shutdown sentinel for the worker.
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._worker: threading.Thread | None = None

    # ----- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Open the audio stream and start the worker thread.

        No-op if already started.
        """
        if self._stream is not None:
            return

        self._worker = threading.Thread(target=self._run_worker, name="dtmf-detector", daemon=True)
        self._worker.start()

        self._stream = sd.InputStream(
            device=self._device,
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Close the audio stream and shut down the worker thread.

        Safe to call multiple times or without a prior ``start()``.
        """
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._worker is not None:
            # Signal the worker to exit; it will see the sentinel and return.
            self._queue.put(None)
            self._worker.join(timeout=2.0)
            self._worker = None

    # ----- context manager ----------------------------------------------

    def __enter__(self) -> AudioCapture:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()

    # ----- internals -----------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time: Any,
        _status: sd.CallbackFlags,
    ) -> None:
        """Real-time audio thread — keep this fast and allocation-light.

        We copy the sample buffer because sounddevice reuses ``indata`` for
        the next callback; without the copy, the worker thread would see
        the buffer mutate underneath it.
        """
        self._queue.put(indata[:, 0].copy())

    def _run_worker(self) -> None:
        """Drain the queue, run detection, invoke ``on_tone`` for each event."""
        while True:
            chunk = self._queue.get()
            if chunk is None:
                return
            for event in self._detector.process(chunk):
                self._on_tone(event)
