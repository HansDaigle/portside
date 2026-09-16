import asyncio
import json
import os
import shutil
import signal
import sys

import psutil
import pytest
from aiohttp import ClientSession
from test_integration import free_port, make_mapping, running_app, upstream_server

from portside.engine import Manager
from portside.models import Project
from portside.recovery import RunRecord, records
from portside.server import create_app
from portside.storage import Store

pytestmark = pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")


@pytest.mark.parametrize("exit_signal", [signal.SIGKILL, signal.SIGHUP])
async def test_interrupted_cli_and_dashboard_recovery(tmp_path, exit_signal):
    async with upstream_server() as upstream:
        mappings = [make_mapping(upstream, id="one"), make_mapping(upstream, id="two")]
        config = tmp_path / "proxies.toml"
        store = Store(config)
        store.save(mappings, [Project("work", "Work", tuple(m.id for m in mappings))])
        state_dir = tmp_path / "state" / "portside"
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
        original_records = []
        try:
            for _ in range(3):
                line = await asyncio.wait_for(process.stdout.readline(), 10)
                assert line, (await process.stderr.read()).decode()
            original_records = list(records(state_dir, store.path))
            assert len(original_records) == 2
            old_pids = {record.process().pid for record in original_records}
            process.send_signal(exit_signal)
            await asyncio.wait_for(process.wait(), 10)
            if exit_signal == signal.SIGHUP:
                assert process.returncode == 0
                assert not list(records(state_dir, store.path))
                assert all(record.process() is None for record in original_records)
                return

            assert all(record.process() is not None for record in original_records)
            # An unrelated config sharing the state directory must remain untouched.
            other = make_mapping(upstream, id="other")
            other_store = Store(tmp_path / "other.toml")
            outsider = Manager([other], store=other_store, state_dir=state_dir)
            await outsider.start(other.id)
            other_pid = outsider.runtimes[other.id].process.pid
            try:
                port = free_port()
                base = f"http://127.0.0.1:{port}"
                async with (
                    running_app(create_app(config, port, state_dir), port),
                    ClientSession() as client,
                ):
                    async with client.get(base + "/api/state") as response:
                        state = await response.json()
                        assert {p["status"] for p in state["proxies"]} == {"running"}
                        assert state["projects"][0]["running_count"] == 2
                    assert all(
                        any("Recovery complete" in e["message"] for e in p["events"])
                        for p in state["proxies"]
                    )
                    new_pids = {r.process().pid for r in records(state_dir, store.path)}
                    assert not (old_pids & new_pids)
                    for mapping in mappings:
                        async with client.get(mapping.local_url) as response:
                            assert response.status == 200
                    await asyncio.sleep(0.1)
                    async with client.get(base + "/api/state") as response:
                        state = await response.json()
                    assert all(p["metrics"]["requests"] == 1 for p in state["proxies"])
                    headers = {"Origin": base, "X-Portside-Token": state["token"]}
                    async with client.post(
                        base + "/api/proxies/one/stop", json={}, headers=headers
                    ) as response:
                        assert response.status == 200
                    assert outsider.runtimes[other.id].process.pid == other_pid
                    assert outsider.runtimes[other.id].process.returncode is None
                assert not list(records(state_dir, store.path))
            finally:
                await outsider.close()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            for record in original_records:
                await record.stop_survivor()


@pytest.mark.parametrize("field", ["created", "argv", "binary"])
async def test_changed_process_identity_is_never_signalled(tmp_path, field):
    mapping = make_mapping(12345)
    manager = Manager([mapping], state_dir=tmp_path)
    try:
        await manager.start(mapping.id)
        runtime = manager.runtimes[mapping.id]
        record = RunRecord(runtime.record.directory, dict(runtime.record.data))
        record.data[field] = {
            "created": record.data["created"] - 1,
            "argv": ["unrelated"],
            "binary": "/unrelated/caddy",
        }[field]
        assert not await record.stop_survivor()
        assert psutil.Process(runtime.process.pid).is_running()
    finally:
        await manager.close()


async def test_spawn_before_pid_write_is_recoverable(tmp_path):
    mapping = make_mapping(12345)
    store = Store(tmp_path / "proxies.toml")
    manager = Manager([mapping], store=store, state_dir=tmp_path)
    try:
        await manager.start(mapping.id)
        runtime = manager.runtimes[mapping.id]
        data = dict(runtime.record.data, pid=None, created=None)
        (runtime.record.directory / "owner.json").write_text(json.dumps(data))
        recovered = list(records(tmp_path, store.path))
        assert len(recovered) == 1
        assert recovered[0].process().pid == runtime.process.pid
    finally:
        await manager.close()
