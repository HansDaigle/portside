import fcntl
import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w

from .models import Mapping, PortsideError, Project, validate_mappings, validate_projects


def default_config():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "portside/proxies.toml"
    )


def default_state():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "portside"


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self._lock = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        self._lock = os.fdopen(fd, "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.release()
            raise PortsideError(
                "This config is already open in another Portside process."
            ) from None

    def release(self):
        if self._lock:
            self._lock.close()
            self._lock = None

    def load(self):
        return self.load_config()[0]

    def load_config(self):
        if not self.path.exists():
            return [], []
        try:
            with self.path.open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise PortsideError(f"Cannot read config: {error}") from error
        if (
            set(data) - {"proxies", "projects"}
            or not isinstance(data.get("proxies", []), list)
            or not isinstance(data.get("projects", []), list)
        ):
            raise PortsideError("Config accepts [[proxies]] and [[projects]] entries.")
        mappings = [Mapping.parse(item) for item in data.get("proxies", [])]
        projects = [Project.parse(item) for item in data.get("projects", [])]
        validate_mappings(mappings)
        validate_projects(projects, mappings)
        return mappings, projects

    def save(self, mappings, projects=None):
        if projects is None:
            projects = self.load_config()[1]
        validate_mappings(mappings)
        validate_projects(projects, mappings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".portside-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                data = {"proxies": [m.to_dict() for m in mappings]}
                if projects:
                    data["projects"] = [p.to_dict() for p in projects]
                tomli_w.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
