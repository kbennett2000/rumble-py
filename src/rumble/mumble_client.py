# Mumble protocol wrapper — the only file in rumble-py that imports pymumble.
#
# Everything else in the project talks to Mumble through MumbleClient. If we
# ever swap libraries (e.g., to mumble-rs via PyO3), this is the only module
# that needs to change.
#
# Threading model
# ---------------
# Three threads can be active at once:
#
# 1. The caller's thread — invokes public methods, reads properties, registers
#    listeners. All public methods are safe to call from any thread.
# 2. The pymumble thread — pymumble's internal protocol thread. It calls our
#    internal handlers, which in turn dispatch to user-registered listeners.
#    **User callbacks therefore run on the pymumble thread.** They must not
#    block (no logging-to-disk, no network I/O); if they do, audio and
#    protocol handling will stall.
# 3. The reconnect thread — our own short-lived thread that runs only while a
#    dropped connection is being re-established. It transitions state and
#    restores channel/mute/deaf on success.

from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

# ---------------------------------------------------------------------------
# pymumble 1.6.1 (the current PyPI release) still calls the deprecated
# `ssl.wrap_socket()`, which Python 3.12 removed. Upstream master has been
# fixed but no release has shipped. Until pymumble cuts a new release, we
# install a shim that emulates wrap_socket() via SSLContext. This MUST run
# before `import pymumble_py3` below.
# ---------------------------------------------------------------------------
if not hasattr(ssl, "wrap_socket"):

    def _wrap_socket_shim(
        sock: Any,
        keyfile: str | None = None,
        certfile: str | None = None,
        server_side: bool = False,
        cert_reqs: int = ssl.CERT_NONE,
        ssl_version: int | None = None,
        ca_certs: str | None = None,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        ciphers: str | None = None,
    ) -> ssl.SSLSocket:
        proto = ssl.PROTOCOL_TLS_SERVER if server_side else ssl.PROTOCOL_TLS_CLIENT
        ctx = ssl.SSLContext(proto)
        ctx.check_hostname = False
        ctx.verify_mode = cert_reqs
        if ca_certs:
            ctx.load_verify_locations(ca_certs)
        if certfile:
            ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
        return ctx.wrap_socket(
            sock,
            server_side=server_side,
            do_handshake_on_connect=do_handshake_on_connect,
            suppress_ragged_eofs=suppress_ragged_eofs,
        )

    ssl.wrap_socket = _wrap_socket_shim  # type: ignore[attr-defined]

import pymumble_py3 as pymumble  # noqa: E402  (must come after the shim)
from pymumble_py3 import constants as pm_const  # noqa: E402
from pymumble_py3.errors import UnknownChannelError  # noqa: E402

logger = logging.getLogger(__name__)

# Mumble's narrowband-up internal audio format. Hard-coded by pymumble.
MUMBLE_SAMPLE_RATE = pm_const.PYMUMBLE_SAMPLERATE  # 48000

# Default port for the Mumble protocol.
DEFAULT_MUMBLE_PORT = 64738

# Audio log throttle: emit a DEBUG line at most this often.
_AUDIO_LOG_INTERVAL_S = 1.0


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MumbleAudioFrame:
    """A decoded audio chunk received from a remote user.

    Attributes:
        user_name: The remote user's display name.
        user_session: The numeric session id pymumble assigned to that user.
        pcm: Raw 16-bit signed little-endian mono PCM bytes.
        sample_rate: Sample rate of ``pcm``. Always 48000 — included so
            downstream callers don't have to remember the constant.
    """

    user_name: str
    user_session: int
    pcm: bytes
    sample_rate: int = MUMBLE_SAMPLE_RATE


class ConnectionState(Enum):
    """Lifecycle states of the Mumble connection."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    # Reconnection was cancelled (disconnect() called mid-retry). Distinct
    # from DISCONNECTED so callers can tell the difference between a clean
    # shutdown and a user-initiated abort during reconnect.
    FAILED = auto()


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class MumbleError(Exception):
    """Base class for everything raised by this module."""


class MumbleConnectionError(MumbleError):
    """Initial connect failed or timed out."""


class MumbleChannelNotFoundError(MumbleError):
    """move_to_channel was called with a path that doesn't exist on the server."""


