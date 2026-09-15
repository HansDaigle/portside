import asyncio
import hmac
import json
import secrets
import webbrowser
from pathlib import Path

from aiohttp import web

from . import __version__
from .engine import Manager
from .models import Mapping, PortsideError
from .storage import Store

STATIC = Path(__file__).parent / "static"


def create_app(config_path, port=9876, state_dir=None):
    store = Store(config_path)
    manager = Manager(store.load(), store=store, state_dir=state_dir, reserved_ports={port})
    token = secrets.token_urlsafe(32)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @web.middleware
    async def guard(request, handler):
        if request.host not in hosts:
            return web.json_response({"error": "Unrecognized dashboard host."}, status=403)
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("Origin") != f"http://{request.host}":
                return web.json_response({"error": "Use the local Portside dashboard."}, status=403)
            if not hmac.compare_digest(request.headers.get("X-Portside-Token", ""), token):
                return web.json_response(
                    {"error": "Refresh the dashboard and try again."}, status=403
                )
            if request.content_type != "application/json":
                return web.json_response({"error": "Expected JSON."}, status=415)
        try:
            response = await handler(request)
        except (PortsideError, json.JSONDecodeError) as error:
            response = web.json_response({"error": str(error)}, status=400)
        except OSError:
            response = web.json_response(
                {"error": "Could not access local configuration or runtime files."}, status=500
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    async def lifecycle(app):
        store.acquire()
        try:
            # Read again after locking, in case another process saved during startup.
            mappings = store.load()
            manager.mappings = {m.id: m for m in mappings}
            from .engine import Runtime

            manager.runtimes = {m.id: Runtime() for m in mappings}
            yield
        finally:
            await manager.close()
            store.release()

    async def state(request):
        return web.json_response(
            {
                "proxies": manager.snapshot(),
                "token": token,
                "caddy_available": bool(manager.binary),
                "config_path": str(store.path),
                "version": __version__,
            }
        )

    async def save(request):
        data = await request.json()
        identifier = request.match_info.get("identifier")
        if identifier:
            if not isinstance(data, dict):
                raise PortsideError("Expected a mapping object.")
            data = {**data, "id": identifier}
        await manager.save(Mapping.parse(data), existing_id=identifier)
        return web.json_response({"ok": True})

    async def action(request):
        identifier = request.match_info["identifier"]
        operation = request.match_info["action"]
        if operation == "start":
            await manager.start(identifier)
        elif operation == "stop":
            await manager.stop(identifier)
        else:
            raise PortsideError("Unknown action.")
        return web.json_response({"ok": True})

    async def delete(request):
        await manager.delete(request.match_info["identifier"])
        return web.json_response({"ok": True})

    async def index(request):
        return web.FileResponse(STATIC / "index.html")

    async def asset(request):
        name = request.match_info["name"]
        if name not in {"app.js", "style.css", "mark.svg", "logo.png", "favicon.png"}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    app = web.Application(middlewares=[guard], client_max_size=16 * 1024)
    app.cleanup_ctx.append(lifecycle)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/static/{name}", asset),
            web.get("/api/state", state),
            web.post("/api/proxies", save),
            web.put("/api/proxies/{identifier}", save),
            web.delete("/api/proxies/{identifier}", delete),
            web.post("/api/proxies/{identifier}/{action}", action),
        ]
    )
    return app


async def open_dashboard(port):
    url = f"http://127.0.0.1:{port}"
    try:
        opened = await asyncio.to_thread(webbrowser.open, url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print(f"Open {url} in your browser.", flush=True)


async def serve(config_path, port, stop_event, *, open_browser=False):
    runner = web.AppRunner(create_app(config_path, port), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        print(f"Portside → http://127.0.0.1:{port}", flush=True)
        print("Press Ctrl+C to stop the dashboard and its proxies.", flush=True)
        if open_browser:
            await open_dashboard(port)
        await stop_event.wait()
    finally:
        await runner.cleanup()
