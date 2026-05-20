# Duck-typed fakes for Dispatcher tests.
#
# Leading underscore so pytest doesn't try to collect them as test modules.

from __future__ import annotations

from collections.abc import Callable

from rumble.config import AudioConfig, MumbleServerConfig
from rumble.dtmf_detector import ToneEvent
from rumble.mumble_client import ConnectionState


class FakeMumbleClient:
    """Stand-in for :class:`rumble.mumble_client.MumbleClient`.

    Records every public call and lets the test drive state changes via
    ``fake.fire_state(...)``.
    """

    def __init__(self, server: MumbleServerConfig) -> None:
        self.server = server
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.set_mute_calls: list[bool] = []
        self.set_deaf_calls: list[bool] = []
        self.move_to_channel_calls: list[str] = []
        self.send_audio_calls: list[bytes] = []
        self._state_listeners: list[Callable[[ConnectionState], None]] = []
        self._current_channel: str | None = None
        self._connected = False
        self._muted = False

    # --- pretend-Mumble API ---

    def connect(self, timeout: float = 10.0) -> None:
        self.connect_calls += 1
        self._connected = True
        self._current_channel = "Root"

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False
        self._current_channel = None

    def set_mute(self, muted: bool) -> None:
        self.set_mute_calls.append(muted)
        self._muted = muted

    def set_deaf(self, deafened: bool) -> None:
        self.set_deaf_calls.append(deafened)

    def move_to_channel(self, channel_path: str) -> None:
        self.move_to_channel_calls.append(channel_path)
        self._current_channel = channel_path

    def send_audio(self, pcm: bytes) -> None:
        self.send_audio_calls.append(pcm)

    def on_audio_received(self, _cb: Callable[..., None]) -> None: ...
    def on_state_changed(self, cb: Callable[[ConnectionState], None]) -> None:
        self._state_listeners.append(cb)

    def on_user_joined(self, _cb: Callable[[str], None]) -> None: ...
    def on_user_left(self, _cb: Callable[[str], None]) -> None: ...

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def current_channel(self) -> str | None:
        return self._current_channel

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def users_in_current_channel(self) -> list[str]:
        # No other users by default; tests that want peers can append here.
        return []

    @property
    def host(self) -> str:
        return self.server.host

    # --- test-side helpers ---

    def fire_state(self, state: ConnectionState) -> None:
        for cb in list(self._state_listeners):
            cb(state)


class FakeAudioCapture:
    """Stand-in for :class:`rumble.audio.AudioCapture`. Holds the ``on_tone``
    callback so tests can fire fake ToneEvents into it."""

    def __init__(
        self,
        audio_config: AudioConfig,
        on_tone: Callable[[ToneEvent], None],
    ) -> None:
        self.audio_config = audio_config
        self.on_tone = on_tone
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def fire(self, event: ToneEvent) -> None:
        """Pretend the audio path detected ``event``."""
        self.on_tone(event)


class FakeTextToSpeech:
    """Stand-in for :class:`rumble.tts.TextToSpeech`. Records spoken text and
    returns a small fixed PCM blob so the Mumble fake sees something."""

    _FIXED_PCM = b"\x00\x00" * 1024  # 2 KB of silence

    def __init__(self) -> None:
        self.synthesize_calls: list[str] = []

    def synthesize(self, text: str) -> bytes:
        self.synthesize_calls.append(text)
        return self._FIXED_PCM


# Convenience: factories that return prebuilt fakes and let the test grab
# them. Each factory remembers the last-created instance.


class FakeMumbleFactory:
    def __init__(self) -> None:
        self.last: FakeMumbleClient | None = None
        self.created: list[FakeMumbleClient] = []

    def __call__(self, server: MumbleServerConfig) -> FakeMumbleClient:
        client = FakeMumbleClient(server)
        self.last = client
        self.created.append(client)
        return client  # type: ignore[return-value]


class FakeAudioCaptureFactory:
    def __init__(self) -> None:
        self.last: FakeAudioCapture | None = None

    def __call__(
        self,
        audio_config: AudioConfig,
        on_tone: Callable[[ToneEvent], None],
    ) -> FakeAudioCapture:
        cap = FakeAudioCapture(audio_config, on_tone)
        self.last = cap
        return cap  # type: ignore[return-value]


class FakeTtsFactory:
    def __init__(self) -> None:
        self.last: FakeTextToSpeech | None = None

    def __call__(self) -> FakeTextToSpeech:
        tts = FakeTextToSpeech()
        self.last = tts
        return tts  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# FakeDispatcher — stand-in for web tests
# ---------------------------------------------------------------------------


from enum import Enum  # noqa: E402

from rumble.config import RumbleConfig  # noqa: E402
from rumble.web.log_buffer import LogBuffer  # noqa: E402


class _FakeConnectionState(Enum):
    DISCONNECTED = 1
    CONNECTED = 2


class FakeDispatcher:
    """Drop-in stand-in for :class:`rumble.commands.Dispatcher` in web tests.

    Implements the same property/method surface the web app reads. Records
    every action call on public attributes named ``*_calls`` for assertions.
    """

    def __init__(self, config: RumbleConfig) -> None:
        self._config = config
        self._active_bank_num = config.initial_bank
        first_server = config.get_bank(self._active_bank_num).servers[0]
        self._mumble = FakeMumbleClient(first_server)
        self._mumble.connect()
        self._log_buffer = LogBuffer()
        self._command_buffer = ""
        self._sticky_mute = False

        # action records
        self.feed_dtmf_calls: list[str] = []
        self.set_bank_calls: list[int] = []
        self.reload_config_calls: list[object] = []
        self.reload_should_raise: Exception | None = None

    # --- dispatcher-shaped properties ---

    @property
    def config(self) -> RumbleConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return True

    @property
    def state(self) -> _FakeConnectionState:
        return (
            _FakeConnectionState.CONNECTED
            if self._mumble.is_connected
            else _FakeConnectionState.DISCONNECTED
        )

    @property
    def mumble(self) -> FakeMumbleClient:
        return self._mumble

    @property
    def active_bank(self) -> int:
        return self._active_bank_num

    @property
    def available_banks(self) -> list[int]:
        return sorted(self._config.banks.keys())

    @property
    def current_command_buffer(self) -> str:
        return self._command_buffer

    @property
    def sticky_mute(self) -> bool:
        return self._sticky_mute

    @property
    def log_buffer(self) -> LogBuffer:
        return self._log_buffer

    # --- dispatcher-shaped actions ---

    def feed_dtmf(self, char: str) -> None:
        self.feed_dtmf_calls.append(char)

    def set_bank(self, n: int) -> None:
        # Mirror the real dispatcher's error shape.
        from rumble.config import ConfigError

        if n not in self.available_banks:
            raise ConfigError(f"bank {n} is not configured")
        self._active_bank_num = n
        self.set_bank_calls.append(n)

    def reload_config(self, path: object = None) -> None:
        self.reload_config_calls.append(path)
        if self.reload_should_raise is not None:
            raise self.reload_should_raise
