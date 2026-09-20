import asyncio

import pytest

from wpk_recorder.cli import (
    _ensure_browser,
    _record_while_dashboard_alive,
    _reconnectable_error,
    parser,
)


def test_recorder_is_cancelled_when_dashboard_stops():
    class BlockingRecorder:
        cancelled = False

        async def run(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    recorder = BlockingRecorder()

    async def run():
        dashboard_task = asyncio.create_task(asyncio.sleep(0))
        with pytest.raises(RuntimeError, match="Dashboard server stopped unexpectedly"):
            await _record_while_dashboard_alive(recorder, dashboard_task)

    asyncio.run(run())
    assert recorder.cancelled


def test_record_and_dashboard_commands_have_independent_ports():
    record = parser().parse_args(["record", "--port", "9224"])
    dashboard = parser().parse_args(
        [
            "dashboard",
            "--port",
            "8766",
            "--assistance-mode",
            "post_session",
        ]
    )

    assert record.command == "record"
    assert record.port == 9224
    assert record.launch_browser is False
    assert dashboard.command == "dashboard"
    assert dashboard.port == 8766
    assert dashboard.assistance_mode == "post_session"


def test_eval_templates_command_accepts_versioned_templates():
    args = parser().parse_args(
        [
            "eval-templates",
            "--template",
            "full-range-v1",
            "--template",
            "full-range-evidence-v2",
            "--depth",
            "light",
        ]
    )
    assert args.command == "eval-templates"
    assert args.templates == [
        "full-range-v1",
        "full-range-evidence-v2",
    ]
    assert args.depth == "light"


def test_record_does_not_launch_browser_by_default():
    default = parser().parse_args(["record"])
    explicit = parser().parse_args(["record", "--launch-browser"])
    run = parser().parse_args(["run", "--no-browser"])

    assert default.launch_browser is False
    assert explicit.launch_browser is True
    assert run.no_browser is True


def test_ensure_browser_does_not_launch_when_disabled(monkeypatch):
    launched = []
    monkeypatch.setattr(
        "wpk_recorder.cli.page_target",
        lambda port: (_ for _ in ()).throw(OSError("cdp down")),
    )
    monkeypatch.setattr(
        "wpk_recorder.cli.launch_browser",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    async def run():
        with pytest.raises(RuntimeError, match="did not become ready"):
            await _ensure_browser(9223, launch=False, timeout=0)

    asyncio.run(run())
    assert launched == []


def test_websocket_disconnects_are_reconnectable():
    class Closed(Exception):
        pass

    assert _reconnectable_error(Closed("no close frame received or sent"))
    assert _reconnectable_error(RuntimeError("No Chrome page for 'h5.sxkxys.com'"))
    assert not _reconnectable_error(KeyboardInterrupt())
    assert not _reconnectable_error(asyncio.CancelledError())
