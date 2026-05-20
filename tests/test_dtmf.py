# Unit tests for the DTMF command-recognition state machine.

from __future__ import annotations

import pytest

from rumble.dtmf import (
    AdminSetting,
    ChangeChannel,
    Command,
    Disconnect,
    DtmfStateMachine,
    LoadConfig,
)


def feed_str(machine: DtmfStateMachine, sequence: str) -> list[Command]:
    """Feed each character of `sequence` one at a time; collect emitted commands."""
    emitted: list[Command] = []
    for ch in sequence:
        result = machine.feed(ch)
        if result is not None:
            emitted.append(result)
    return emitted


# ---------------------------------------------------------------------------
# Happy paths — one test per command type
# ---------------------------------------------------------------------------


class TestHappyPaths:
    def test_disconnect(self) -> None:
        m = DtmfStateMachine()
        assert m.feed("#") is None
        assert m.feed("*") == Disconnect()

    @pytest.mark.parametrize(
        ("sequence", "bank"),
        [("#0*", 0), ("#5*", 5), ("#9*", 9)],
    )
    def test_load_config(self, sequence: str, bank: int) -> None:
        m = DtmfStateMachine()
        assert feed_str(m, sequence) == [LoadConfig(bank=bank)]

    @pytest.mark.parametrize(
        ("sequence", "setting", "value"),
        [
            ("#00#0*", "00", "0"),
            ("#01#1*", "01", "1"),
            ("#03#1*", "03", "1"),
            ("#42#7*", "42", "7"),
            ("#99#9*", "99", "9"),
        ],
    )
    def test_admin_setting(self, sequence: str, setting: str, value: str) -> None:
        m = DtmfStateMachine()
        assert feed_str(m, sequence) == [AdminSetting(setting=setting, value=value)]

    @pytest.mark.parametrize(
        ("sequence", "server", "channel"),
        [
            ("#001#2*", "001", "2"),
            ("#123#0*", "123", "0"),
            ("#456#5*", "456", "5"),
            ("#999#9*", "999", "9"),
        ],
    )
    def test_change_channel(self, sequence: str, server: str, channel: str) -> None:
        m = DtmfStateMachine()
        assert feed_str(m, sequence) == [ChangeChannel(server=server, channel=channel)]


# ---------------------------------------------------------------------------
# Buffer state
# ---------------------------------------------------------------------------


