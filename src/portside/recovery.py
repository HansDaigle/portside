"""Private ownership records for Caddy children that outlive their manager."""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import psutil

from .models import PortsideError


@dataclass
class RunRecord:
    directory: Path
    data: dict

    @property
    def config(self):
        return self.directory / "Caddyfile"

    @classmethod
    def create(cls, state_dir, owner, mapping, binary, configuration):
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = Path(tempfile.mkdtemp(prefix="managed-", dir=state_dir)).resolve()
        record = cls(
            directory,
            {
                "version": 1,
                "owner": str(owner) if owner else None,
                "mapping": mapping.to_dict(),
                "binary": str(Path(binary).resolve()),
                "argv": [
                    binary,
                    "run",
                    "--config",
                    str(directory / "Caddyfile"),
                    "--adapter",
                    "caddyfile",
                ],
                "config_hash": hashlib.sha256(configuration.encode()).hexdigest(),
                "pid": None,
                "created": None,
            },
        )
        try:
            record.config.write_text(configuration)
            record.config.chmod(0o600)
            # Write before spawning: the unique config path also identifies a child
            # if the manager dies between spawn and recording its PID.
            record.write()
        except BaseException:
            record.remove()
            raise
        return record

    def write(self):
        temporary = self.directory / "owner.tmp"
        temporary.write_text(json.dumps(self.data))
        temporary.chmod(0o600)
        temporary.replace(self.directory / "owner.json")

    def attach(self, pid):
        try:
            process = psutil.Process(pid)
            self.data.update(pid=pid, created=process.create_time())
            self.write()
        except psutil.NoSuchProcess:
            pass  # Startup failure is reported by the normal subprocess reader.

    def process(self):
        """Never signal a PID without verifying its identity and exact command."""
        pid = self.data.get("pid")
        try:
            candidates = [psutil.Process(pid)] if pid else psutil.process_iter()
            for process in candidates:
                try:
                    if (
                        process.uids().real == os.getuid()
                        and process.cmdline() == self.data["argv"]
                        and process.exe() == self.data["binary"]
                        and (pid is None or process.create_time() == self.data["created"])
                        and process.status() != psutil.STATUS_ZOMBIE
                    ):
                        return process
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except psutil.NoSuchProcess:
            pass
        return None

    async def stop_survivor(self):
        process = self.process()
        if process is None:
            return False
        try:
            process.terminate()  # psutil also guards against PID reuse when signalling.
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, psutil.TimeoutExpired) as error:
            raise PortsideError("Could not stop the previous Portside proxy process.") from error
        return True

    def remove(self):
        try:
            shutil.rmtree(self.directory)
        except FileNotFoundError:
            pass


def records(state_dir, owner):
    if owner is None:
        return
    for directory in Path(state_dir).glob("managed-*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            data = json.loads((directory / "owner.json").read_text())
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or data.get("owner") != str(owner)
                or not isinstance(data.get("mapping"), dict)
                or not isinstance(data["mapping"].get("id"), str)
                or not isinstance(data.get("binary"), str)
                or (
                    data.get("pid") is not None
                    and (
                        type(data["pid"]) is not int
                        or data["pid"] <= 0
                        or type(data.get("created")) not in (int, float)
                    )
                )
                or not isinstance(data.get("argv"), list)
                or len(data["argv"]) != 6
                or not all(isinstance(arg, str) for arg in data["argv"])
                or data.get("argv")
                != [
                    data.get("argv", [None])[0],
                    "run",
                    "--config",
                    str(directory.resolve() / "Caddyfile"),
                    "--adapter",
                    "caddyfile",
                ]
                or hashlib.sha256((directory / "Caddyfile").read_bytes()).hexdigest()
                != data.get("config_hash")
            ):
                continue
            yield RunRecord(directory, data)
        except (OSError, ValueError, TypeError, IndexError):
            continue
