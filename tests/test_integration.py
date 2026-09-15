import asyncio
import shutil
import socket
import ssl
from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession, WSMsgType, web

from portside.engine import Manager
from portside.metrics import TrafficMetrics
from portside.models import Mapping, PortsideError
from portside.server import create_app
from portside.storage import Store


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@asynccontextmanager
async def running_app(app, port):
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", port).start()
        yield
    finally:
        await runner.cleanup()


@asynccontextmanager
async def upstream_server():
    port = free_port()

    async def echo(request):
        if request.path == "/ws":
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await ws.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await ws.send_bytes(message.data)
            return ws
        if request.path == "/redirect":
            return web.Response(
                status=302, headers={"Location": f"http://127.0.0.1:{port}/next?q=1#ok"}
            )
        if request.path == "/external":
            return web.Response(status=307, headers={"Location": "https://unrelated.example/next"})
        if request.path == "/failure":
            return web.Response(status=503, text="Unavailable")
        if request.path == "/cookies":
            response = web.Response(text="cookies")
            response.headers.add(
                "Set-Cookie", "session=abc; Domain=127.0.0.1; Path=/; HttpOnly; SameSite=Lax"
            )
            response.headers.add("Set-Cookie", "other=def; Path=/; Secure")
            return response
        if request.path == "/stream":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            await asyncio.sleep(0.3)
            await response.write(b"data: second\n\n")
            return response
        return web.json_response(
            {
                "method": request.method,
                "path": request.raw_path,
                "body": (await request.read()).decode(),
                "host": request.host,
                "origin": request.headers.get("Origin"),
                "referer": request.headers.get("Referer"),
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", echo)
    async with running_app(app, port):
        yield port


def make_mapping(upstream_port, **changes):
    return Mapping.parse(
        {
            "id": "test",
            "name": "Test",
            "port": free_port(),
            "upstream": f"http://127.0.0.1:{upstream_port}",
            **changes,
        }
    )


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_real_forwarding_redirects_cookies_and_streaming(tmp_path):
    async with upstream_server() as port:
        mapping = make_mapping(port, rewrite_cookies=True, rewrite_origin=True)
        manager = Manager([mapping], state_dir=tmp_path)
        try:
            await manager.start(mapping.id)
            async with ClientSession() as client:
                async with client.post(
                    mapping.local_url + "/a%2Fb?x=a%20b&x=2",
                    data="hello",
                    headers={"Origin": mapping.local_url, "Referer": mapping.local_url + "/page"},
                ) as response:
                    body = await response.json()
                    assert body == {
                        "method": "POST",
                        "path": "/a%2Fb?x=a%20b&x=2",
                        "body": "hello",
                        "host": f"127.0.0.1:{port}",
                        "origin": mapping.upstream,
                        "referer": mapping.upstream + "/page",
                    }
                async with client.get(
                    mapping.local_url + "/redirect", allow_redirects=False
                ) as response:
                    assert response.status == 302
                    assert response.headers["Location"] == mapping.local_url + "/next?q=1#ok"
                async with client.get(
                    mapping.local_url + "/external", allow_redirects=False
                ) as response:
                    assert response.status == 307
                    assert response.headers["Location"] == "https://unrelated.example/next"
                async with client.get(mapping.local_url + "/cookies") as response:
                    cookies = response.headers.getall("Set-Cookie")
                    assert cookies == [
                        "session=abc; Path=/; HttpOnly; SameSite=Lax",
                        "other=def; Path=/; Secure",
                    ]
                async with client.get(mapping.local_url + "/stream") as response:
                    first = await asyncio.wait_for(response.content.readuntil(b"\n\n"), 0.2)
                    assert first == b"data: first\n\n"
                    assert await response.read() == b"data: second\n\n"
        finally:
            await manager.close()
        assert all(row["status"] == "stopped" for row in manager.snapshot())


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_concurrent_websockets_stop_restart_and_occupied_port(tmp_path):
    async with upstream_server() as port:
        first, second = make_mapping(port), make_mapping(port, id="second")
        manager = Manager([first, second], state_dir=tmp_path)
        try:
            await manager.start(first.id)
            await manager.start(second.id)
            async with (
                ClientSession() as client,
                client.ws_connect(first.local_url + "/ws") as ws,
            ):
                await ws.send_str("before")
                assert (await ws.receive(timeout=2)).data == "before"
                await manager.stop(second.id)
                await ws.send_bytes(b"after")
                assert (await ws.receive(timeout=2)).data == b"after"
                await manager.start(second.id)
                with pytest.raises(PortsideError, match="Stop"):
                    await manager.delete(first.id)
            await manager.stop(first.id)
            with socket.socket() as occupied:
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                occupied.bind(("127.0.0.1", first.port))
                occupied.listen()
                with pytest.raises(PortsideError):
                    await manager.start(first.id)
            await manager.start(first.id)
        finally:
            await manager.close()


async def test_dashboard_crud_validation_and_cross_origin_protection(tmp_path):
    port = free_port()
    config = tmp_path / "proxies.toml"
    app = create_app(config, port, state_dir=tmp_path / "state")
    base = f"http://127.0.0.1:{port}"
    async with running_app(app, port), ClientSession() as client:
        async with client.get(base + "/api/state") as response:
            state = await response.json()
        headers = {"Origin": base, "X-Portside-Token": state["token"]}
        data = {
            "id": "saved",
            "name": "Saved mapping",
            "port": free_port(),
            "upstream": "https://example.com",
        }
        for bad in (
            {},
            {**headers, "Origin": "https://evil.example"},
            {**headers, "X-Portside-Token": "bad"},
        ):
            async with client.post(base + "/api/proxies", json=data, headers=bad) as response:
                assert response.status == 403
        async with client.get(base + "/api/state", headers={"Host": "evil.example"}) as response:
            assert response.status == 403
        async with client.post(base + "/api/proxies", json=data, headers=headers) as response:
            assert response.status == 200
        assert Store(config).load()[0].name == "Saved mapping"
        async with client.post(
            base + "/api/proxies", json={**data, "id": "duplicate"}, headers=headers
        ) as response:
            assert response.status == 400
        async with client.put(
            base + "/api/proxies/saved", json={**data, "name": "Updated"}, headers=headers
        ) as response:
            assert response.status == 200
        assert Store(config).load()[0].name == "Updated"
        async with client.delete(base + "/api/proxies/saved", json={}, headers=headers) as response:
            assert response.status == 200
        assert Store(config).load() == []


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_local_https_with_explicit_test_ca_and_unrelated_origin(tmp_path):
    async with upstream_server() as upstream_port:
        mapping = make_mapping(upstream_port, https=True, rewrite_origin=True)
        manager = Manager([mapping], state_dir=tmp_path)
        try:
            await manager.start(mapping.id)
            root = tmp_path / "caddy/data/caddy/pki/authorities/local/root.crt"
            # Trust only this test CA in this client; never modify system trust.
            for _ in range(100):
                if root.exists():
                    break
                await asyncio.sleep(0.05)
            context = ssl.create_default_context(cafile=str(root))
            async with ClientSession() as client:
                for attempt in range(30):
                    try:
                        async with client.get(
                            mapping.local_url + "/",
                            ssl=context,
                            headers={"Origin": "https://unrelated.example"},
                        ) as response:
                            body = await response.json()
                            assert body["origin"] == "https://unrelated.example"
                            assert body["host"] == f"127.0.0.1:{upstream_port}"
                            break
                    except OSError:
                        if attempt == 29:
                            raise
                        await asyncio.sleep(0.1)
        finally:
            await manager.close()


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_upstream_tls_rejects_untrusted_certificate(tmp_path):
    async with upstream_server() as upstream_port:
        secure = make_mapping(upstream_port, https=True)
        outer = make_mapping(upstream_port, id="outer", upstream=secure.local_url)
        manager = Manager([secure, outer], state_dir=tmp_path)
        try:
            await manager.start(secure.id)
            await manager.start(outer.id)
            async with (
                ClientSession() as client,
                client.get(outer.local_url + "/") as response,
            ):
                assert response.status == 502
        finally:
            await manager.close()


async def test_failed_process_start_is_reported_and_cleaned_up(tmp_path):
    mapping = make_mapping(free_port())
    manager = Manager([mapping], binary="/usr/bin/false", state_dir=tmp_path)
    with pytest.raises(PortsideError, match="exited"):
        await manager.start(mapping.id)
    assert manager.snapshot()[0]["status"] == "error"
    assert manager.runtimes[mapping.id].process is None
    await manager.close()


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_actual_access_logs_are_filtered_and_feed_metrics(tmp_path, monkeypatch):
    entries = []

    class CapturingMetrics(TrafficMetrics):
        def record(self, entry, now=None):
            entries.append(entry)
            super().record(entry, now=now)

    monkeypatch.setattr("portside.engine.TrafficMetrics", CapturingMetrics)
    async with upstream_server() as upstream_port:
        mapping = make_mapping(upstream_port)
        manager = Manager([mapping], state_dir=tmp_path)
        try:
            await manager.start(mapping.id)
            async with ClientSession() as client:
                for path in ("/cookies?private=test", "/failure"):
                    async with client.get(
                        mapping.local_url + path,
                        headers={"Authorization": "Bearer test-only", "Cookie": "test-only"},
                    ) as response:
                        await response.read()
            for _ in range(100):
                if manager.snapshot()[0]["metrics"]["requests"] == 2:
                    break
                await asyncio.sleep(0.01)
            metrics = manager.snapshot()[0]["metrics"]
            assert metrics["requests"] == 2
            assert metrics["errors"] == 1
            assert metrics["response_bytes"] == len("cookiesUnavailable")
            assert metrics["avg_duration_ms"] >= 0
            assert len(entries) == 2
            assert all(
                not ({"request", "resp_headers", "user_id"} & entry.keys()) for entry in entries
            )
            assert all(
                "test-only" not in str(entry) and "private" not in str(entry) for entry in entries
            )
            await manager.stop(mapping.id)
            await manager.start(mapping.id)
            assert manager.snapshot()[0]["metrics"]["requests"] == 0
        finally:
            await manager.close()
