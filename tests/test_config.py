# Tests for config loading and validation.

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from rumble.config import ConfigError, RumbleConfig, load_config


def _write_yaml(tmp_path: Path, text: str) -> Path:
    """Helper: dedent YAML and write to a tempfile, returning its path."""
    target = tmp_path / "config.yaml"
    target.write_text(dedent(text))
    return target


_BASIC = """\
    callsign: "AE9S"
    initial_bank: 0
    audio:
      input_device: null
      output_device: null
      sample_rate: 8000
      dtmf_min_magnitude: 0.05
    ident:
      wav_path: null
      interval_seconds: 600
    web:
      enabled: true
      host: "127.0.0.1"
      port: 8080
    banks:
      0:
        servers:
          - name: "local-dev"
            host: "127.0.0.1"
            port: 64738
            username: "AE9S"
        channels:
          - server_number: "001"
            channel_number: "1"
            server_ref: "local-dev"
            channel_path: "Root"
            nickname: "local lobby"
"""


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestLoadValidConfig:
    def test_full_config_round_trip(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, _BASIC))
        assert cfg.callsign == "AE9S"
        assert cfg.initial_bank == 0
        assert cfg.audio.input_device is None
        assert cfg.audio.sample_rate == 8000
        assert cfg.audio.dtmf_min_magnitude == 0.05
        assert cfg.ident.interval_seconds == 600
        assert cfg.web.host == "127.0.0.1"
        assert cfg.web.port == 8080

        bank = cfg.get_bank(0)
        assert bank.servers[0].name == "local-dev"
        assert bank.servers[0].host == "127.0.0.1"
        assert bank.channels[0].server_number == "001"
        assert bank.channels[0].channel_number == "1"
        assert bank.channels[0].channel_path == "Root"
        assert bank.channels[0].nickname == "local lobby"

    def test_audio_default_string_normalized_to_none(self, tmp_path: Path) -> None:
        text = _BASIC.replace("input_device: null", 'input_device: "default"')
        cfg = load_config(_write_yaml(tmp_path, text))
        assert cfg.audio.input_device is None

    def test_unquoted_integer_server_number_gets_zero_padded(self, tmp_path: Path) -> None:
        # YAML coerces unquoted `001` to integer 1. We re-pad it.
        text = _BASIC.replace('server_number: "001"', "server_number: 1")
        cfg = load_config(_write_yaml(tmp_path, text))
        assert cfg.get_bank(0).channels[0].server_number == "001"

    def test_multiple_banks(self, tmp_path: Path) -> None:
        # Two banks, both with their own server + a channel mapping.
        text = """\
callsign: "AE9S"
initial_bank: 0
audio: {}
ident: {}
web: {}
banks:
  0:
    servers:
      - name: "local"
        host: "127.0.0.1"
    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "local"
        channel_path: "Root"
        nickname: "local"
  1:
    servers:
      - name: "prod"
        host: "mumble.example.org"
    channels:
      - server_number: "002"
        channel_number: "5"
        server_ref: "prod"
        channel_path: "Root/Lobby"
        nickname: "prod lobby"
"""
        target = tmp_path / "config.yaml"
        target.write_text(text)
        cfg = load_config(target)
        assert set(cfg.banks.keys()) == {0, 1}
        assert cfg.get_bank(1).servers[0].host == "mumble.example.org"
        assert cfg.get_bank(1).channel_for("002", "5").nickname == "prod lobby"

    def test_example_config_loads(self) -> None:
        # Live example file at the repo root must always parse cleanly.
        repo_root = Path(__file__).resolve().parents[1]
        cfg = load_config(repo_root / "config.example.yaml")
        assert isinstance(cfg, RumbleConfig)
        assert cfg.callsign
        assert 0 in cfg.banks


# ---------------------------------------------------------------------------
# Validation failure modes (one test per failure)
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("callsign: [unterminated")
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_config(bad)

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        target = tmp_path / "list.yaml"
        target.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigError, match="must be a mapping"):
            load_config(target)

    def test_missing_callsign(self, tmp_path: Path) -> None:
        text = _BASIC.replace('callsign: "AE9S"', "")
        with pytest.raises(ConfigError, match="callsign"):
            load_config(_write_yaml(tmp_path, text))

    def test_empty_callsign(self, tmp_path: Path) -> None:
        text = _BASIC.replace('callsign: "AE9S"', 'callsign: ""')
        with pytest.raises(ConfigError, match="callsign"):
            load_config(_write_yaml(tmp_path, text))

    def test_missing_banks(self, tmp_path: Path) -> None:
        text = "callsign: AE9S\n"
        with pytest.raises(ConfigError, match="banks"):
            load_config(_write_yaml(tmp_path, text))

    def test_initial_bank_not_in_banks(self, tmp_path: Path) -> None:
        text = _BASIC.replace("initial_bank: 0", "initial_bank: 7")
        with pytest.raises(ConfigError, match="initial_bank"):
            load_config(_write_yaml(tmp_path, text))

    def test_duplicate_channel_mapping(self, tmp_path: Path) -> None:
        text = """\
callsign: "AE9S"
initial_bank: 0
audio: {}
ident: {}
web: {}
banks:
  0:
    servers:
      - name: "local-dev"
        host: "127.0.0.1"
    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root"
        nickname: "first"
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root/Other"
        nickname: "dup"
"""
        target = tmp_path / "config.yaml"
        target.write_text(text)
        with pytest.raises(ConfigError, match="duplicate channel mapping"):
            load_config(target)

    def test_channel_references_unknown_server(self, tmp_path: Path) -> None:
        text = _BASIC.replace('server_ref: "local-dev"', 'server_ref: "ghost"')
        with pytest.raises(ConfigError, match="unknown server"):
            load_config(_write_yaml(tmp_path, text))

    def test_server_number_too_long(self, tmp_path: Path) -> None:
        text = _BASIC.replace('server_number: "001"', 'server_number: "1234"')
        with pytest.raises(ConfigError, match="3 digit"):
            load_config(_write_yaml(tmp_path, text))

    def test_server_number_not_digits(self, tmp_path: Path) -> None:
        text = _BASIC.replace('server_number: "001"', 'server_number: "abc"')
        with pytest.raises(ConfigError, match="3 digit"):
            load_config(_write_yaml(tmp_path, text))

    def test_channel_number_not_single_digit(self, tmp_path: Path) -> None:
        text = _BASIC.replace('channel_number: "1"', 'channel_number: "12"')
        with pytest.raises(ConfigError, match="1 digit"):
            load_config(_write_yaml(tmp_path, text))

    def test_duplicate_server_name_in_bank(self, tmp_path: Path) -> None:
        text = """\
callsign: "AE9S"
initial_bank: 0
audio: {}
ident: {}
web: {}
banks:
  0:
    servers:
      - name: "local-dev"
        host: "127.0.0.1"
      - name: "local-dev"
        host: "127.0.0.2"
    channels:
      - server_number: "001"
        channel_number: "1"
        server_ref: "local-dev"
        channel_path: "Root"
        nickname: "x"
"""
        target = tmp_path / "config.yaml"
        target.write_text(text)
        with pytest.raises(ConfigError, match="duplicate server"):
            load_config(target)

    def test_bank_number_must_be_int(self, tmp_path: Path) -> None:
        text = _BASIC.replace("banks:\n      0:", "banks:\n      not-a-number:")
        with pytest.raises(ConfigError, match="must be an integer"):
            load_config(_write_yaml(tmp_path, text))

    def test_get_bank_missing_raises(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, _BASIC))
        with pytest.raises(ConfigError, match="bank 9"):
            cfg.get_bank(9)
