# 0002. Pin pymumble==1.6.1 and install an ssl.wrap_socket shim for Python 3.12+

Date: 2026-05-28
Status: Accepted

## Context

pymumble 1.6.1, the current PyPI release, calls `ssl.wrap_socket()`. Python
3.12 removed that function entirely. The fix exists in pymumble's upstream
`master` branch but no release has been cut. The development machine runs
Python 3.12.3, and `pyproject.toml` targets Python 3.11+; blocking Python
3.12 was not acceptable.

## Decision

We install a shim at the top of `src/rumble/mumble_client.py` that
re-implements `ssl.wrap_socket()` using the modern `ssl.SSLContext` API. The
shim runs at module import time, before `import pymumble_py3`, and is guarded
by `if not hasattr(ssl, "wrap_socket")` so it is a no-op on Python 3.11 where
the original function still exists. pymumble is pinned to `==1.6.1` (exact,
not `>=`) in `pyproject.toml` so that a future PyPI release cannot silently
change the SSL wiring in ways that would break or conflict with the shim.

CLAUDE.md documents the exact steps to remove the shim once upstream ships a
fixed release.

## Alternatives considered

- **Fork pymumble and apply the upstream fix ourselves** — gives us a working
  package immediately but means we own maintenance of that fork indefinitely.
  The fix is one line; owning a fork to carry a one-line patch is too much
  ongoing overhead. Rejected.

- **Vendor pymumble into the repo** — similar to a fork but slightly less
  formal. Same problem: we'd be carrying dead weight once upstream ships.
  Rejected.

- **Block Python 3.12 and run on 3.11 only** — sidesteps the issue but
  narrows the target platform. Raspberry Pi OS (bookworm) ships Python 3.11,
  so this would have worked for the primary deployment target today, but it
  would create a cliff when distros eventually drop 3.11. Rejected.

- **Wait for pymumble to cut a release** — zero engineering effort, but
  upstream has not released since 2022 and the pace of activity on the project
  is low. Blocking the whole build on an unknown external timeline was not
  acceptable. Rejected.

## Consequences

What we gained:

- Python 3.12 and 3.13 compatibility without modifying the library on disk or
  forking anything.
- The exact-pin prevents a surprise API change from silently breaking the shim
  on a routine `pip install --upgrade`.

What we accepted:

- The shim is subtle: it lives above the `import pymumble_py3` line and the
  reason for that ordering must be understood by anyone editing the file. A
  comment explains it, but it is still a non-obvious pattern.
- If a future Python release changes the `ssl.SSLContext.wrap_socket` signature
  in a breaking way, our shim breaks too — but that scenario is unlikely given
  that `SSLContext.wrap_socket` is a well-established API.
- The exact pin on pymumble means `pip install --upgrade rumble-py` will not
  pick up a fixed pymumble release automatically; someone must notice the
  upstream release and bump the pin by hand.

## Revisit if

- pymumble cuts a release that removes the `ssl.wrap_socket()` call. At that
  point: delete the shim block from `mumble_client.py`, drop the `import ssl`
  if it is no longer used, bump the pinned version in `pyproject.toml`, and
  run the full integration suite (`RUMBLE_INTEGRATION=1 pytest -v -k mumble`)
  to confirm nothing regressed.
