# Config loader — YAML → typed dataclasses with validation.
#
# The on-disk format supports config "banks" (the DTMF `LoadConfig(bank=N)`
# command swaps between them at runtime). All banks live in one file —
# audio/ident/web settings are shared across banks; only the server list and
# channel map differ.
#
# Validation is strict at load time: every (server_number, channel_number)
# pair is unique within a bank, every channel mapping references a server
# that exists in the same bank, etc. Anything wrong raises ConfigError with
# a message identifying the bank and the bad field.

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when the YAML config can't be loaded or fails validation."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MumbleServerConfig:
    """A Mumble server we can connect to."""

    name: str
    host: str
    port: int = 64738
    username: str = "rumble-py"
    password: str | None = None
    certfile: str | None = None
    keyfile: str | None = None


@dataclass(frozen=True)
class ChannelMapping:
    """One row in the DTMF (server_number, channel_number) → real channel map."""

    server_number: str  # 3-digit zero-padded string: "001"
    channel_number: str  # 1-digit string: "2"
    server_ref: str  # MumbleServerConfig.name
    channel_path: str  # Mumble channel path, e.g. "Root/Repeaters/W0XYZ"
    nickname: str  # spoken via TTS when joining


@dataclass(frozen=True)
class Bank:
    """One bank — the set of servers and channel mappings that get loaded
    together by `LoadConfig(bank=N)`."""

    servers: tuple[MumbleServerConfig, ...] = field(default_factory=tuple)
    channels: tuple[ChannelMapping, ...] = field(default_factory=tuple)

    def server_by_name(self, name: str) -> MumbleServerConfig | None:
        for s in self.servers:
            if s.name == name:
                return s
        return None

    def channel_for(self, server_number: str, channel_number: str) -> ChannelMapping | None:
        for c in self.channels:
            if c.server_number == server_number and c.channel_number == channel_number:
                return c
        return None


@dataclass(frozen=True)
class AudioConfig:
    """Audio I/O parameters for the radio interface."""

    input_device: str | None = None  # None / "default" → system default
    output_device: str | None = None
    sample_rate: int = 8000  # for DTMF detection
    dtmf_min_magnitude: float = 0.05


@dataclass(frozen=True)
class IdentConfig:
    """Periodic station identification."""

    wav_path: str | None = None
    interval_seconds: int = 600  # 10 minutes, FCC §97.119 maximum


