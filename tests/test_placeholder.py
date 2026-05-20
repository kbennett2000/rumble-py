# Trivial sanity test so `pytest` has at least one passing test to discover.

from rumble import __version__


def test_version_is_set() -> None:
    assert __version__ == "0.1.0"
