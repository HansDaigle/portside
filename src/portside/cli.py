import argparse
import asyncio
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from aiohttp import ClientError, ClientSession, ClientTimeout

from . import __version__
from .engine import Manager
from .models import Mapping, PortsideError
from .server import open_dashboard, serve
from .storage import Store, default_config


def parser():
    root = argparse.ArgumentParser(prog="portside", description="Remote destinations. Local ports.")
    root.add_argument("--version", action="version", version=f"Portside {__version__}")
    root.add_argument("--port", type=int, help="Local proxy port (1024–65535)")
    root.add_argument("--to", help="Destination origin, e.g. https://example.com")
    root.add_argument("--config", type=Path, help="Start all mappings from a TOML file")
    root.add_argument("--local-host", default="localhost", help="localhost or name.localhost")
    root.add_argument("--https", action="store_true", help="Use local HTTPS (explicit trust setup)")
    root.add_argument(
        "--rewrite-cookies", action="store_true", help="Translate exact-domain cookies"
    )
    root.add_argument(
        "--rewrite-origin", action="store_true", help="Translate matching local origins"
    )
    sub = root.add_subparsers(dest="command")
    ui = sub.add_parser("ui", help="Open the local dashboard server")
    ui.add_argument("--port", type=int, default=9876, dest="ui_port")
    ui.add_argument("--config", type=Path, default=default_config(), dest="ui_config")
    ui.add_argument("--open", action="store_true", dest="open_browser", help="Open your browser")
    doctor = sub.add_parser("doctor", help="Check dependencies and configuration")
    doctor.add_argument("--config", type=Path, default=default_config(), dest="doctor_config")
    return root


async def run(args):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)
    if args.command == "ui":
        if not 1024 <= args.ui_port <= 65535:
            raise PortsideError("Dashboard port must be from 1024 to 65535.")
        if args.open_browser and await dashboard_running(args.ui_config, args.ui_port):
            print("Portside is already running. Opening its dashboard.", flush=True)
            await open_dashboard(args.ui_port)
            return
        await serve(args.ui_config, args.ui_port, stop, open_browser=args.open_browser)
        return
    store = None
    manager = None
    try:
        if args.config:
            if not args.config.is_file():
                raise PortsideError(f"Config file does not exist: {args.config}")
            store = Store(args.config)
            store.acquire()
            mappings = store.load()
            if not mappings:
                raise PortsideError("This config has no mappings.")
        else:
            mappings = [
                Mapping.parse(
                    {
                        "name": "Quick proxy",
                        "port": args.port,
                        "upstream": args.to,
                        "local_host": args.local_host,
                        "https": args.https,
                        "rewrite_cookies": args.rewrite_cookies,
                        "rewrite_origin": args.rewrite_origin,
                    }
                )
            ]
        manager = Manager(mappings)
        if store:
            manager.store = store
            await manager.recover()
        for mapping in mappings:
            await manager.start(mapping.id)
            print(f"{mapping.local_url} → {mapping.upstream}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 0.5)
            except TimeoutError:
                pass
            if any(row["status"] == "error" for row in manager.snapshot()):
                raise PortsideError("A proxy exited unexpectedly; stopping this run.")
    finally:
        if manager:
            await manager.close()
        if store:
            store.release()


async def dashboard_running(config_path, port):
    """Reuse only a dashboard serving the requested configuration."""
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=2), trust_env=False) as client,
            client.get(f"http://127.0.0.1:{port}/api/state", allow_redirects=False) as response,
        ):
            if response.status != 200:
                return False
            state = await response.json()
            return (
                isinstance(state, dict)
                and isinstance(state.get("proxies"), list)
                and state.get("version") == __version__
                and state.get("config_path") == str(config_path.expanduser().resolve())
            )
    except (ClientError, TimeoutError, ValueError):
        return False


def main():
    cli = parser()
    args = cli.parse_args()
    if args.command and (args.port is not None or args.to or args.config):
        cli.error("Place subcommand options after ui/doctor; quick proxy options are separate.")
    if not args.command and (bool(args.config) == bool(args.port is not None or args.to)):
        cli.error("Use --port PORT --to URL, --config FILE, or the ui/doctor command.")
    if not args.command and not args.config and (args.port is None or not args.to):
        cli.error("--port and --to are required together.")
    try:
        if args.command == "doctor":
            binary = shutil.which("caddy")
            if not binary:
                raise PortsideError("Caddy is missing. Install it with: brew install caddy")
            version = subprocess.run(
                [binary, "version"], capture_output=True, text=True, check=True, timeout=10
            ).stdout.strip()
            mappings = Store(args.doctor_config).load()
            print(f"Portside {__version__}\nPython {sys.version.split()[0]}\nCaddy {version}")
            print(f"Config: {args.doctor_config} ({len(mappings)} saved mappings)")
            return
        asyncio.run(run(args))
    except (PortsideError, OSError, subprocess.SubprocessError) as error:
        print(f"Portside: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