@dataclass(frozen=True)
class WebConfig:
    """Built-in FastAPI configuration UI."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass(frozen=True)
class RumbleConfig:
    """The full loaded config — all banks plus the shared settings."""

    callsign: str
    banks: dict[int, Bank]
    audio: AudioConfig
    ident: IdentConfig
    web: WebConfig
    initial_bank: int = 0

    def get_bank(self, n: int) -> Bank:
        """Return the bank at index ``n``, or raise ConfigError if missing."""
        if n not in self.banks:
            raise ConfigError(f"bank {n} is not configured")
        return self.banks[n]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_config(path: str | Path) -> RumbleConfig:
    """Load and validate a YAML config from disk.

    Args:
        path: Path to the YAML file.

    Returns:
        A fully-validated :class:`RumbleConfig`.

    Raises:
        ConfigError: If the file is missing, unparseable, or fails any
            validation rule.
    """
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{p}: top-level YAML must be a mapping")

    return _parse_root(raw, source=str(p))


def _parse_root(raw: Mapping[str, Any], *, source: str) -> RumbleConfig:
    callsign = _require_str(raw, "callsign", source)
    initial_bank = int(raw.get("initial_bank", 0))

    audio = _parse_audio(raw.get("audio") or {})
    ident = _parse_ident(raw.get("ident") or {})
    web = _parse_web(raw.get("web") or {})

    banks_raw = raw.get("banks")
    if not isinstance(banks_raw, Mapping) or not banks_raw:
        raise ConfigError(f"{source}: 'banks' must be a non-empty mapping")

    banks: dict[int, Bank] = {}
    for key, bank_raw in banks_raw.items():
        try:
            bank_num = int(key)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{source}: bank key {key!r} must be an integer") from exc
        if not isinstance(bank_raw, Mapping):
            raise ConfigError(f"{source}: bank {bank_num}: must be a mapping")
        banks[bank_num] = _parse_bank(bank_raw, bank_num=bank_num, source=source)

    if initial_bank not in banks:
        raise ConfigError(f"{source}: initial_bank {initial_bank} is not present in 'banks'")

    return RumbleConfig(
        callsign=callsign,
        banks=banks,
        audio=audio,
        ident=ident,
        web=web,
        initial_bank=initial_bank,
    )


def _parse_audio(raw: Mapping[str, Any]) -> AudioConfig:
    input_device = _normalize_device(raw.get("input_device"))
    output_device = _normalize_device(raw.get("output_device"))
    return AudioConfig(
        input_device=input_device,
        output_device=output_device,
        sample_rate=int(raw.get("sample_rate", 8000)),
        dtmf_min_magnitude=float(raw.get("dtmf_min_magnitude", 0.05)),
    )


def _parse_ident(raw: Mapping[str, Any]) -> IdentConfig:
    return IdentConfig(
        wav_path=raw.get("wav_path") or None,
        interval_seconds=int(raw.get("interval_seconds", 600)),
    )


def _parse_web(raw: Mapping[str, Any]) -> WebConfig:
    return WebConfig(
        enabled=bool(raw.get("enabled", True)),
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8080)),
    )


def _parse_bank(raw: Mapping[str, Any], *, bank_num: int, source: str) -> Bank:
    servers_raw = raw.get("servers") or []
    if not isinstance(servers_raw, list):
        raise ConfigError(f"{source}: bank {bank_num}: 'servers' must be a list")
    servers = tuple(
        _parse_server(s, bank_num=bank_num, idx=i, source=source) for i, s in enumerate(servers_raw)
    )

    channels_raw = raw.get("channels") or []
    if not isinstance(channels_raw, list):
        raise ConfigError(f"{source}: bank {bank_num}: 'channels' must be a list")
    channels = tuple(
        _parse_channel(c, bank_num=bank_num, idx=i, source=source)
        for i, c in enumerate(channels_raw)
    )

    _validate_bank(servers, channels, bank_num=bank_num, source=source)
    return Bank(servers=servers, channels=channels)


def _parse_server(
    raw: Mapping[str, Any], *, bank_num: int, idx: int, source: str
) -> MumbleServerConfig:
    where = f"{source}: bank {bank_num} server[{idx}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: must be a mapping")
    name = _require_str(raw, "name", where)
    host = _require_str(raw, "host", where)
    return MumbleServerConfig(
        name=name,
        host=host,
        port=int(raw.get("port", 64738)),
        username=str(raw.get("username") or "rumble-py"),
        password=raw.get("password") or None,
        certfile=raw.get("certfile") or None,
        keyfile=raw.get("keyfile") or None,
    )


def _parse_channel(
    raw: Mapping[str, Any], *, bank_num: int, idx: int, source: str
) -> ChannelMapping:
    where = f"{source}: bank {bank_num} channel[{idx}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where}: must be a mapping")

    server_number = _normalize_digit_string(
        raw.get("server_number"), width=3, where=f"{where} 'server_number'"
    )
    channel_number = _normalize_digit_string(
        raw.get("channel_number"), width=1, where=f"{where} 'channel_number'"
    )
    return ChannelMapping(
        server_number=server_number,
        channel_number=channel_number,
        server_ref=_require_str(raw, "server_ref", where),
        channel_path=_require_str(raw, "channel_path", where),
        nickname=_require_str(raw, "nickname", where),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_bank(
    servers: tuple[MumbleServerConfig, ...],
    channels: tuple[ChannelMapping, ...],
    *,
    bank_num: int,
    source: str,
) -> None:
    seen_names: set[str] = set()
    for s in servers:
        if s.name in seen_names:
            raise ConfigError(f"{source}: bank {bank_num}: duplicate server name {s.name!r}")
        seen_names.add(s.name)

    seen_pairs: set[tuple[str, str]] = set()
    for c in channels:
        pair = (c.server_number, c.channel_number)
        if pair in seen_pairs:
            raise ConfigError(
                f"{source}: bank {bank_num}: duplicate channel mapping "
                f"for DTMF {c.server_number}/{c.channel_number}"
            )
        seen_pairs.add(pair)
        if c.server_ref not in seen_names:
            raise ConfigError(
                f"{source}: bank {bank_num}: channel mapping "
                f"{c.server_number}/{c.channel_number} references unknown "
                f"server {c.server_ref!r}"
            )


def _require_str(d: Mapping[str, Any], key: str, where: str) -> str:
    val = d.get(key)
    if val is None or not str(val).strip():
        raise ConfigError(f"{where}: '{key}' is required and must be non-empty")
    return str(val)


def _normalize_digit_string(value: Any, *, width: int, where: str) -> str:
    """Coerce ``value`` into a zero-padded string of exactly ``width`` digits.

    Accepts integers (helpful when YAML auto-coerces unquoted numbers, e.g.
    ``001`` → 1) and zero-pads them back to the expected width. Anything
    non-numeric or longer than ``width`` is an error.
    """
    if value is None:
        raise ConfigError(f"{where}: required")
    if isinstance(value, bool):
        # bool is a subclass of int — we don't want to silently accept it.
        raise ConfigError(f"{where}: expected {width}-digit number, got bool")
    if isinstance(value, int):
        if value < 0 or value >= 10**width:
            raise ConfigError(f"{where}: {value} doesn't fit in {width} digit(s)")
        return str(value).zfill(width)
    s = str(value).strip()
    if not s.isdigit() or len(s) != width:
        raise ConfigError(f"{where}: expected exactly {width} digit(s), got {value!r}")
    return s


def _normalize_device(value: Any) -> str | None:
    """Treat None, empty string, or 'default' (any case) as 'use system default'."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "default":
        return None
    return s
