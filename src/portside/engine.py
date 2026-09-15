import asyncio
import errno
import json
import os
import re
import shutil
import socket
import tempfile
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .metrics import TrafficMetrics
from .models import PortsideError, validate_mappings
from .storage import default_state


def caddyfile(mapping):
    """Generate only validated, quoted Caddyfile tokens. Never interpolate arbitrary directives."""

    def quote(value):
        # Caddyfile quoted strings preserve backslashes; JSON quoting would
        # double regex escapes and silently break domain matching.
        if any(c in value for c in "`\r\n"):
            raise PortsideError("Invalid character in generated proxy configuration.")
        return f"`{value}`"

    upstream = urlsplit(mapping.upstream)
    authority = re.escape(upstream.netloc)
    # Accept the equivalent explicit/default port in redirects, but not another port.
    default_port = 443 if upstream.scheme == "https" else 80
    if upstream.port is None:
        authority += f"(?::{default_port})?"
    elif upstream.port == default_port:
        authority = re.escape(upstream.netloc.rsplit(":", 1)[0]) + f"(?::{default_port})?"
    location_pattern = rf"(?i)^(?:https?:)?//{authority}([/?#].*|$)"
    lines = [
        "{",
        "  admin off",
        "  persist_config off",
        "  auto_https disable_redirects",
        "  skip_install_trust",
        "}",
        f"{mapping.local_url} {{",
        "  bind 127.0.0.1",
        "  log portside {",
        "    output stdout",
        "    format filter {",
        "      wrap json",
        "      fields {",
        "        request delete",
        "        resp_headers delete",
        "        user_id delete",
        "      }",
        "    }",
        "  }",
    ]
    if mapping.https:
        lines.append("  tls internal")
    lines += [
        f"  reverse_proxy {quote(mapping.upstream)} {{",
        f"    header_up Host {quote(upstream.netloc)}",
        f"    header_down Location {quote(location_pattern)} {quote(mapping.local_url + '${1}')}",
        "    transport http {",
        "      dial_timeout 10s",
        "      response_header_timeout 30s",
        "    }",
    ]
    if mapping.rewrite_cookies:
        pattern = rf"(?i);[ \t]*Domain=\.?{re.escape(upstream.hostname)}[ \t]*(;|$)"
        lines.append(f'    header_down Set-Cookie {quote(pattern)} "${{1}}"')
    if mapping.rewrite_origin:
        local = re.escape(mapping.local_url)
        lines += [
            f"    header_up Origin {quote('^' + local + '$')} {quote(mapping.upstream)}",
            f"    header_up Referer {quote('^' + local + '(/.*|$)')} {quote(mapping.upstream + '${1}')}",
        ]
    lines += ["  }", "}", ""]
    return "\n".join(lines)


@dataclass
class Runtime:
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task | None = None
    status: str = "stopped"
    error: str | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=30))
    metrics: TrafficMetrics = field(default_factory=TrafficMetrics)

    def event(self, message):
        self.events.append({"time": datetime.now(UTC).isoformat(), "message": message})


