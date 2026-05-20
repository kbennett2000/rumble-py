# DTMF detector and command-sequence state machine — the brain of rumble-py.
#
# Pure logic: no audio, no I/O. Feed DTMF characters in one at a time;
# get Command instances out the other end.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final

# ---------------------------------------------------------------------------
# Command types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Disconnect:
    """Disconnect from the current Mumble channel.

    Emitted by the DTMF sequence ``#*``.
    """


@dataclass(frozen=True)
class LoadConfig:
    """Load a stored configuration bank.

    Emitted by the DTMF sequence ``#N*``.

    Attributes:
        bank: The bank number, a single decimal digit 0-9.
    """

    bank: int


@dataclass(frozen=True)
class AdminSetting:
    """Change an administrative setting.

    Emitted by the DTMF sequence ``#XX#Y*``.

    Attributes:
        setting: The two-digit setting code (``"00"``-``"99"``). Stored as a
            string so that leading zeros are preserved — setting ``"00"`` is
            a different setting than ``"0"``.
        value: The single-digit new value (``"0"``-``"9"``). Stored as a
            string for symmetry with `setting`.
    """

    setting: str
    value: str


@dataclass(frozen=True)
class ChangeChannel:
    """Change to a specific server/channel.

    Emitted by the DTMF sequence ``#XXX#Y*``.

    Attributes:
        server: The three-digit server code (``"000"``-``"999"``). Stored as
            a string to preserve leading zeros — they are significant when
            indexing into the config's channel map.
        channel: The single-digit channel number (``"0"``-``"9"``). Stored
            as a string for symmetry with `server`.
    """

    server: str
    channel: str


Command = Disconnect | LoadConfig | AdminSetting | ChangeChannel


# ---------------------------------------------------------------------------
# Internal state machine
# ---------------------------------------------------------------------------


class _State(Enum):
    """States in the DTMF command-recognition state machine.

    Renamed from the original C# `DTMFCommandStates` enum for readability;
    the structural correspondence is roughly:

    ====================================  ==========================================
    C#                                    Python
    ====================================  ==========================================
    ignore                                IDLE
    isCommand                             AFTER_HASH
    isNotDisconnect                       AFTER_HASH_DIGIT
    isAdminSettingORChannelChange         AFTER_TWO_DIGITS
    isAdminSetting                        ADMIN_VALUE_NEEDED
    isChangeChannel                       CHANNEL_SEPARATOR_NEEDED
    isAdminSettingNotFinal                ADMIN_TERMINATOR_NEEDED
    isChannelChangeNoChannelNumber        CHANNEL_VALUE_NEEDED
    isChannelChangeNotFinal               CHANNEL_TERMINATOR_NEEDED
    ====================================  ==========================================
    """

    IDLE = auto()
    AFTER_HASH = auto()  # got '#'
    AFTER_HASH_DIGIT = auto()  # got '#N'  — admin/channel prefix or LoadConfig pending
    AFTER_TWO_DIGITS = auto()  # got '#NN'  — admin or channel?
    ADMIN_VALUE_NEEDED = auto()  # got '#XX#'
    ADMIN_TERMINATOR_NEEDED = auto()  # got '#XX#Y'
    CHANNEL_SEPARATOR_NEEDED = auto()  # got '#XXX'
    CHANNEL_VALUE_NEEDED = auto()  # got '#XXX#'
    CHANNEL_TERMINATOR_NEEDED = auto()  # got '#XXX#Y'


# Long-form names from the original C# version → canonical single-char DTMF.
# Casing matters here (only the capitalized forms are accepted); for single
# characters the input is upper-cased before lookup, so "a" → "A" works.
_LONG_FORM_NAMES: Final[dict[str, str]] = {
    "Zero": "0",
    "One": "1",
    "Two": "2",
    "Three": "3",
    "Four": "4",
    "Five": "5",
    "Six": "6",
    "Seven": "7",
    "Eight": "8",
    "Nine": "9",
    "Star": "*",
    "Hash": "#",
    "Pound": "#",
    "A": "A",
    "B": "B",
    "C": "C",
    "D": "D",
}

_DIGITS: Final[frozenset[str]] = frozenset("0123456789")
_VALID_CHARS: Final[frozenset[str]] = frozenset("0123456789*#ABCD")


