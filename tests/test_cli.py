import asyncio
import os
import shutil
import socket
import sys

import pytest
from aiohttp import ClientSession
from test_integration import free_port, make_mapping, upstream_server

from portside import cli, server
from portside.storage import Store


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_config_cli_starts_multiple_and_cleans_up_on_signal(tmp_path):
    async with upstream_server() as port:
        mappings = [make_mapping(port, id="one"), make_mapping(port, id="two")]
        config = tmp_path / "proxies.toml"
        Store(config).save(mappings)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "portside.cli",
            "--config",
            str(config),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
        )
        try:
            output = []
            for _ in range(3):
                output.append((await asyncio.wait_for(process.stdout.readline(), 10)).decode())
            assert "Press Ctrl+C" in "".join(output)
            async with ClientSession() as client:
                for mapping in mappings:
                    async with client.get(mapping.local_url + "/") as response:
                        assert response.status == 200
            process.terminate()
            assert await asyncio.wait_for(process.wait(), 10) == 0
            for mapping in mappings:
                with socket.socket() as probe:
                    assert probe.connect_ex(("127.0.0.1", mapping.port)) != 0
            store = Store(config)
            store.acquire()
            store.release()
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


async def test_browser_opens_after_ready_and_existing_dashboard_is_reused(tmp_path, monkeypatch):
    config = tmp_path / "proxies.toml"
    port = free_port()
    stop = asyncio.Event()
    ready = asyncio.Event()
    opened = []

    async def open_browser(actual_port):
        assert actual_port == port
        assert await cli.dashboard_running(config, port)
        opened.append(actual_port)
        ready.set()

    monkeypatch.setattr(server, "open_dashboard", open_browser)
    monkeypatch.setattr(cli, "open_dashboard", open_browser)
    task = asyncio.create_task(server.serve(config, port, stop, open_browser=True))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        assert not await cli.dashboard_running(tmp_path / "different.toml", port)
        args = cli.parser().parse_args(
            ["ui", "--port", str(port), "--config", str(config), "--open"]
        )
        await cli.run(args)
        assert opened == [port, port]
        assert not task.done()
    finally:
        stop.set()
        await asyncio.wait_for(task, 5)
    assert not await cli.dashboard_running(config, port)


async def test_browser_failure_leaves_manual_url(monkeypatch, capsys):
    def unavailable(url):
        raise OSError("No browser available")

    monkeypatch.setattr(server.webbrowser, "open", unavailable)
    await server.open_dashboard(9876)
    assert "Open http://127.0.0.1:9876 in your browser." in capsys.readouterr().out
