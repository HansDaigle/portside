# Portside

Work in this checkout and current branch.

- Product scope: `docs/PLAN.md`; browser limits: `docs/BROWSER-COMPATIBILITY.md`.
- Python: `src/portside`; bundled dashboard: `src/portside/static`.
- Run: `uv run portside ui`. Verify: `uv run pytest` and `uv run ruff check .`.
- Keep CLI and UI on the same model, storage, and process manager.
- Keep listeners on loopback and Caddy's admin API disabled.
- Never install certificate trust or change system DNS automatically.
- Never commit personal mappings, credentials, certificate keys, or runtime files.
