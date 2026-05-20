# Command dispatcher — wires DTMF events through the state machine to Mumble.
#
# This is the only module that imports from every other rumble subsystem.
# Everyone else (audio, dtmf, dtmf_detector, mumble_client, tts, config) is
# kept independent so they can be tested and reused without dragging in the
# orchestration logic.
#
# Threading model
# ---------------
# Three threads touch dispatcher state:
#
# 1. The main thread — calls start(), wait(), stop().
# 2. AudioCapture's worker thread — calls _on_tone_event() once per detected
#    tone start/stop.
# 3. MumbleClient's pymumble thread — fires state-change callbacks (not used
#    for mutation yet, but the listener registration plumbing is here).
# 4. The dispatcher's own TTS worker thread — drains a queue and calls
#    self._tts.synthesize() + self._mumble.send_audio().
#
# A single threading.Lock (self._lock) guards all mutable dispatcher state:
# the active bank, sticky-mute flag, state-machine, mumble client handle,
# audio-capture handle, running flag.

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Protocol

from rumble.audio import AudioCapture
from rumble.config import (
    AudioConfig,
    Bank,
    MumbleServerConfig,
    RumbleConfig,
)
from rumble.dtmf import (
    AdminSetting,
    ChangeChannel,
    Command,
    Disconnect,
    DtmfStateMachine,
    LoadConfig,
)
from rumble.dtmf_detector import ToneEvent
from rumble.mumble_client import ConnectionState, MumbleChannelNotFoundError, MumbleClient
from rumble.tts import TextToSpeech

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Injectable factory protocols (for tests)
# ---------------------------------------------------------------------------


class _MumbleFactory(Protocol):
    def __call__(self, server: MumbleServerConfig) -> MumbleClient: ...


class _AudioCaptureFactory(Protocol):
    def __call__(
        self,
        audio_config: AudioConfig,
        on_tone: Callable[[ToneEvent], None],
    ) -> AudioCapture: ...


class _TtsFactory(Protocol):
    def __call__(self) -> TextToSpeech: ...


# ---------------------------------------------------------------------------
# Default factories — what the real program uses
# ---------------------------------------------------------------------------


def _default_mumble_factory(server: MumbleServerConfig) -> MumbleClient:
    return MumbleClient(
        host=server.host,
        port=server.port,
        username=server.username,
        password=server.password,
        certfile=server.certfile,
        keyfile=server.keyfile,
        reconnect=True,
    )


def _default_audio_capture_factory(
    audio_config: AudioConfig,
    on_tone: Callable[[ToneEvent], None],
) -> AudioCapture:
    return AudioCapture(
        device=audio_config.input_device,
        sample_rate=audio_config.sample_rate,
        on_tone=on_tone,
        min_magnitude=audio_config.dtmf_min_magnitude,
    )


def _default_tts_factory() -> TextToSpeech:
    return TextToSpeech()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


# Admin setting "00" toggles sticky mute. Other codes can be wired in by
# extending _ADMIN_HANDLERS — see _handle_admin_setting.
_ADMIN_STICKY_MUTE = "00"


