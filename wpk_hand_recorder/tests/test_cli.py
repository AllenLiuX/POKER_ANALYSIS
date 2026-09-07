import asyncio

import pytest

from wpk_recorder.cli import _record_while_dashboard_alive, parser


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
    dashboard = parser().parse_args(["dashboard", "--port", "8766"])

    assert record.command == "record"
    assert record.port == 9224
    assert dashboard.command == "dashboard"
    assert dashboard.port == 8766
