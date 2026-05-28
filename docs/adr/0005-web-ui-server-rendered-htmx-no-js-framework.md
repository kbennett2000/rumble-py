# 0005. Web UI: server-rendered HTML with HTMX, no JavaScript framework, no build step

Date: 2026-05-28
Status: Accepted

## Context

rumble-py needs a local web UI so an operator can monitor connection status,
send DTMF commands from a browser, tail the application log, and reload config
without restarting the process. The UI is accessed on localhost (127.0.0.1
by default) from the same machine or a laptop on the shack LAN.

The project already runs FastAPI for its web layer. The question was what to
put in front of it: a JavaScript SPA framework (React, Vue, Svelte), a minimal
hypermedia library (HTMX), or plain HTML forms.

The target deployment is an embedded Linux system with limited CPU. The
operator is a radio hobbyist, not a web developer. The UI has to be
maintainable by someone whose primary language is Python, with no Node.js
toolchain installed on the server.

## Decision

The web UI is built from:

- **FastAPI + Jinja2** for server-side HTML rendering.
- **HTMX** (loaded from CDN, pinned to a specific version with a Subresource
  Integrity hash) for in-place partial updates without writing JavaScript.
- **Server-Sent Events** (SSE) for the streaming log tail.
- No JavaScript framework, no bundler, no `node_modules`, no build step.

The web layer (`src/rumble/web/app.py`) never imports pymumble, sounddevice,
or numpy. It only reads the dispatcher's public API, so the web UI has no
direct dependency on the audio or Mumble subsystems.

## Alternatives considered

- **React / Vue / Svelte SPA** — full client-side rendering, rich interactivity.
  Requires Node.js, npm, a bundler, and a build pipeline that produces static
  assets. None of that exists on the target hardware. It also significantly
  increases the maintenance surface: a Python developer maintaining a React
  frontend is context-switching between two ecosystems. For a shack monitoring
  UI with about six interactive controls, the complexity is not justified.
  Rejected.

- **Plain HTML forms with full-page reloads** — no JavaScript at all. Works,
  but every button press reloads the entire page including the log tail and the
  connection status panel. The operator loses their scroll position in the log
  on every action. Acceptable for a prototype; not acceptable as the shipped UI.
  Rejected.

- **WebSockets for bidirectional real-time updates** — would allow the server
  to push state changes as they happen rather than relying on HTMX polling.
  More complex to implement (async context, connection management) and more
  brittle to network interruptions between the Pi and the browser. SSE is
  unidirectional and simpler; HTMX polling covers the status panel. Rejected
  in favor of SSE for the log and polling for status.

## Consequences

What we gained:

- Zero JavaScript to write or maintain. The dynamic behavior (partial DOM swaps,
  log streaming) is expressed entirely in HTML attributes (`hx-post`,
  `hx-target`, `hx-swap`).
- No build step on the server. Deploying the web UI is `pip install -e .` —
  nothing more.
- The web layer is easy to reason about: each route renders a Jinja2 template
  from a snapshot of dispatcher state. No client-side state to reconcile.
- HTMX is a single script file. The SRI hash ensures the browser rejects a
  tampered or outdated version.

What we accepted:

- HTMX is loaded from a CDN. An operator running with no internet access (not
  uncommon in a shack that is air-gapped from the internet) will see a broken
  UI unless the CDN script is vendored locally. This is a known gap.
- The SRI hash is pinned to a specific HTMX release. Upgrading HTMX requires
  manually updating the hash in every template that references it. Forgetting
  to update the hash causes browsers to block the script silently.
- HTMX polling (for the status partial) sends HTTP requests on a fixed interval
  regardless of whether anything has changed. On a localhost connection the
  overhead is negligible, but it is not zero.
- The UI has no CSRF protection on its POST endpoints. The current default
  (`web.host: 127.0.0.1`) limits exposure to localhost, which is the accepted
  mitigation. The issue is tracked in `docs/issues-to-file.md`. The UI must
  not be exposed on `0.0.0.0` on untrusted networks without adding CSRF tokens.

## Revisit if

- The web UI needs to support real-time bidirectional interaction that SSE +
  HTMX polling cannot express cleanly (e.g., a live audio level meter that
  updates at 10 Hz). At that point, add a WebSocket endpoint for that specific
  widget rather than replacing the whole UI approach.
- HTMX is abandoned or falls significantly behind browser standards. Evaluate
  vendoring the pinned version directly into `src/rumble/web/static/` rather
  than switching to a framework.
