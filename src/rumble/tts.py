# Text-to-speech — pyttsx3 → 16-bit LE mono PCM at 48 kHz.
#
# We don't play the audio locally; we hand it to the dispatcher to push into
# the Mumble outbound stream. pyttsx3 can only emit by writing a WAV to disk
# (no in-process buffer API), so the synthesize() flow is:
#
#   1. pyttsx3.save_to_file(text, tmp.wav); engine.runAndWait()
#   2. wave.open() the result — note the rate/width/channels vary by driver
#      (espeak-ng on Linux is usually 22050 Hz mono 16-bit; SAPI on Windows
#      tends to be 44100 or 22050)
#   3. Convert to mono if needed (channel-average)
#   4. Resample to 48 kHz if needed (np.interp linear)
#   5. Return raw int16 LE bytes

from __future__ import annotations

import logging
import os
import tempfile
import wave
from typing import Final

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE: Final[int] = 48000


class TtsError(Exception):
    """TTS engine couldn't be initialized or synthesis failed."""


class TextToSpeech:
    """Cross-platform TTS via pyttsx3 (SAPI on Windows, espeak-ng on Linux).

    Output is returned as raw PCM bytes ready to hand to ``MumbleClient.send_audio``.
    The engine itself is held open across calls — pyttsx3 is expensive to
    initialize, so amortizing it pays off when commands are processed back
    to back.

    Not thread-safe: pyttsx3's espeak driver is global-ish and doesn't react
    well to concurrent calls. The dispatcher runs all TTS through a single
    worker thread to enforce serialization.
    """

    def __init__(self, rate: int = 175, voice: str | None = None) -> None:
        """Construct the engine.

        Args:
            rate: Words-per-minute speech rate. pyttsx3's default is 200;
                175 is slightly slower and more intelligible over a noisy
                radio link.
            voice: Optional voice id (driver-specific). ``None`` uses the
                engine's default voice.

        Raises:
            TtsError: If pyttsx3 fails to initialize. On Linux the most
                common cause is a missing ``espeak-ng`` package — install
                with ``sudo apt install espeak-ng``.
        """
        # pyttsx3 is imported lazily so importing the rumble package on a
        # machine without espeak-ng doesn't blow up unrelated code paths.
        try:
            import pyttsx3
        except ImportError as exc:  # pragma: no cover - pyttsx3 is in deps
            raise TtsError("pyttsx3 is not installed. `pip install -e .[dev]`.") from exc

        try:
            self._engine = pyttsx3.init()
        except Exception as exc:
            raise TtsError(
                "could not initialize pyttsx3 TTS engine. On Linux this "
                "usually means espeak-ng is missing — install with "
                "`sudo apt install espeak-ng`."
            ) from exc

        self._engine.setProperty("rate", rate)
        if voice is not None:
            self._engine.setProperty("voice", voice)

    def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` and return mono int16 LE PCM at 48 kHz.

        Blocking — returns when the WAV is written, read back, and converted.
        Typical latency is 100-300 ms for a short phrase.

        Args:
            text: The text to speak.

        Returns:
            Raw little-endian 16-bit signed PCM bytes, mono, 48 000 Hz.
        """
        # NamedTemporaryFile with delete=False so we can close it (release
        # the handle) before pyttsx3 tries to write to it on Windows.
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            self._engine.save_to_file(text, tmp_path)
            self._engine.runAndWait()
            return _read_and_convert(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_and_convert(wav_path: str) -> bytes:
    """Load ``wav_path`` and return 48 kHz mono int16 LE PCM bytes."""
    with wave.open(wav_path, "rb") as wav:
        nchannels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        framerate = wav.getframerate()
        nframes = wav.getnframes()
        raw = wav.readframes(nframes)

    if sampwidth != 2:
        raise TtsError(
            f"unexpected WAV sample width {sampwidth} bytes (only 16-bit " f"PCM is supported)"
        )
    samples = np.frombuffer(raw, dtype=np.int16)

    if nchannels == 2:
        # Average channels to produce mono. Promote to int32 before the mean
        # to avoid overflow on near-clipping samples.
        samples = samples.reshape(-1, 2).astype(np.int32).mean(axis=1).astype(np.int16)
    elif nchannels != 1:
        raise TtsError(f"unsupported channel count: {nchannels}")

    if framerate != TARGET_SAMPLE_RATE:
        samples = _resample_linear(samples, framerate, TARGET_SAMPLE_RATE)

    return samples.tobytes()


def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample int16 audio by linear interpolation. Good enough for speech."""
    if src_rate == dst_rate:
        return samples
    src_n = samples.size
    dst_n = int(round(src_n * dst_rate / src_rate))
    if dst_n <= 0:
        return np.zeros(0, dtype=np.int16)
    src_x = np.arange(src_n)
    dst_x = np.linspace(0, src_n - 1, dst_n)
    resampled = np.interp(dst_x, src_x, samples)
    return resampled.astype(np.int16)
