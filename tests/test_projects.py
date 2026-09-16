import asyncio
import shutil
import socket
from dataclasses import replace

import pytest
from aiohttp import ClientSession
from test_integration import free_port, make_mapping, running_app, upstream_server

from portside.engine import Manager
from portside.models import PortsideError, Project, validate_projects
from portside.server import create_app
from portside.storage import Store


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"name": ""},
        {"name": "bad\nname"},
        {"id": "../bad", "name": "Project"},
        {"name": "Project", "proxy_ids": "one"},
        {"name": "Project", "proxy_ids": ["one", "one"]},
        {"name": "Project", "extra": True},
    ],
)
def test_invalid_project(data):
    with pytest.raises(PortsideError):
        Project.parse(data)


def test_project_config_legacy_roundtrip_and_invalid_membership(tmp_path):
    mapping = make_mapping(12345, id="one")
    store = Store(tmp_path / "proxies.toml")
    store.save([mapping])
    assert store.load_config() == ([mapping], [])
    project = Project("work", "Work", (mapping.id,))
    store.save([mapping], [project])
    assert Store(store.path).load_config() == ([mapping], [project])
    renamed = replace(mapping, name="Renamed connection")
    Store(store.path).save([renamed])
    assert store.load_config() == ([renamed], [project])
    before = store.path.read_bytes()
    for projects in (
        [project, Project("other", "Other", (mapping.id,))],
        [project, Project("other", "WORK")],
        [Project("bad", "Bad", ("missing",))],
    ):
        with pytest.raises(PortsideError):
            store.save([renamed], projects)
        assert store.path.read_bytes() == before
    with pytest.raises(PortsideError):
        validate_projects([project, project], [mapping])
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(not shutil.which("caddy"), reason="Caddy required")
async def test_group_partial_retry_isolation_and_live_membership(tmp_path):
    async with upstream_server() as port:
        mappings = [make_mapping(port, id=name) for name in ("web", "api", "auth", "other")]
        project = Project("work", "Work", tuple(m.id for m in mappings[:3]))
        other = Project("other", "Other", ("other",))
        store = Store(tmp_path / "proxies.toml")
        manager = Manager(
            mappings, store=store, state_dir=tmp_path / "state", projects=[project, other]
        )
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", mappings[1].port))
        occupied.listen()
        try:
            await manager.project_action(other.id, "start")
            other_pid = manager.runtimes["other"].process.pid
            result = await manager.project_action(project.id, "start")
            assert not result["ok"]
            assert [r["ok"] for r in result["results"]] == [True, False, True]
            state = manager.project_snapshot()[0]
            assert (state["status"], state["running_count"], state["error_count"]) == (
                "partial",
                2,
                1,
            )
            first_pid = manager.runtimes["web"].process.pid
            async with (
                ClientSession() as client,
                client.ws_connect(mappings[0].local_url + "/ws") as ws,
            ):
                occupied.close()
                # Repeated starts are serialized and leave existing processes alone.
                results = await asyncio.gather(
                    *(manager.project_action(project.id, "start") for _ in range(2))
                )
                assert all(result["ok"] for result in results)
                assert manager.runtimes["web"].process.pid == first_pid
                assert manager.project_snapshot()[0]["running_count"] == 3
                ownership = manager.runtimes["web"].record.data.copy()
                await manager.save_project(replace(project, name="Renamed"), existing_id=project.id)
                await manager.save_project(Project("second", "Second", ("web",)))
                assert manager.projects[project.id].proxy_ids == ("api", "auth")
                assert manager.runtimes["web"].record.data == ownership
                await ws.send_str("still connected")
                assert (await ws.receive()).data == "still connected"
                await manager.delete_project("second")
                assert manager.runtimes["web"].process.pid == first_pid
                assert manager.runtimes["web"].status == "running"
            assert (await manager.project_action(project.id, "stop"))["ok"]
            assert manager.runtimes["other"].process.pid == other_pid
            assert manager.runtimes["other"].status == "running"
            await manager.delete("api")
            assert manager.projects[project.id].proxy_ids == ("auth",)
            saved_mappings, saved_projects = store.load_config()
            assert len(saved_mappings) == 3 and len(saved_projects) == 2
            await manager.save_project(Project("empty", "Empty"))
            assert (await manager.project_action("empty", "start"))["results"] == []
        finally:
            occupied.close()
            await manager.close()


async def test_project_api_crud_guards_and_restart_persistence(tmp_path):
    config = tmp_path / "proxies.toml"
    mapping = make_mapping(12345, id="one")
    Store(config).save([mapping])
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    async with (
        running_app(create_app(config, port, tmp_path / "state"), port),
        ClientSession() as client,
    ):
        async with client.get(base + "/api/state") as response:
            state = await response.json()
        headers = {"Origin": base, "X-Portside-Token": state["token"]}
        data = {"id": "work", "name": "Work", "proxy_ids": [mapping.id]}
        async with client.post(base + "/api/projects", json=data) as response:
            assert response.status == 403
        async with client.post(base + "/api/projects", json=data, headers=headers) as response:
            assert response.status == 200
        for method, url in (
            ("put", "/api/projects/work"),
            ("delete", "/api/projects/work"),
            ("post", "/api/projects/work/start"),
        ):
            async with getattr(client, method)(base + url, json=data) as response:
                assert response.status == 403
        async with client.put(
            base + "/api/projects/work", json={**data, "name": "Updated"}, headers=headers
        ) as response:
            assert response.status == 200
        async with client.put(
            base + "/api/projects/work", json={**data, "proxy_ids": ["missing"]}, headers=headers
        ) as response:
            assert response.status == 400
    async with (
        running_app(create_app(config, port, tmp_path / "state"), port),
        ClientSession() as client,
    ):
        async with client.get(base + "/api/state") as response:
            state = await response.json()
        assert state["projects"][0]["name"] == "Updated"
        assert state["projects"][0]["proxy_ids"] == [mapping.id]
        headers["X-Portside-Token"] = state["token"]
        async with client.post(
            base + "/api/projects/missing/start", json={}, headers=headers
        ) as response:
            assert response.status == 400
        async with client.delete(base + "/api/projects/work", headers=headers, json={}) as response:
            assert response.status == 200
        assert Store(config).load_config() == ([mapping], [])
