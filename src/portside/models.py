import ipaddress
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit
from uuid import uuid4


class PortsideError(ValueError):
    """A user-facing configuration or lifecycle error."""


@dataclass(frozen=True)
class Mapping:
    id: str
    name: str
    port: int
    upstream: str
    local_host: str = "localhost"
    https: bool = False
    rewrite_cookies: bool = False
    rewrite_origin: bool = False

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise PortsideError("Each mapping must be an object.")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise PortsideError(f"Unknown fields: {', '.join(sorted(unknown))}")
        name = data.get("name", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise PortsideError("Give the mapping a name between 1 and 80 characters.")
        if any(ord(c) < 32 for c in name):
            raise PortsideError("The name cannot contain control characters.")
        port = data.get("port")
        if type(port) is not int or not 1024 <= port <= 65535:
            raise PortsideError("Local port must be a number from 1024 to 65535.")
        upstream = data.get("upstream", "")
        if not isinstance(upstream, str) or len(upstream) > 2048:
            raise PortsideError("Enter an HTTP or HTTPS destination.")
        if any(c.isspace() or ord(c) < 32 for c in upstream):
            raise PortsideError("Destination cannot contain whitespace or control characters.")
        try:
            parsed = urlsplit(upstream)
            upstream_port, hostname = parsed.port, parsed.hostname
        except ValueError:
            raise PortsideError("Enter a valid destination URL and port.") from None
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise PortsideError("Destination must start with http:// or https://.")
        if parsed.username is not None or parsed.password is not None:
            raise PortsideError("Credentials in destination URLs are not supported.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise PortsideError(
                "Use a destination domain and optional port, without a path or query."
            )
        if upstream_port is not None and not 1 <= upstream_port <= 65535:
            raise PortsideError("Destination port must be from 1 to 65535.")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", hostname):
                raise PortsideError("Use an ASCII hostname or IP address.")
            if len(hostname) > 253 or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                for label in hostname.split(".")
            ):
                raise PortsideError("Destination hostname is invalid.")
        local_host = data.get("local_host", "localhost")
        if not isinstance(local_host, str) or not (
            local_host in {"localhost", "127.0.0.1"}
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.localhost", local_host)
        ):
            raise PortsideError("Local hostname must be localhost, 127.0.0.1, or name.localhost.")
        for key in ("https", "rewrite_cookies", "rewrite_origin"):
            if type(data.get(key, False)) is not bool:
                raise PortsideError(f"{key} must be true or false.")
        identifier = data.get("id", uuid4().hex[:12])
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", identifier):
            raise PortsideError("Mapping ID must use lowercase letters, digits, or hyphens.")
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if upstream_port is not None:
            authority += f":{upstream_port}"
        if (
            hostname in {local_host, "localhost", "127.0.0.1", "::1"}
            or hostname.endswith(".localhost")
        ) and (upstream_port or (443 if parsed.scheme == "https" else 80)) == port:
            raise PortsideError("A mapping cannot forward back to its own port.")
        return cls(
            identifier,
            name.strip(),
            port,
            f"{parsed.scheme}://{authority}",
            local_host,
            data.get("https", False),
            data.get("rewrite_cookies", False),
            data.get("rewrite_origin", False),
        )

    @property
    def local_url(self):
        return f"{'https' if self.https else 'http'}://{self.local_host}:{self.port}"

    def to_dict(self):
        return asdict(self)


def validate_mappings(mappings):
    for field in ("id", "port"):
        values = [getattr(m, field) for m in mappings]
        if len(values) != len(set(values)):
            raise PortsideError(f"Every mapping needs a unique {field}.")
