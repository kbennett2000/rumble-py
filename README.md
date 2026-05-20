# rumble-py

DTMF-controlled Mumble client for linking analog amateur radios over the internet.

A Python rewrite of an older C# Windows-only tool, now cross-platform and using
the Mumble protocol directly via [pymumble](https://github.com/azlux/pymumble)
(no need to run the Mumble desktop client).

> **Status:** alpha. Scaffold only — application logic not yet implemented.

## Quick start

```bash
# 1. Create a virtual environment and install the package in editable mode
#    with development dependencies.
python3.11 -m venv .venv
source .venv/bin/activate            # Linux/macOS
# .venv\Scripts\activate             # Windows PowerShell
pip install -e ".[dev]"

# 2. Bring up the local dev Mumble server (requires Docker).
cd docker && docker compose up -d && cd ..

# 3. Run the package (right now this just prints a version banner).
python -m rumble

# 4. Run the tests.
pytest
```

## License

MIT — see [LICENSE](LICENSE).