class Manager:
    def __init__(self, mappings=(), store=None, state_dir=None, binary=None, reserved_ports=()):
        validate_mappings(mappings)
        self.mappings = {m.id: m for m in mappings}
        self.runtimes = {m.id: Runtime() for m in mappings}
        self.store = store
        self.state_dir = Path(state_dir or default_state())
        self.binary = binary or shutil.which("caddy")
        self.reserved_ports = set(reserved_ports)
        self.lock = asyncio.Lock()

    def snapshot(self):
        result = []
        for identifier, mapping in self.mappings.items():
            runtime = self.runtimes[identifier]
            result.append(
                {
                    **mapping.to_dict(),
                    "local_url": mapping.local_url,
                    "status": runtime.status,
                    "error": runtime.error,
                    "events": list(runtime.events),
                    "metrics": runtime.metrics.snapshot(),
                }
            )
        return result

    def get(self, identifier):
        if identifier not in self.mappings:
            raise PortsideError("Mapping not found.")
        return self.mappings[identifier]

    async def save(self, mapping, existing_id=None):
        async with self.lock:
            if existing_id is not None:
                self.get(existing_id)
                if mapping.id != existing_id:
                    raise PortsideError("The mapping ID cannot change.")
                if self.runtimes[existing_id].status in {"running", "starting", "stopping"}:
                    raise PortsideError("Stop this mapping before editing it.")
            elif mapping.id in self.mappings:
                raise PortsideError("That mapping ID already exists.")
            if mapping.port in self.reserved_ports:
                raise PortsideError("This port is reserved for the dashboard.")
            updated = {**self.mappings, mapping.id: mapping}
            validate_mappings(list(updated.values()))
            if self.store:
                self.store.save(list(updated.values()))
            self.mappings = updated
            self.runtimes.setdefault(mapping.id, Runtime())
            self.runtimes[mapping.id].error = None
            self.runtimes[mapping.id].status = "stopped"
            self.runtimes[mapping.id].metrics = TrafficMetrics()

    async def delete(self, identifier):
        async with self.lock:
            self.get(identifier)
            if self.runtimes[identifier].status in {"running", "starting", "stopping"}:
                raise PortsideError("Stop this mapping before deleting it.")
            updated = {k: v for k, v in self.mappings.items() if k != identifier}
            if self.store:
                self.store.save(list(updated.values()))
            self.mappings = updated
            del self.runtimes[identifier]

    async def _read_output(self, runtime):
        process = runtime.process
        while line := await process.stdout.readline():
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                if data.get("logger") == "http.log.access.portside":
                    runtime.metrics.record(data)
                    continue
                if data.get("level") not in {"error", "fatal", "warn"}:
                    continue
                # Keep only the message: never retain Caddy's request/header fields.
                message = str(data.get("msg", "Caddy reported an error"))[:500]
                if message in {
                    "admin endpoint disabled",
                    "HTTP/2 skipped because it requires TLS",
                    "HTTP/3 skipped because it requires TLS",
                    "Caddyfile input is not formatted; run 'caddy fmt --overwrite' to fix inconsistencies",
                }:
                    continue
            except (ValueError, TypeError):
                message = line.decode(errors="replace").strip()[:500]
            runtime.event(message)
        code = await process.wait()
        if runtime.status not in {"stopping", "stopped"}:
            runtime.status = "error"
            runtime.error = f"Caddy exited with code {code}. Check recent events."
            runtime.event(runtime.error)

    async def start(self, identifier):
        async with self.lock:
            mapping = self.get(identifier)
            runtime = self.runtimes[identifier]
            if runtime.status == "running":
                return
            if not self.binary:
                raise PortsideError("Caddy is missing. Install it with: brew install caddy")
            if mapping.port in self.reserved_ports:
                raise PortsideError("This port is reserved for the dashboard.")
            if runtime.reader:
                await runtime.reader
            runtime.error = None
            runtime.status = "starting"
            runtime.event("Starting local listener")
            try:
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(("127.0.0.1", mapping.port))
                self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.TemporaryDirectory(prefix="run-", dir=self.state_dir) as temp:
                    config = Path(temp) / "Caddyfile"
                    config.write_text(caddyfile(mapping))
                    env = {
                        **os.environ,
                        "XDG_DATA_HOME": str(self.state_dir / "caddy/data"),
                        "XDG_CONFIG_HOME": str(self.state_dir / "caddy/config"),
                    }
                    runtime.metrics = TrafficMetrics()
                    runtime.process = await asyncio.create_subprocess_exec(
                        self.binary,
                        "run",
                        "--config",
                        str(config),
                        "--adapter",
                        "caddyfile",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )
                    runtime.reader = asyncio.create_task(self._read_output(runtime))
                    for _ in range(100):
                        await asyncio.sleep(0.05)
                        if runtime.process.returncode is not None:
                            await runtime.reader
                            raise PortsideError(runtime.error or "Caddy could not start.")
                        try:
                            _, writer = await asyncio.open_connection("127.0.0.1", mapping.port)
                        except OSError:
                            continue
                        writer.close()
                        await writer.wait_closed()
                        runtime.status = "running"
                        runtime.event(f"Listening at {mapping.local_url}")
                        return
                    raise PortsideError("Caddy did not open the local port within 5 seconds.")
            except (OSError, PortsideError) as error:
                await self._stop(runtime)
                runtime.status = "error"
                runtime.error = (
                    f"Port {mapping.port} is already in use."
                    if isinstance(error, OSError) and error.errno == errno.EADDRINUSE
                    else str(error)
                )
                runtime.event(runtime.error)
                raise PortsideError(runtime.error) from error

    async def _stop(self, runtime):
        runtime.status = "stopping"
        process = runtime.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        if runtime.reader:
            await runtime.reader
        runtime.process = None
        runtime.reader = None
        runtime.status = "stopped"

    async def stop(self, identifier):
        async with self.lock:
            self.get(identifier)
            await self._stop(self.runtimes[identifier])
            self.runtimes[identifier].error = None
            self.runtimes[identifier].event("Stopped")

    async def close(self):
        async with self.lock:
            await asyncio.gather(*(self._stop(r) for r in self.runtimes.values()))
