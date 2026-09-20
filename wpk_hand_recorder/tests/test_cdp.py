from io import BytesIO
from urllib.error import URLError

import pytest

from wpk_recorder.cdp import page_target


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return BytesIO(self._payload)

    def __exit__(self, *_args):
        return False


def test_page_target_falls_back_to_ipv6_localhost(monkeypatch):
    payload = (
        b'[{"type":"page","url":"https://h5.sxkxys.com/","webSocketDebuggerUrl":'
        b'"ws://[::1]:9223/devtools/page/abc"}]'
    )

    def fake_urlopen(url, timeout=3):
        if "127.0.0.1" in url:
            raise URLError(ConnectionRefusedError("Connection refused"))
        if "[::1]" in url:
            return _FakeResponse(payload)
        raise AssertionError(url)

    monkeypatch.setattr("wpk_recorder.cdp.urlopen", fake_urlopen)
    target = page_target(9223)
    assert target["webSocketDebuggerUrl"].startswith("ws://[::1]:9223/")


def test_page_target_reports_when_cdp_is_down(monkeypatch):
    def fake_urlopen(url, timeout=3):
        raise URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr("wpk_recorder.cdp.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Chrome CDP is not listening"):
        page_target(9223)