class MumbleNotConnectedError(MumbleError):
    """An operation that requires a live connection was attempted while disconnected."""


# ---------------------------------------------------------------------------
# URI parser
# ---------------------------------------------------------------------------


def parse_mumble_uri(uri: str) -> dict[str, Any]:
    """Parse a ``mumble://...`` URI into kwargs for :class:`MumbleClient`.

    Accepts the standard Mumble URI shapes:

    * ``mumble://user@host``
    * ``mumble://user:pass@host:port``
    * ``mumble://user@host/Root/General``
    * ``mumble://user:pass@host:port/Root/Some%20Channel``

    Args:
        uri: The URI string.

    Returns:
        A dict with keys ``host`` (str), ``port`` (int), ``username`` (str),
        and optionally ``password`` (str) and ``channel_path`` (str).

    Raises:
        ValueError: If the scheme is not ``mumble``, the host is missing,
            or the username is missing.
    """
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "mumble":
        raise ValueError(f"not a mumble URI: {uri!r}")
    if not parsed.hostname:
        raise ValueError(f"missing host in URI: {uri!r}")
    if not parsed.username:
        raise ValueError(f"missing username in URI: {uri!r}")

    result: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or DEFAULT_MUMBLE_PORT,
        "username": urllib.parse.unquote(parsed.username),
    }
    if parsed.password:
        result["password"] = urllib.parse.unquote(parsed.password)
    if parsed.path and parsed.path != "/":
        channel_path = urllib.parse.unquote(parsed.path).lstrip("/")
        if channel_path:
            result["channel_path"] = channel_path
    return result


# ---------------------------------------------------------------------------
# MumbleClient
# ---------------------------------------------------------------------------


