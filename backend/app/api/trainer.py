"""翻前训练器 API：发题（next）+ 判分反馈（answer）。

设计为无状态：/next 返回的场景里带齐评分所需字段，前端答题时原样回传给 /answer。
无需登录、无需服务端会话即可"练一把→即时反馈"。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.poker.preflop.coach import build_feedback
from app.poker.preflop.handclass import hand_class
from app.poker.preflop.ranges import load_spot
from app.poker.preflop.scenario import available_spots, generate_scenario
from app.poker.preflop.scoring import score_action

router = APIRouter(tags=["trainer"])


@router.get("/trainer/spots")
def get_trainer_spots(format: Optional[str] = Query(None)) -> dict:
    """训练器可选的 spot 列表（供前端做过滤器）。"""
    return {"spots": available_spots(format)}


@router.get("/trainer/next")
def get_trainer_next(
    format: Optional[str] = Query(None),
    spot: Optional[str] = Query(None),
    position: Optional[str] = Query(None),
    seed: Optional[int] = Query(None),
) -> dict:
    """生成一道翻前决策题（随机或按条件）。"""
    try:
        scenario = generate_scenario(fmt=format, spot=spot, position=position, seed=seed)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"scenario": scenario}


class TrainerAnswerRequest(BaseModel):
    format: str = Field("6max_100bb")
    spot: str = Field("RFI")
    position: str
    hero: List[str] = Field(..., description="2 张手牌，如 ['As','Kd']")
    action: str = Field(..., description="所选动作，如 fold / call / raise")
    scenario_id: Optional[str] = Field(None, description="回传场景 id，仅作前端追踪")


@router.post("/trainer/answer")
def post_trainer_answer(req: TrainerAnswerRequest) -> dict:
    """对一道题的作答进行判分，并返回中文教学反馈。"""
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

    score = score_action(freqs, req.action)
    feedback = build_feedback(
        position=req.position, spot=req.spot, hand_class=cls, score=score
    )
    return {
        "scenario_id": req.scenario_id,
        "hand_class": cls,
        "position": req.position,
        "spot": req.spot,
        "score": score,
        "feedback": feedback,
        "meta": ps.meta,
    }