class Dispatcher:
    """The conductor. Owns Mumble, audio capture, DTMF state, and TTS.

    Lifecycle::

        d = Dispatcher(config)
        d.start()       # connects Mumble, opens audio capture, announces self
        d.wait()        # blocks until d.stop() is called from another thread
        d.stop()        # idempotent clean shutdown
    """

    def __init__(
        self,
        config: RumbleConfig,
        bank: int | None = None,
        *,
        mumble_factory: _MumbleFactory | None = None,
        audio_capture_factory: _AudioCaptureFactory | None = None,
        tts_factory: _TtsFactory | None = None,
    ) -> None:
        """Construct the dispatcher.

        Args:
            config: A loaded :class:`RumbleConfig`.
            bank: Override the active bank (defaults to ``config.initial_bank``).
            mumble_factory: For tests — produces a MumbleClient given a
                :class:`MumbleServerConfig`. The default uses the real one.
            audio_capture_factory: For tests — same idea for AudioCapture.
            tts_factory: For tests — same idea for TextToSpeech.
        """
        self._config = config
        self._active_bank_num = bank if bank is not None else config.initial_bank
        # Validate the requested bank up front so we don't fail mid-start.
        self._active_bank: Bank = config.get_bank(self._active_bank_num)

        self._mumble_factory = mumble_factory or _default_mumble_factory
        self._audio_capture_factory = audio_capture_factory or _default_audio_capture_factory
        self._tts_factory = tts_factory or _default_tts_factory

        self._lock = threading.Lock()
        self._state_machine = DtmfStateMachine()
        self._sticky_mute = False
        self._running = False
        self._stop_event = threading.Event()

        self._mumble: MumbleClient | None = None
        self._current_server: MumbleServerConfig | None = None
        self._capture: AudioCapture | None = None
        self._tts: TextToSpeech | None = None

        # TTS worker — runs synthesis off the audio/pymumble threads.
        self._tts_queue: queue.Queue[str | None] = queue.Queue()
        self._tts_thread: threading.Thread | None = None

    # ----- public properties --------------------------------------------

    @property
    def is_running(self) -> bool:
        """``True`` between successful start() and stop()."""
        return self._running

    @property
    def current_command_buffer(self) -> str:
        """Pass-through to the DTMF state machine's in-progress buffer."""
        return self._state_machine.current_buffer

    @property
    def mumble(self) -> MumbleClient | None:
        """The current MumbleClient, or None when stopped."""
        return self._mumble

    @property
    def active_bank(self) -> int:
        """Bank number currently in effect (mutates with LoadConfig)."""
        return self._active_bank_num

    @property
    def sticky_mute(self) -> bool:
        """Whether the sticky-mute flag (admin 00/0) is set."""
        return self._sticky_mute

    # ----- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Connect, open audio capture, announce self over TTS.

        Raises:
            RuntimeError: If the active bank has no servers configured.
        """
        with self._lock:
            if self._running:
                return
            if not self._active_bank.servers:
                raise RuntimeError(f"bank {self._active_bank_num} has no servers configured")

            self._tts = self._tts_factory()
            self._start_tts_worker()

            first_server = self._active_bank.servers[0]
            self._connect_to_server_locked(first_server)

            self._capture = self._audio_capture_factory(self._config.audio, self._on_tone_event)
            self._capture.start()
            logger.info(
                "audio capture started (device=%r, sample_rate=%d Hz)",
                self._config.audio.input_device or "default",
                self._config.audio.sample_rate,
            )

            self._running = True
            self._stop_event.clear()

        # Announce outside the lock — _tts_say is enqueue-only and safe.
        self._tts_say(f"{self._config.callsign} rumble-py listening")
        logger.info(
            "dispatcher started; bank=%d callsign=%r",
            self._active_bank_num,
            self._config.callsign,
        )

    def stop(self) -> None:
        """Tear down audio, Mumble, and the TTS worker. Idempotent."""
        with self._lock:
            if not self._running:
                self._stop_event.set()
                return
            self._running = False

            if self._capture is not None:
                self._capture.stop()
                self._capture = None
            if self._mumble is not None:
                self._mumble.disconnect()
                self._mumble = None
                self._current_server = None
            if self._tts_thread is not None:
                self._tts_queue.put(None)  # sentinel
        if self._tts_thread is not None:
            self._tts_thread.join(timeout=2.0)
            self._tts_thread = None
        with self._lock:
            self._tts = None
            self._state_machine.reset()
            self._stop_event.set()
        logger.info("dispatcher stopped")

    def wait(self) -> None:
        """Block until :meth:`stop` is called from another thread."""
        self._stop_event.wait()

    # ----- testing hook -------------------------------------------------

    def feed_dtmf(self, char: str) -> None:
        """Inject a DTMF character with the same flow a real tone would take.

        Mutes Mumble at the start of a sequence, feeds the char into the
        state machine, dispatches any emitted command, and unmutes once the
        state machine returns to idle (unless sticky mute is set).
        Intended for tests and the web UI's "send DTMF" debug widget.
        """
        self._handle_tone_char(char)

    # ----- audio → DTMF → command flow ----------------------------------

    def _on_tone_event(self, event: ToneEvent) -> None:
        """AudioCapture worker thread → dispatcher.

        We mute Mumble the instant a tone *starts* so the keypad tones don't
        get relayed to other channel members (they're already obnoxious;
        relaying them across the link multiplies the misery). On tone
        *stop* we feed the character into the state machine — that's where
        commands are recognized — and unmute if the state machine has
        returned to idle and the operator hasn't engaged sticky mute.
        """
        if event.kind == "start":
            with self._lock:
                if self._mumble is not None:
                    self._mumble.set_mute(True)
            return
        # kind == "stop"
        self._handle_tone_char(event.char)

    def _handle_tone_char(self, char: str) -> None:
        with self._lock:
            command = self._state_machine.feed(char)
            became_idle = self._state_machine.is_idle

        if command is not None:
            self._dispatch_command(command)

        # Unmute when the state machine has finished (either by emitting a
        # command or by aborting on an invalid char), unless the operator
        # has asked us to stay muted via admin 00/0.
        if became_idle:
            with self._lock:
                if self._mumble is not None and not self._sticky_mute:
                    self._mumble.set_mute(False)

    # ----- command dispatch ---------------------------------------------

    def _dispatch_command(self, command: Command) -> None:
        logger.info("dispatch %r", command)
        if isinstance(command, Disconnect):
            self._handle_disconnect()
        elif isinstance(command, LoadConfig):
            self._handle_load_config(command.bank)
        elif isinstance(command, AdminSetting):
            self._handle_admin_setting(command.setting, command.value)
        elif isinstance(command, ChangeChannel):
            self._handle_change_channel(command.server, command.channel)

    def _handle_disconnect(self) -> None:
        with self._lock:
            if self._mumble is not None:
                # "Root" is the universal Mumble idle parking spot — we don't
                # actually drop the TCP connection, just move out of any
                # active channel.
                self._mumble.move_to_channel("Root")
        self._tts_say("disconnected")

    def _handle_load_config(self, bank_num: int) -> None:
        try:
            new_bank = self._config.get_bank(bank_num)
        except Exception:
            self._tts_say(f"bank {bank_num} not configured")
            logger.warning("LoadConfig requested missing bank %d", bank_num)
            return
        with self._lock:
            self._active_bank_num = bank_num
            self._active_bank = new_bank
        self._tts_say(f"loaded bank {bank_num}")
        logger.info("active bank is now %d", bank_num)

    def _handle_admin_setting(self, setting: str, value: str) -> None:
        if setting == _ADMIN_STICKY_MUTE:
            if value == "0":
                with self._lock:
                    self._sticky_mute = True
                    if self._mumble is not None:
                        self._mumble.set_mute(True)
                self._tts_say("muted")
                return
            if value == "1":
                with self._lock:
                    self._sticky_mute = False
                    if self._mumble is not None:
                        self._mumble.set_mute(False)
                self._tts_say("unmuted")
                return
        # Future settings can hook in here.
        logger.warning("unknown admin setting %s/%s", setting, value)
        self._tts_say("unknown admin setting")

    def _handle_change_channel(self, server_number: str, channel_number: str) -> None:
        mapping = self._active_bank.channel_for(server_number, channel_number)
        if mapping is None:
            logger.warning(
                "no channel mapping for %s/%s in bank %d",
                server_number,
                channel_number,
                self._active_bank_num,
            )
            self._tts_say("channel not found")
            return

        target_server = self._active_bank.server_by_name(mapping.server_ref)
        if target_server is None:
            logger.error(
                "channel mapping %s/%s refers to unknown server %r "
                "(should have been caught at config-load time)",
                server_number,
                channel_number,
                mapping.server_ref,
            )
            self._tts_say("server not configured")
            return

        with self._lock:
            need_server_switch = (
                self._current_server is None or self._current_server.name != target_server.name
            )

        if need_server_switch:
            self._switch_server(target_server)

        try:
            with self._lock:
                if self._mumble is not None:
                    self._mumble.move_to_channel(mapping.channel_path)
        except MumbleChannelNotFoundError:
            # Config disagrees with the server (channel was renamed/deleted).
            # Don't crash the dispatcher — tell the operator and carry on.
            logger.warning(
                "channel %r exists in config but not on the server",
                mapping.channel_path,
            )
            self._tts_say("channel not found on server")
            return
        self._tts_say(f"switched to {mapping.nickname}")

    # ----- server switching ---------------------------------------------

    def _switch_server(self, new_server: MumbleServerConfig) -> None:
        with self._lock:
            if self._mumble is not None:
                self._mumble.disconnect()
                self._mumble = None
                self._current_server = None
            self._connect_to_server_locked(new_server)

    def _connect_to_server_locked(self, server: MumbleServerConfig) -> None:
        """Caller holds self._lock."""
        client = self._mumble_factory(server)
        client.on_state_changed(self._on_mumble_state_changed)
        client.connect(timeout=10.0)
        # Apply sticky-mute on the new connection so it survives server hops.
        if self._sticky_mute:
            client.set_mute(True)
        self._mumble = client
        self._current_server = server
        logger.info("connected to server %r (%s:%d)", server.name, server.host, server.port)

    def _on_mumble_state_changed(self, state: ConnectionState) -> None:
        """Logger-only for now; future milestones may surface this via the web UI."""
        logger.info("mumble state: %s", state.name)

    # ----- TTS plumbing -------------------------------------------------

    def _start_tts_worker(self) -> None:
        self._tts_thread = threading.Thread(target=self._tts_worker, name="tts-worker", daemon=True)
        self._tts_thread.start()

    def _tts_worker(self) -> None:
        while True:
            text = self._tts_queue.get()
            if text is None:
                return
            tts = self._tts
            mumble = self._mumble
            if tts is None or mumble is None:
                # Shutting down; drop the message.
                continue
            try:
                pcm = tts.synthesize(text)
            except Exception:
                logger.exception("TTS synthesis failed for %r", text)
                continue
            try:
                mumble.send_audio(pcm)
            except Exception:
                logger.exception("Mumble send_audio failed for TTS")

    def _tts_say(self, text: str) -> None:
        """Enqueue ``text`` for synthesis. Returns immediately."""
        self._tts_queue.put(text)