class MumbleClient:
    """High-level Mumble client. Wraps pymumble and exposes a clean API.

    See module docstring for the thread model. All public methods are safe to
    call from any thread; user-registered event callbacks fire on the
    pymumble thread (or briefly on the reconnect thread during reconnects).
    Callbacks must return quickly to keep audio flowing.
    """

    # Event names used internally; the public on_X registration methods route
    # to these.
    _EVENT_AUDIO = "audio_received"
    _EVENT_STATE = "state_changed"
    _EVENT_USER_JOINED = "user_joined"
    _EVENT_USER_LEFT = "user_left"

    def __init__(
        self,
        host: str,
        username: str,
        port: int = DEFAULT_MUMBLE_PORT,
        password: str | None = None,
        certfile: str | None = None,
        keyfile: str | None = None,
        reconnect: bool = True,
        reconnect_max_backoff: float = 60.0,
    ) -> None:
        """Construct a Mumble client. No network activity until :meth:`connect`.

        Args:
            host: Mumble server hostname or IP.
            username: Display name for this client on the server.
            port: TCP port. Defaults to 64738 (standard Mumble port).
            password: Server password. ``None`` for open servers.
            certfile: Path to a client certificate PEM file, or ``None``.
            keyfile: Path to the private key PEM, or ``None``.
            reconnect: If ``True`` (default), automatically retry on a
                dropped connection with exponential backoff.
            reconnect_max_backoff: Maximum delay between reconnect attempts,
                in seconds. The sequence is 1, 2, 4, 8, ... capped at this.
        """
        if not host:
            raise ValueError("host is required")
        if not username:
            raise ValueError("username is required")

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._certfile = certfile
        self._keyfile = keyfile
        self._reconnect = reconnect
        self._reconnect_max_backoff = reconnect_max_backoff

        # Internal state — guarded by _lock. The lock is reentrant because
        # some methods call each other (e.g., disconnect during reconnect).
        self._lock = threading.RLock()
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._mumble: pymumble.Mumble | None = None
        self._connect_timeout: float = 10.0

        # Desired state that we cache so it survives reconnects (and can be
        # set before the first connect).
        self._muted: bool = False
        self._deafened: bool = False
        self._desired_channel: str | None = None

        # Reconnect machinery.
        self._reconnect_thread: threading.Thread | None = None
        self._reconnect_cancelled = threading.Event()
        self._reconnect_attempts: int = 0

        # Listeners — list of callbacks per event. Mutated only under _lock,
        # but we iterate copies so callbacks can register/unregister freely.
        self._listeners: dict[str, list[Callable[..., None]]] = {
            self._EVENT_AUDIO: [],
            self._EVENT_STATE: [],
            self._EVENT_USER_JOINED: [],
            self._EVENT_USER_LEFT: [],
        }

        # Rate limiting for the audio-frame DEBUG log line.
        self._last_audio_log_time: float = 0.0

    # ----- public properties --------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Current high-level connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """``True`` exactly when :attr:`state` is ``CONNECTED``."""
        return self._state is ConnectionState.CONNECTED

    @property
    def username(self) -> str:
        """The username this client connects with."""
        return self._username

    @property
    def muted(self) -> bool:
        """Whether this client is self-muted. Reflects the cached desired state."""
        return self._muted

    @property
    def deafened(self) -> bool:
        """Whether this client is self-deafened. Reflects the cached desired state."""
        return self._deafened

    @property
    def current_channel(self) -> str | None:
        """The slash-separated path of the channel we're in, or ``None``.

        Example: ``"Root/Lobby"``. ``None`` when not connected.
        """
        if not self.is_connected or self._mumble is None:
            return None
        myself = self._mumble.users.myself
        if myself is None:
            return None
        channel = self._mumble.channels[myself["channel_id"]]
        tree = self._mumble.channels.get_tree(channel)
        return "/".join(c["name"] for c in tree)

    @property
    def users_in_current_channel(self) -> list[str]:
        """Names of every other user currently in our channel."""
        if not self.is_connected or self._mumble is None:
            return []
        myself = self._mumble.users.myself
        if myself is None:
            return []
        my_session = myself["session"]
        my_channel_id = myself["channel_id"]
        return [
            u["name"]
            for u in self._mumble.users.values()
            if u["channel_id"] == my_channel_id and u["session"] != my_session
        ]

    # ----- lifecycle ----------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        """Connect synchronously to the Mumble server.

        Blocks until the server has sent ``ServerSync`` (i.e., the connection
        is fully established and channels/users are populated), or until the
        timeout elapses.

        Args:
            timeout: Maximum time to wait, in seconds.

        Raises:
            MumbleConnectionError: If the connection fails or times out. No
                automatic retry happens here; reconnection logic only kicks
                in for established connections that subsequently drop.
        """
        with self._lock:
            if self._state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
                return
            self._reconnect_cancelled.clear()
            self._connect_timeout = timeout
            self._transition_state(ConnectionState.CONNECTING)

        try:
            self._open_connection(timeout)
        except MumbleConnectionError:
            with self._lock:
                self._teardown_mumble_locked()
                self._transition_state(ConnectionState.DISCONNECTED)
            raise

        with self._lock:
            self._transition_state(ConnectionState.CONNECTED)

    def disconnect(self) -> None:
        """Tear down the connection. Idempotent; safe to call from any state.

        Cancels any in-progress reconnect. If we were mid-reconnect when
        called, the final state will be ``FAILED``; otherwise it will be
        ``DISCONNECTED``.
        """
        self._reconnect_cancelled.set()
        thread = self._reconnect_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        with self._lock:
            self._teardown_mumble_locked()
            # If the reconnect thread set FAILED before exiting, leave it.
            if self._state is not ConnectionState.FAILED:
                self._transition_state(ConnectionState.DISCONNECTED)

    def move_to_channel(self, channel_path: str) -> None:
        """Move into the channel at ``channel_path``.

        Args:
            channel_path: A slash-separated path. The leading ``"Root"`` and
                any leading slash are optional, so all of ``"Root/Lobby"``,
                ``"/Root/Lobby"``, and ``"Lobby"`` mean the same channel.

        Raises:
            MumbleNotConnectedError: If we're not currently connected.
            MumbleChannelNotFoundError: If no channel matches the path.
        """
        with self._lock:
            if not self.is_connected or self._mumble is None:
                raise MumbleNotConnectedError("not connected")
            channel = self._resolve_channel_path(channel_path)
            self._mumble.users.myself.move_in(channel["channel_id"])
            self._desired_channel = channel_path
            logger.info("moving to channel %r", channel_path)

    def set_mute(self, muted: bool) -> None:
        """Mute (or unmute) this client's outgoing audio.

        State is cached, so calling this before :meth:`connect` is fine —
        the requested state is applied as soon as the connection is open,
        and re-applied on every reconnect.
        """
        with self._lock:
            self._muted = muted
            if self.is_connected and self._mumble is not None:
                me = self._mumble.users.myself
                if muted:
                    me.mute()
                else:
                    me.unmute()
                logger.info("mute=%s", muted)

    def set_deaf(self, deafened: bool) -> None:
        """Deafen (or undeafen) this client. State is cached like :meth:`set_mute`."""
        with self._lock:
            self._deafened = deafened
            if self.is_connected and self._mumble is not None:
                me = self._mumble.users.myself
                if deafened:
                    me.deafen()
                else:
                    me.undeafen()
                logger.info("deaf=%s", deafened)

    def send_audio(self, pcm: bytes) -> None:
        """Push 16-bit LE signed mono PCM at 48 kHz into the outbound stream.

        Silently does nothing if we're not connected — this matches how a
        real radio behaves when its uplink fails, and avoids forcing every
        audio-source caller to gate on ``is_connected``.
        """
        with self._lock:
            if not self.is_connected or self._mumble is None:
                return
            self._mumble.sound_output.add_sound(pcm)

    # ----- listener registration ----------------------------------------

    def on_audio_received(self, callback: Callable[[MumbleAudioFrame], None]) -> None:
        """Register a callback for incoming audio frames.

        Callbacks fire on the pymumble protocol thread. **Do not block.**
        """
        self._register(self._EVENT_AUDIO, callback)

    def on_state_changed(self, callback: Callable[[ConnectionState], None]) -> None:
        """Register a callback for connection state transitions."""
        self._register(self._EVENT_STATE, callback)

    def on_user_joined(self, callback: Callable[[str], None]) -> None:
        """Register a callback that fires when a user joins the server.

        The callback receives the username. Fires for every user that
        appears, not just those entering our channel.
        """
        self._register(self._EVENT_USER_JOINED, callback)

    def on_user_left(self, callback: Callable[[str], None]) -> None:
        """Register a callback that fires when a user leaves the server."""
        self._register(self._EVENT_USER_LEFT, callback)

    # ----- internals: connection management -----------------------------

    def _open_connection(self, timeout: float) -> None:
        """Open a fresh pymumble connection. Caller holds no locks."""
        mumble = pymumble.Mumble(
            host=self._host,
            user=self._username,
            port=self._port,
            password=self._password or "",
            certfile=self._certfile,
            keyfile=self._keyfile,
            # Auto-reconnect is handled by us, not pymumble.
            reconnect=False,
        )
        # Register callbacks BEFORE start() so we don't race the protocol thread.
        self._register_pymumble_callbacks(mumble)

        # pymumble's default is to silently drop incoming audio. That makes
        # sense for control bots but is the wrong default for us — the whole
        # point of this wrapper is to forward audio between Mumble and the
        # radio, so we always want PCM decoded and emitted via SOUNDRECEIVED.
        mumble.set_receive_sound(True)

        mumble.start()

        # Poll the connection state. pymumble's is_ready() blocks indefinitely;
        # we want a timeout, and we want to detect FAILED quickly.
        deadline = time.monotonic() + timeout
        while True:
            pm_state = mumble.connected
            if pm_state == pm_const.PYMUMBLE_CONN_STATE_CONNECTED:
                break
            if pm_state == pm_const.PYMUMBLE_CONN_STATE_FAILED:
                raise MumbleConnectionError(f"connection to {self._host}:{self._port} failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Close the half-open socket so pymumble's thread can exit.
                self._safe_close_socket(mumble)
                raise MumbleConnectionError(f"connection to {self._host}:{self._port} timed out")
            time.sleep(min(0.05, remaining))

        with self._lock:
            self._mumble = mumble
            # Apply cached self-state.
            if self._muted:
                mumble.users.myself.mute()
            if self._deafened:
                mumble.users.myself.deafen()
        logger.info("connected to %s:%d as %r", self._host, self._port, self._username)

    def _teardown_mumble_locked(self) -> None:
        """Close any active pymumble connection. Caller holds _lock."""
        if self._mumble is None:
            return
        self._safe_close_socket(self._mumble)
        self._mumble = None

    @staticmethod
    def _safe_close_socket(mumble: pymumble.Mumble) -> None:
        """Best-effort shutdown: signal EOF over TCP, then close the socket.

        pymumble has no public disconnect API. We shutdown the TCP layer
        first so pymumble's blocking ``select()`` returns and its ``recv()``
        cleanly raises ``socket.error`` (which pymumble's run-loop catches).
        Without the shutdown, an immediate close() turns the FD to -1 and
        pymumble's next ``select()`` raises ``ValueError`` on Python 3.12 —
        which pymumble's ``except socket.error:`` clause doesn't catch, so
        its thread crashes and the DISCONNECTED callback never fires.
        """
        sock = getattr(mumble, "control_socket", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except (OSError, ssl.SSLError, ValueError):
            # Socket already shut down, SSL state mismatch, or other races.
            pass
        try:
            sock.close()
        except OSError:
            pass

    # ----- internals: reconnect loop ------------------------------------

    def _on_unexpected_disconnect(self) -> None:
        """Handler for pymumble's DISCONNECTED callback (pymumble thread)."""
        with self._lock:
            # We only react to drops that happened mid-session. Drops during
            # the initial connect are handled by the polling loop in
            # _open_connection.
            if self._state is not ConnectionState.CONNECTED:
                return
            if not self._reconnect:
                self._teardown_mumble_locked()
                self._transition_state(ConnectionState.DISCONNECTED)
                return
            self._transition_state(ConnectionState.RECONNECTING)
            self._reconnect_cancelled.clear()
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop, name="mumble-reconnect", daemon=True
            )
            self._reconnect_thread.start()
        logger.warning("connection dropped; reconnecting in background")

    def _reconnect_loop(self) -> None:
        """Retry connect() with exponential backoff. Runs on the reconnect thread."""
        backoff = 1.0
        while not self._reconnect_cancelled.is_set():
            # Reuse the original connect timeout for each attempt.
            self._reconnect_attempts += 1
            attempt = self._reconnect_attempts
            logger.warning("reconnect attempt %d", attempt)

            with self._lock:
                self._teardown_mumble_locked()

            try:
                self._open_connection(self._connect_timeout)
            except MumbleConnectionError as exc:
                logger.error("reconnect attempt %d failed: %s", attempt, exc)
                # Sleep with cancellation support.
                if self._reconnect_cancelled.wait(timeout=backoff):
                    break
                backoff = min(backoff * 2, self._reconnect_max_backoff)
                continue

            # Successful reconnect. Restore desired channel; mute/deaf state
            # was already applied inside _open_connection.
            with self._lock:
                if self._desired_channel is not None:
                    try:
                        channel = self._resolve_channel_path(self._desired_channel)
                        self._mumble.users.myself.move_in(channel["channel_id"])
                    except (MumbleChannelNotFoundError, AttributeError):
                        logger.exception(
                            "failed to restore channel %r after reconnect",
                            self._desired_channel,
                        )
                self._transition_state(ConnectionState.CONNECTED)
            logger.info("reconnected after %d attempt(s)", attempt)
            return

        # Cancelled before we succeeded.
        with self._lock:
            self._teardown_mumble_locked()
            self._transition_state(ConnectionState.FAILED)
        logger.info("reconnect cancelled after %d attempt(s)", self._reconnect_attempts)

    # ----- internals: pymumble callback plumbing ------------------------

    def _register_pymumble_callbacks(self, mumble: pymumble.Mumble) -> None:
        mumble.callbacks.add_callback(
            pm_const.PYMUMBLE_CLBK_DISCONNECTED, self._on_unexpected_disconnect
        )
        mumble.callbacks.add_callback(pm_const.PYMUMBLE_CLBK_SOUNDRECEIVED, self._on_pymumble_sound)
        mumble.callbacks.add_callback(
            pm_const.PYMUMBLE_CLBK_USERCREATED, self._on_pymumble_user_created
        )
        mumble.callbacks.add_callback(
            pm_const.PYMUMBLE_CLBK_USERREMOVED, self._on_pymumble_user_removed
        )

    def _on_pymumble_sound(self, user: Any, chunk: Any) -> None:
        """pymumble SOUNDRECEIVED → MumbleAudioFrame."""
        frame = MumbleAudioFrame(
            user_name=user["name"],
            user_session=user["session"],
            pcm=chunk.pcm,
        )
        now = time.monotonic()
        if now - self._last_audio_log_time >= _AUDIO_LOG_INTERVAL_S:
            self._last_audio_log_time = now
            logger.debug("audio from %s: %d bytes", frame.user_name, len(frame.pcm))
        self._dispatch(self._EVENT_AUDIO, frame)

    def _on_pymumble_user_created(self, user: Any) -> None:
        name = user.get("name", "")
        if name:
            self._dispatch(self._EVENT_USER_JOINED, name)

    def _on_pymumble_user_removed(self, user: Any, _msg: Any) -> None:
        name = user.get("name", "")
        if name:
            self._dispatch(self._EVENT_USER_LEFT, name)

    # ----- internals: misc helpers --------------------------------------

    def _register(self, event_name: str, callback: Callable[..., None]) -> None:
        with self._lock:
            self._listeners[event_name].append(callback)

    def _dispatch(self, event_name: str, *args: Any) -> None:
        # Copy under lock so the iteration is safe even if a callback
        # registers another listener.
        with self._lock:
            callbacks = list(self._listeners[event_name])
        for cb in callbacks:
            try:
                cb(*args)
            except Exception:
                # One bad listener does not break the others.
                logger.exception("listener for %r raised", event_name)

    def _transition_state(self, new_state: ConnectionState) -> None:
        """Update state and notify listeners. Caller holds _lock."""
        if new_state is self._state:
            return
        logger.info("state: %s -> %s", self._state.name, new_state.name)
        self._state = new_state
        # Dispatch outside the iteration of internal data, but we're still
        # holding the reentrant lock — that's intentional, since listeners
        # may want to query state inside their handler.
        self._dispatch(self._EVENT_STATE, new_state)

    def _resolve_channel_path(self, channel_path: str) -> Any:
        """Translate a path like 'Root/Lobby' into a pymumble Channel dict.

        Caller holds _lock; we're connected.
        """
        # Split on either separator, drop empties.
        parts = [p for p in channel_path.replace("\\", "/").split("/") if p]
        # The root channel's name appears as the first path component in the
        # canonical form, but pymumble's find_by_tree starts from root, so
        # we drop it. Use the actual root name from the server rather than
        # hardcoding "Root", in case the operator renamed it.
        assert self._mumble is not None
        root_name = self._mumble.channels[0]["name"]
        if parts and parts[0] == root_name:
            parts = parts[1:]
        try:
            return self._mumble.channels.find_by_tree(parts)
        except UnknownChannelError as exc:
            raise MumbleChannelNotFoundError(f"channel {channel_path!r} not found") from exc