def _normalize(char: object) -> str | None:
    """Map various input forms to a single canonical DTMF character.

    Args:
        char: Either a single-character string from the DTMF alphabet
            (``0``-``9``, ``*``, ``#``, ``A``-``D``, case-insensitive) or one
            of the long-form names defined in `_LONG_FORM_NAMES`
            (``"Hash"``, ``"Star"``, ``"Zero"`` … ``"Nine"``, ``"A"`` …
            ``"D"``, ``"Pound"`` as an alias for ``"Hash"``).

    Returns:
        A one-character string in the DTMF alphabet, or ``None`` if the input
        is empty, non-string, or unrecognized.
    """
    if not isinstance(char, str) or not char:
        return None
    if len(char) == 1:
        upper = char.upper()
        return upper if upper in _VALID_CHARS else None
    return _LONG_FORM_NAMES.get(char)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DtmfStateMachine:
    """Recognize DTMF command sequences fed one character at a time.

    Grammar::

        DISCONNECT      ::= '#' '*'
        LOAD_CONFIG     ::= '#' digit '*'
        ADMIN_SETTING   ::= '#' digit digit '#' digit '*'
        CHANGE_CHANNEL  ::= '#' digit digit digit '#' digit '*'

    The state machine consumes input via :meth:`feed`, one character per call,
    and returns a :class:`Command` instance the moment a complete sequence is
    recognized.

    **Input formats accepted:**

    * Single characters from the DTMF alphabet: ``"0"``-``"9"``, ``"*"``,
      ``"#"``, ``"A"``-``"D"``. Letter case is normalized — ``"a"`` and
      ``"A"`` are equivalent.
    * Long-form names used by the original C# version: ``"Hash"``, ``"Star"``,
      ``"Zero"``-``"Nine"``, ``"A"``-``"D"``, plus ``"Pound"`` as a synonym
      for ``"Hash"``. These are matched case-sensitively — ``"five"`` is
      *not* the same as ``"Five"``, and the lowercase form is rejected. This
      mirrors the way the original C# code emitted them.

    **Invalid input** — empty strings, ``None``, unknown long-form names, and
    characters outside the DTMF alphabet — is treated as a fumbled keypress:
    the machine silently resets to idle and returns ``None``. No exceptions
    are raised for invalid input; this is a normal operating condition for a
    radio operator.

    Stray valid characters received while the machine is idle (i.e., before
    any ``#`` has been pressed) are also silently ignored — the machine
    simply stays idle.
    """

    def __init__(self) -> None:
        self._state: _State = _State.IDLE
        self._buffer: str = ""

    # ----- read-only properties ------------------------------------------

    @property
    def current_buffer(self) -> str:
        """The DTMF characters consumed so far in the in-progress command.

        Empty when the machine is idle. Cleared automatically when a command
        is emitted or when :meth:`reset` is called.
        """
        return self._buffer

    @property
    def is_idle(self) -> bool:
        """``True`` when no command is currently being assembled."""
        return self._state is _State.IDLE

    # ----- mutators ------------------------------------------------------

    def reset(self) -> None:
        """Discard any in-progress command and return to the idle state."""
        self._state = _State.IDLE
        self._buffer = ""

    def feed(self, char: str) -> Command | None:
        """Feed one DTMF character into the state machine.

        Args:
            char: A DTMF character — see the class docstring for accepted
                input formats.

        Returns:
            A :class:`Command` (``Disconnect``, ``LoadConfig``,
            ``AdminSetting``, or ``ChangeChannel``) when the character just
            consumed completed a valid command sequence. ``None`` otherwise.

            Invalid input also returns ``None`` and silently resets the
            machine — no exception is raised.
        """
        c = _normalize(char)
        if c is None:
            self.reset()
            return None
        return self._step(c)

    # ----- internals -----------------------------------------------------

    def _step(self, c: str) -> Command | None:
        """Advance the state machine by one normalized character.

        The character `c` is guaranteed by `_normalize` to be a single
        uppercase DTMF character. Each branch handles one state.
        """
        match self._state:
            case _State.IDLE:
                # Only '#' starts a command. Other valid DTMF characters
                # arriving in idle are silently dropped (operator noise).
                if c == "#":
                    self._buffer = "#"
                    self._state = _State.AFTER_HASH
                return None

            case _State.AFTER_HASH:
                if c == "*":
                    self.reset()
                    return Disconnect()
                if c in _DIGITS:
                    self._buffer += c
                    self._state = _State.AFTER_HASH_DIGIT
                    return None
                self.reset()
                return None

            case _State.AFTER_HASH_DIGIT:
                if c == "*":
                    # LoadConfig: '#N*' is terminal. Buffer is "#N" — the bank
                    # digit lives at index 1. This matches the original C#
                    # behavior (isLoadConfig is reached after three keypresses,
                    # not four).
                    bank = int(self._buffer[1])
                    self.reset()
                    return LoadConfig(bank=bank)
                if c in _DIGITS:
                    self._buffer += c
                    self._state = _State.AFTER_TWO_DIGITS
                    return None
                self.reset()
                return None

            case _State.AFTER_TWO_DIGITS:
                if c == "#":
                    # Admin path: '#XX#'
                    self._buffer += c
                    self._state = _State.ADMIN_VALUE_NEEDED
                    return None
                if c in _DIGITS:
                    # Channel path: '#XXX'
                    self._buffer += c
                    self._state = _State.CHANNEL_SEPARATOR_NEEDED
                    return None
                self.reset()
                return None

            case _State.ADMIN_VALUE_NEEDED:
                if c in _DIGITS:
                    self._buffer += c
                    self._state = _State.ADMIN_TERMINATOR_NEEDED
                    return None
                self.reset()
                return None

            case _State.ADMIN_TERMINATOR_NEEDED:
                if c == "*":
                    # Buffer is "#XX#Y": setting at [1:3], value at [4].
                    setting = self._buffer[1:3]
                    value = self._buffer[4]
                    self.reset()
                    return AdminSetting(setting=setting, value=value)
                self.reset()
                return None

            case _State.CHANNEL_SEPARATOR_NEEDED:
                if c == "#":
                    self._buffer += c
                    self._state = _State.CHANNEL_VALUE_NEEDED
                    return None
                self.reset()
                return None

            case _State.CHANNEL_VALUE_NEEDED:
                if c in _DIGITS:
                    self._buffer += c
                    self._state = _State.CHANNEL_TERMINATOR_NEEDED
                    return None
                self.reset()
                return None

            case _State.CHANNEL_TERMINATOR_NEEDED:
                if c == "*":
                    # Buffer is "#XXX#Y": server at [1:4], channel at [5].
                    server = self._buffer[1:4]
                    channel = self._buffer[5]
                    self.reset()
                    return ChangeChannel(server=server, channel=channel)
                self.reset()
                return None
