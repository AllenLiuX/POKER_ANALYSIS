from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .analytics import connect_readonly, hand_detail, snapshot


def create_app(data_dir: Path) -> FastAPI:
    data_dir = data_dir.resolve()
    web_dir = Path(__file__).parent / "web"
    app = FastAPI(title="WPK 实时牌谱", docs_url=None, redoc_url=None)
    app.state.data_dir = data_dir
    app.mount("/assets", StaticFiles(directory=web_dir), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/api/snapshot")
    async def api_snapshot(
        mode: Optional[str] = Query(default=None, pattern="^(holdem|squid)$")
    ) -> dict:
        try:
            return snapshot(data_dir, mode=mode)
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/hands/{hand_id}")
    async def api_hand(hand_id: str) -> dict:
        hand = hand_detail(data_dir, hand_id)
        if hand is None:
            raise HTTPException(status_code=404, detail="hand not found")
        return hand

    @app.get("/api/export/hands.csv")
    async def export_hands() -> Response:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "hand_id",
                "hand_number",
                "played_at_utc",
                "game_mode",
                "street",
                "sequence",
                "user_id",
                "alias",
                "seat",
                "action",
                "amount",
                "amount_to",
                "stack_after",
            ]
        )
        connection = connect_readonly(data_dir)
        try:
            for row in connection.execute(
                """
                SELECT h.hand_id, h.hand_number, h.played_at, h.game_mode,
                       a.street, a.sequence,
                       a.user_id, a.alias, a.seat, a.action, a.amount,
                       a.amount_to, a.stack_after
                FROM hands h JOIN actions a ON a.hand_id = h.hand_id
                WHERE h.excluded_from_stats = 0
                ORDER BY h.played_at, h.hand_number, a.sequence
                """
            ):
                writer.writerow(tuple(row))
        finally:
            connection.close()
        return Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="wpk-actions.csv"'},
        )

    @app.get("/api/export/hands.json")
    async def export_hands_json() -> Response:
        connection = connect_readonly(data_dir)
        try:
            hands = []
            for row in connection.execute(
                """
                SELECT hand_json, hand_number, played_at
                FROM hands ORDER BY played_at, hand_number
                """
            ):
                hand = json.loads(row["hand_json"])
                hand["hand_number"] = row["hand_number"]
                hand["played_at"] = row["played_at"]
                hands.append(hand)
        finally:
            connection.close()
        return Response(
            json.dumps(hands, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="wpk-hands.json"'},
        )

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _event_stream(request, data_dir),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


async def _event_stream(request: Request, data_dir: Path) -> AsyncIterator[str]:
    last_sequence = -1
    mode = request.query_params.get("mode")
    if mode not in {"holdem", "squid"}:
        mode = None
    while not await request.is_disconnected():
        try:
            data = snapshot(data_dir, mode=mode)
            current = int(data.get("last_sequence", 0))
            if current != last_sequence:
                last_sequence = current
                yield f"event: snapshot\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
        except sqlite3.Error as error:
            yield f"event: error\ndata: {json.dumps({'detail': str(error)})}\n\n"
        await asyncio.sleep(0.75)
