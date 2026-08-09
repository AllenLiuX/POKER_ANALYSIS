"""胜率 / 赔率 API。"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.poker.equity import ev_call, hero_equity, pot_odds

router = APIRouter(tags=["equity"])


class EquityRequest(BaseModel):
    hero: List[str] = Field(..., description="英雄 2 张手牌，如 ['As','Ad']")
    villain_range: str = Field(..., description="对手范围，如 'QQ+, AKs'")
    board: Optional[List[str]] = Field(None, description="公共牌 0/3/4/5 张")
    trials: int = Field(10000, ge=100, le=200000)
    seed: Optional[int] = None


class EquityResponse(BaseModel):
    win: float
    tie: float
    lose: float
    equity: float
    samples: int


@router.post("/equity", response_model=EquityResponse)
def post_equity(req: EquityRequest) -> EquityResponse:
    try:
        # 翻后（3~5 张公共牌）走精确加权枚举，翻前退回蒙特卡洛。
        result = hero_equity(
            hero=req.hero,
            villain_range=req.villain_range,
            board=req.board,
            trials=req.trials,
            seed=req.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EquityResponse(**result)


class PotOddsRequest(BaseModel):
    pot: float = Field(..., ge=0, description="跟注前底池（已含对手下注）")
    call: float = Field(..., ge=0, description="需跟注额")
    equity: Optional[float] = Field(None, ge=0, le=1, description="可选：给出则一并算 EV")


@router.post("/potodds")
def post_pot_odds(req: PotOddsRequest) -> dict:
    try:
        out = pot_odds(req.pot, req.call)
        if req.equity is not None:
            out["ev_call"] = ev_call(req.equity, req.pot, req.call)
            out["profitable"] = req.equity >= out["required_equity"]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return out