class TestBufferState:
    def test_buffer_grows_as_chars_arrive(self) -> None:
        m = DtmfStateMachine()
        progression: list[tuple[str, str]] = [
            ("#", "#"),
            ("0", "#0"),
            ("0", "#00"),
            ("#", "#00#"),
            ("3", "#00#3"),
        ]
        for ch, expected in progression:
            m.feed(ch)
            assert m.current_buffer == expected

    def test_buffer_clears_when_command_emits(self) -> None:
        m = DtmfStateMachine()
        for ch in "#001#2":
            m.feed(ch)
        assert m.current_buffer == "#001#2"
        m.feed("*")
        assert m.current_buffer == ""

    def test_buffer_clears_on_reset(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        m.feed("0")
        m.reset()
        assert m.current_buffer == ""

    def test_buffer_clears_on_invalid_input(self) -> None:
        m = DtmfStateMachine()
        for ch in "#00#0":
            m.feed(ch)
        m.feed("B")  # invalid at this position
        assert m.current_buffer == ""


# ---------------------------------------------------------------------------
# Reset behavior
# ---------------------------------------------------------------------------


class TestResetBehavior:
    def test_explicit_reset_mid_command(self) -> None:
        m = DtmfStateMachine()
        for ch in "#00#0":
            m.feed(ch)
        assert not m.is_idle
        m.reset()
        assert m.is_idle
        assert m.current_buffer == ""

    def test_invalid_char_resets_then_fresh_hash_starts_new_command(self) -> None:
        m = DtmfStateMachine()
        for ch in "#00#0":
            m.feed(ch)
        assert m.feed("B") is None
        assert m.is_idle
        assert m.feed("#") is None
        assert m.feed("*") == Disconnect()

    def test_partial_command_abandoned_then_full_command_succeeds(self) -> None:
        # Operator starts an admin command, fumbles, starts a new channel command.
        m = DtmfStateMachine()
        emitted = feed_str(m, "#00B#001#2*")
        assert emitted == [ChangeChannel(server="001", channel="2")]


# ---------------------------------------------------------------------------
# is_idle property
# ---------------------------------------------------------------------------


class TestIsIdleProperty:
    def test_idle_at_start(self) -> None:
        assert DtmfStateMachine().is_idle

    def test_not_idle_mid_command(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        assert not m.is_idle

    def test_idle_after_command_emits(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        m.feed("*")
        assert m.is_idle

    def test_idle_after_reset(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        m.feed("0")
        m.reset()
        assert m.is_idle


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


class TestInputNormalization:
    def test_long_form_disconnect(self) -> None:
        m = DtmfStateMachine()
        assert m.feed("Hash") is None
        assert m.feed("Star") == Disconnect()

    def test_long_form_load_config(self) -> None:
        m = DtmfStateMachine()
        last: Command | None = None
        for token in ("Hash", "Five", "Star"):
            last = m.feed(token)
        assert last == LoadConfig(bank=5)

    def test_long_form_admin_setting(self) -> None:
        m = DtmfStateMachine()
        last: Command | None = None
        for token in ("Hash", "Zero", "Three", "Hash", "One", "Star"):
            last = m.feed(token)
        assert last == AdminSetting(setting="03", value="1")

    def test_long_form_change_channel(self) -> None:
        m = DtmfStateMachine()
        last: Command | None = None
        for token in ("Hash", "One", "Two", "Three", "Hash", "Zero", "Star"):
            last = m.feed(token)
        assert last == ChangeChannel(server="123", channel="0")

    def test_pound_is_synonym_for_hash(self) -> None:
        m = DtmfStateMachine()
        m.feed("Pound")
        assert m.feed("Star") == Disconnect()

    def test_single_char_letters_are_case_insensitive(self) -> None:
        # "a" should normalize to "A", which is a valid DTMF char (though
        # invalid as the second character of a command — so this just
        # exercises the normalization path).
        m = DtmfStateMachine()
        m.feed("#")
        m.feed("a")  # normalizes to "A"; invalid after '#', should reset
        assert m.is_idle

    @pytest.mark.parametrize(
        "token",
        [
            "Banana",  # not a known name
            "Zer0",  # close, but wrong
            "five",  # lowercase long-form is rejected
            "hash",  # lowercase long-form is rejected
            "HASH",  # uppercase long-form is rejected
            "  ",  # whitespace
            "??",  # punctuation
            "5x",  # multi-char with a valid digit prefix
        ],
    )
    def test_unknown_long_form_resets(self, token: str) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        assert m.feed(token) is None
        assert m.is_idle


# ---------------------------------------------------------------------------
# Back-to-back command sequences
# ---------------------------------------------------------------------------


class TestSequences:
    def test_three_commands_back_to_back(self) -> None:
        m = DtmfStateMachine()
        emitted = feed_str(m, "#001#2*#00#1*#*")
        assert emitted == [
            ChangeChannel(server="001", channel="2"),
            AdminSetting(setting="00", value="1"),
            Disconnect(),
        ]

    def test_two_disconnects_in_a_row(self) -> None:
        m = DtmfStateMachine()
        assert feed_str(m, "#*#*") == [Disconnect(), Disconnect()]

    def test_load_config_then_change_channel(self) -> None:
        m = DtmfStateMachine()
        emitted = feed_str(m, "#7*#234#5*")
        assert emitted == [
            LoadConfig(bank=7),
            ChangeChannel(server="234", channel="5"),
        ]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.parametrize("stray", list("0123456789*ABCD"))
    def test_chars_before_hash_are_silently_ignored(self, stray: str) -> None:
        m = DtmfStateMachine()
        assert m.feed(stray) is None
        assert m.is_idle
        assert m.current_buffer == ""

    def test_none_input_when_idle(self) -> None:
        m = DtmfStateMachine()
        assert m.feed(None) is None  # type: ignore[arg-type]
        assert m.is_idle

    def test_none_input_mid_command_resets(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        m.feed("0")
        assert m.feed(None) is None  # type: ignore[arg-type]
        assert m.is_idle
        assert m.current_buffer == ""

    def test_empty_string_when_idle(self) -> None:
        m = DtmfStateMachine()
        assert m.feed("") is None
        assert m.is_idle

    def test_empty_string_mid_command_resets(self) -> None:
        m = DtmfStateMachine()
        m.feed("#")
        assert m.feed("") is None
        assert m.is_idle

    def test_disconnect_after_two_digits_not_recognized(self) -> None:
        # '#00*' is not a valid command. The trailing '*' is invalid in
        # AFTER_TWO_DIGITS state and must reset.
        m = DtmfStateMachine()
        assert feed_str(m, "#00*") == []
        assert DtmfStateMachine().is_idle  # sanity

    def test_admin_value_must_be_digit(self) -> None:
        # After '#00#' the next char must be a digit. '*' is not.
        m = DtmfStateMachine()
        for ch in "#00#":
            m.feed(ch)
        assert m.feed("*") is None
        assert m.is_idle

    def test_admin_terminator_must_be_star(self) -> None:
        # After '#00#1' the next char must be '*'. A digit is not.
        m = DtmfStateMachine()
        for ch in "#00#1":
            m.feed(ch)
        assert m.feed("5") is None
        assert m.is_idle

    def test_channel_separator_must_be_hash(self) -> None:
        # After '#001' the next char must be '#'. '*' is not.
        m = DtmfStateMachine()
        for ch in "#001":
            m.feed(ch)
        assert m.feed("*") is None
        assert m.is_idle

    def test_channel_value_must_be_digit(self) -> None:
        # After '#001#' the next char must be a digit. '*' is not.
        m = DtmfStateMachine()
        for ch in "#001#":
            m.feed(ch)
        assert m.feed("*") is None
        assert m.is_idle

    def test_channel_terminator_must_be_star(self) -> None:
        # After '#001#2' the next char must be '*'. A digit is not.
        m = DtmfStateMachine()
        for ch in "#001#2":
            m.feed(ch)
        assert m.feed("5") is None
        assert m.is_idle

    def test_stray_star_after_load_config_emit_is_noop(self) -> None:
        # '#N*' emits LoadConfig and returns to idle. A '*' arriving next is
        # then a stray idle-state character — it must be silently dropped,
        # not interpreted as part of a follow-on command. This is a
        # regression guard for the grammar change from '#N**' to '#N*'.
        m = DtmfStateMachine()
        assert feed_str(m, "#5*") == [LoadConfig(bank=5)]
        assert m.is_idle
        assert m.feed("*") is None
        assert m.is_idle
        assert m.current_buffer == ""

    @pytest.mark.parametrize(
        "prefix",
        # Note: "#0*" is intentionally absent — under the corrected '#N*'
        # grammar that sequence emits LoadConfig and returns to idle, so it
        # is no longer a mid-command position.
        ["#", "#0", "#00", "#00#", "#00#0", "#001", "#001#", "#001#2"],
    )
    def test_letter_resets_at_every_non_idle_position(self, prefix: str) -> None:
        m = DtmfStateMachine()
        for ch in prefix:
            m.feed(ch)
        assert m.feed("B") is None
        assert m.is_idle, f"machine did not reset after prefix {prefix!r} + 'B'"

    def test_command_dataclasses_are_frozen(self) -> None:
        # Frozen dataclasses are hashable, which is handy for set/dict membership.
        assert {Disconnect(), Disconnect()} == {Disconnect()}
        assert LoadConfig(bank=3) == LoadConfig(bank=3)
        assert AdminSetting("00", "1") != AdminSetting("0", "1")  # leading zero matters
        assert ChangeChannel("001", "2") != ChangeChannel("01", "2")
