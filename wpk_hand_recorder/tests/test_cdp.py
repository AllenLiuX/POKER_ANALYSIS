import asyncio
import json
from io import BytesIO
from urllib.error import URLError

import pytest

from wpk_recorder.cdp import CDPRecorder, page_target


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


class _FakeCDPSocket:
    """Answers the recorder's CDP commands; the page reports the given hook states."""

    def __init__(self, recorder, health_values):
        self.recorder = recorder
        self.health_values = list(health_values)
        self.sent = []

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message["method"] == "Runtime.evaluate" and "no-client" in (
            message["params"]["expression"]
        ):
            value = self.health_values.pop(0) if self.health_values else "ok"
            result = {"result": {"value": value}}
        else:
            result = {}
        future = self.recorder._pending.get(message["id"])
        future.set_result({"id": message["id"], "result": result})


def test_hook_watchdog_reinstalls_missing_binding_and_hook():
    recorder = CDPRecorder.__new__(CDPRecorder)
    recorder._pending = {}
    recorder._next_command_id = 1000
    socket = _FakeCDPSocket(recorder, ["ok", "no-binding", "ok"])

    async def run_watchdog():
        task = asyncio.create_task(
            recorder._hook_watchdog(socket, "HOOK_SCRIPT", interval=0)
        )
        while len(socket.sent) < 6:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run_watchdog())
    methods = [
        (message["method"], message["params"].get("expression"))
        for message in socket.sent[:6]
    ]
    assert [method for method, _ in methods] == [
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Runtime.removeBinding",
        "Runtime.addBinding",
        "Runtime.evaluate",
        "Runtime.evaluate",
    ]
    assert methods[4][1] == "HOOK_SCRIPT"


def test_page_target_reports_when_cdp_is_down(monkeypatch):
    def fake_urlopen(url, timeout=3):
        raise URLError(ConnectionRefusedError("Connection refused"))

    monkeypatch.setattr("wpk_recorder.cdp.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Chrome CDP is not listening"):
        page_target(9223)
