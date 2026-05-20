# Entry point — runs when the user executes `python -m rumble`.

from rumble import __version__


def main() -> None:
    """Print a version banner. Real application wiring lands in a later milestone."""
    print(f"rumble-py v{__version__}")


if __name__ == "__main__":
    main()
