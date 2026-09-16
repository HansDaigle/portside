# Local verification — 2026-09-15

Environment: macOS, Python 3.12.12, Caddy 2.11.4. Python dependencies are recorded in `uv.lock`.

Result: 60 tests passed; Ruff lint and formatting checks passed (2026-09-16). The initial source distribution and wheel built successfully; the wheel contained all dashboard assets and its installed CLI returned `Portside 0.1.0` in an isolated environment.

## Automated coverage

- Mapping validation, normalization, duplicate IDs/ports, and self-loop rejection.
- Atomic TOML round-trip, private file permissions, and exclusive manager lock.
- Actual Caddy forwarding: POST body, encoded path/query, upstream Host, Origin/Referer translation.
- Same-upstream redirect rewriting, unrelated redirects, multiple Set-Cookie headers, and event streaming.
- Concurrent mappings, text/binary WebSockets, stopping/restarting another mapping without disconnecting an active socket.
- Occupied ports, failed Caddy startup, process cleanup, and state reporting.
- Local HTTPS with a CA trusted only inside the test client; system trust remains untouched.
- Rejection of an untrusted HTTPS upstream certificate.
- Dashboard CRUD, persistence, validation, Host checks, and cross-origin/token rejection.
- Multi-mapping CLI startup, SIGTERM shutdown, listener cleanup, and lock release.
- Forced manager termination with two surviving Caddy processes, followed by dashboard recovery, fresh traffic metrics, and working stop controls. A different config sharing the same runtime directory remains untouched.
- SIGHUP cleanup (Terminal hangup), PID reuse/command/executable mismatch protection, and recovery when the manager exits before recording a child's PID.
- Browser launch after server readiness, existing-dashboard reuse for the same config, and manual URL fallback if browser opening fails.
- Bounded 60-second traffic history, weighted duration, error totals, malformed metric rejection, and reset on start.
- Project config round-trip and legacy loading, invalid/double membership, duplicate names, guarded CRUD, restart persistence, partial starts, repeated/concurrent starts, retry, and stop isolation.
- Project rename/membership changes keep an open WebSocket and proxy PID intact; project deletion keeps its connections. Crash recovery restores project running counts.
- Real Caddy access logs feed metrics with request fields, response headers, and user IDs removed at the source; test cookies and private query content do not appear in emitted entries.

Run with `uv run pytest` and `uv run ruff check .`.

## Browser checks

Verified in the Codex in-app browser:

- Empty dashboard, new mapping form, successful save, start and stop.
- Opened `http://localhost:4444` and received the real HTTPS example.com page.
- Edit dialog retained saved values; invalid destination path showed an inline error and preserved form data; correction saved successfully.
- Desktop layout inspected at the browser's actual widths (924px and 1280px).
- No unexpected console errors during the initial successful flow.
- Live request/duration graphs and activity details on the same page, using two controlled local upstreams. README screenshots show this demo traffic.
- Project dialog creation/assignment, occupied-port feedback (2/3 running), Retry to 3/3, rename and removal of a running member, group stop leaving Ungrouped running, and project deletion preserving all three connections. No console warnings or errors in this flow.

Metrics count completed requests, including streams and WebSockets only after they close. Durations measure the full request. Numeric aggregates stay in memory; traffic content is not retained.

## Remaining validation

- 375px/mobile layout: browser viewport override returned without changing the actual viewport. Responsive rules exist but this visual check remains open.
- Real website authentication, OAuth callbacks, parent-domain cookies, and origin-bound features need user-selected target sites.
- Browser/OS certificate trust setup is intentionally manual and has not been performed.
- Dashboard delete is covered through HTTP integration tests; its rendered confirmation dialog has not been exercised end to end.
- Distribution CI is a later milestone. Proxies created before ownership records existed need a one-time verified cleanup; they cannot be automatically attributed from a port number alone.
