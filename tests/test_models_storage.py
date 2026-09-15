import pytest

from portside.models import Mapping, PortsideError, validate_mappings
from portside.storage import Store


def mapping(**changes):
    return Mapping.parse(
        {
            "id": "example",
            "name": "Example",
            "port": 4444,
            "upstream": "https://example.com",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"port": True},
        {"port": 80},
        {"port": 65536},
        {"port": "4444"},
        {"name": ""},
        {"name": "x\ny"},
        {"id": "../bad"},
        {"upstream": "https://user:password@example.com"},
        {"upstream": "ftp://example.com"},
        {"upstream": "https://example.com/path"},
        {"upstream": "https://example.com?x=1"},
        {"upstream": "https://example.com/#x"},
        {"upstream": "https://example.com:99999"},
        {"upstream": "https://example.com\nadmin off"},
        {"upstream": "https://bad{host}.com"},
        {"upstream": "http://localhost:4444"},
        {"upstream": "http://a.localhost:4444"},
        {"local_host": "0.0.0.0"},
        {"local_host": "example.com"},
        {"https": "false"},
        {"unknown": "setting"},
    ],
)
def test_rejects_invalid_mapping(changes):
    with pytest.raises(PortsideError):
        mapping(**changes)


def test_normalizes_origin_and_preserves_custom_port():
    value = mapping(upstream="https://EXAMPLE.com:8443/", local_host="app.localhost", https=True)
    assert value.upstream == "https://example.com:8443"
    assert value.local_url == "https://app.localhost:4444"
    assert mapping(upstream="http://[::1]:9090").upstream == "http://[::1]:9090"


def test_duplicate_ports_and_ids():
    for second in (mapping(id="second"), mapping(port=5555)):
        with pytest.raises(PortsideError):
            validate_mappings([mapping(), second])


def test_store_roundtrip_and_lock(tmp_path):
    store = Store(tmp_path / "proxies.toml")
    store.acquire()
    try:
        with pytest.raises(PortsideError, match="already open"):
            Store(store.path).acquire()
        store.save([mapping(name='Quotes " and unicode é')])
        assert store.load() == [mapping(name='Quotes " and unicode é')]
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert not list(tmp_path.glob(".portside-*"))
    finally:
        store.release()
    another = Store(store.path)
    another.acquire()
    another.release()


def test_bad_toml_and_unknown_schema(tmp_path):
    store = Store(tmp_path / "proxies.toml")
    for contents in ("broken = [", "proxy = []", '[proxies]\nname="bad"'):
        store.path.write_text(contents)
        with pytest.raises(PortsideError):
            store.load()
