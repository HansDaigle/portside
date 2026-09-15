# Portside implementation plan

## Goal

Choose a local port and remote domain, then use that destination through the local URL. Support a quick CLI, multiple saved mappings, a small dashboard, WebSockets, and explicit browser compatibility options.

Repository: [HansDaigle/portside](https://github.com/HansDaigle/portside).

## Architecture and decisions

- Python 3.11+ CLI and aiohttp control server; bundled HTML/CSS/JavaScript dashboard with no frontend build step, remote fonts, or analytics.
- Caddy owns HTTP parsing, TLS, streaming, and WebSockets. Python owns validation, persistence, lifecycle, and UI endpoints.
- One Caddy child per mapping. Editing requires stopping that mapping; other mappings stay connected.
- TOML is the shared format. Atomic writes and a process lock prevent accidental overwrites by multiple managers.
- Bind only to `127.0.0.1`. Default hostname: `localhost`. Optional individual `.localhost` hostnames support separate browser sessions.
- HTTP locally by default; optional local HTTPS with explicit trust setup. No automatic trust-store or DNS changes.
- Dashboard mutations require a same-origin request and session token. Validate Host to prevent DNS rebinding. Do not log traffic bodies, cookies, or authorization headers.
- Clean shutdown, occupied-port checks, dependency checks, and understandable startup errors.

## Milestone 1 — Usable local foundation (current)

- [x] Package, CLI help, dependency checks, setup instructions.
- [x] Validated mapping model and atomic TOML storage.
- [x] One-off and multi-mapping foreground CLI modes.
- [x] Caddy start/stop, startup failure detection, bounded lifecycle logs.
- [x] Dashboard: list, create, edit, delete, start, stop, filter, open local URL.
- [x] Single-page dashboard with per-connection request and duration graphs, activity dialogs, and README screenshots.
- [x] HTTP/HTTPS upstreams, custom ports, redirects, streaming, WebSockets.
- [x] Optional local HTTPS, exact-domain cookie translation, origin translation.
- [x] Integration tests against a controlled upstream through actual Caddy.
- [x] Desktop browser verification: create, start, stop, edit, validation feedback, and public HTTPS destination through localhost.
- [ ] Narrow-width browser verification. Responsive CSS is implemented; the in-app browser's viewport override did not apply during this run.

## Milestone 2 — Browser compatibility

- [ ] Select one or two real target sites with the user and test login flows.
- [ ] Identify OAuth callback registrations and multi-domain dependencies.
- [ ] Verify browser certificate trust setup and isolated hostnames.
- [ ] Add per-site adapters only where a tested example justifies them.
- [ ] Document origin-bound features that cannot be made transparent.

Compatibility is site-specific. Arbitrary public-site logins cannot be guaranteed through a changed browser origin. Do not globally disable browser protections or rewrite every string in HTML/JavaScript.

## Milestone 3 — Distribution

- [x] Double-clickable macOS Terminal launcher with browser opening and existing-dashboard reuse.
- [ ] Installation/upgrade smoke tests.
- [ ] Background service lifecycle if requested; no login item in v0.1.
- [ ] CI, release packaging, and license selection.
- [x] Recover proxies left behind by an interrupted dashboard using private ownership records and verified process identity; handle Terminal hangup gracefully.
- [ ] Dependency compatibility policy.

## Verification gates

1. Reject malformed destinations, embedded credentials, invalid/duplicate ports, bad TOML, and untrusted UI mutations.
2. Forward encoded paths/queries, methods, bodies, status, and content through actual Caddy.
3. Test same-upstream and unrelated redirects, cookies, and WebSocket echo/close.
4. Check concurrent mappings, occupied ports, stop/restart, and cleanup.
5. Exercise add/edit/start/stop/delete in the rendered UI, with useful errors and preserved form values.

## Design

Compact utility dashboard: dark slate, green actions, restrained borders, system fonts, monospaced endpoints, and responsive rows. Status uses text and color. Forms have labels and inline errors; dialogs support keyboard focus and reduced motion.


## Local progress — 2026-09-15

The first version is implemented, including live graphs, generated branding, and a macOS launcher. See `VERIFICATION.md` for checks and remaining gaps. Examples use generic destinations and local demo traffic.
