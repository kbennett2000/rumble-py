# Unit tests for parse_mumble_uri.

from __future__ import annotations

import pytest

from rumble.mumble_client import parse_mumble_uri


class TestParseMumbleUri:
    def test_user_at_host(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com")
        assert result == {
            "host": "example.com",
            "port": 64738,
            "username": "alice",
        }

    def test_user_at_host_with_port(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com:9999")
        assert result["host"] == "example.com"
        assert result["port"] == 9999
        assert isinstance(result["port"], int)

    def test_user_pass_host_port(self) -> None:
        result = parse_mumble_uri("mumble://alice:secret@example.com:64738")
        assert result == {
            "host": "example.com",
            "port": 64738,
            "username": "alice",
            "password": "secret",
        }

    def test_user_host_channel(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com/Root/General")
        assert result["channel_path"] == "Root/General"

    def test_user_pass_host_port_channel_with_spaces(self) -> None:
        result = parse_mumble_uri("mumble://alice:secret@example.com:64738/Root/Some%20Channel")
        assert result["channel_path"] == "Root/Some Channel"

    def test_url_encoded_username(self) -> None:
        result = parse_mumble_uri("mumble://alice%2Fbob@example.com")
        assert result["username"] == "alice/bob"

    def test_url_encoded_password(self) -> None:
        result = parse_mumble_uri("mumble://alice:p%40ssword@example.com")
        assert result["password"] == "p@ssword"

    def test_default_port(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com")
        assert result["port"] == 64738

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="not a mumble URI"):
            parse_mumble_uri("https://alice@example.com")

    def test_missing_host_raises(self) -> None:
        with pytest.raises(ValueError, match="missing host"):
            parse_mumble_uri("mumble://")

    def test_missing_username_raises(self) -> None:
        with pytest.raises(ValueError, match="missing username"):
            parse_mumble_uri("mumble://example.com")

    def test_no_password_when_omitted(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com")
        assert "password" not in result

    def test_no_channel_when_omitted(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com")
        assert "channel_path" not in result

    def test_root_only_channel(self) -> None:
        result = parse_mumble_uri("mumble://alice@example.com/Root")
        assert result["channel_path"] == "Root"

    def test_trailing_slash_only(self) -> None:
        # A bare "/" path is equivalent to no path.
        result = parse_mumble_uri("mumble://alice@example.com/")
        assert "channel_path" not in result

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_mumble_uri("")

    def test_garbage_input_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_mumble_uri("not a uri at all")
