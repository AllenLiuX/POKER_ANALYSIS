"""翻前范围表 + 打分 API。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.poker.preflop.handclass import RANK_ORDER, grid_cells, hand_class
from app.poker.preflop.ranges import list_spots, load_spot
from app.poker.preflop.scoring import score_action

router = APIRouter(tags=["ranges"])


@router.get("/ranges")
def get_ranges() -> dict:
    return {"spots": list_spots()}


@router.get("/ranges/{fmt}/{spot}/{position}")
def get_range_grid(fmt: str, spot: str, position: str) -> dict:
    try:
        ps = load_spot(fmt, spot, position)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cells = []
    for cell in grid_cells():
        cls = cell["hand_class"]
        cells.append({**cell, "freqs": ps.frequencies.get(cls, {})})
    return {
        "meta": ps.meta,
        "actions": ps.actions,
        "ranks": list(RANK_ORDER),
        "cells": cells,
    }


class PreflopScoreRequest(BaseModel):
    format: str = Field("6max_100bb")
    spot: str = Field("RFI")
    position: str
    hero: List[str] = Field(..., description="2 张手牌，如 ['As','Kd']")
    action: str = Field(..., description="fold / call / raise")


@router.post("/preflop/score")
def post_preflop_score(req: PreflopScoreRequest) -> dict:
    if len(req.hero) != 2:
        raise HTTPException(status_code=400, detail="hero 必须是 2 张手牌")
    try:
        cls = hand_class(req.hero[0], req.hero[1])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ps = load_spot(req.format, req.spot, req.position)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    freqs = ps.frequencies.get(cls)
    if freqs is None:
        raise HTTPException(status_code=400, detail=f"无该类别频率：{cls}")
    if req.action not in freqs:
        raise HTTPException(
            status_code=400,
            detail=f"该 spot 不支持动作 {req.action!r}，可选：{sorted(freqs.keys())}",
        )

    result = score_action(freqs, req.action)
    result["hand_class"] = cls
    result["meta"] = ps.meta
    return result
