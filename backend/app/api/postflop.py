"""翻后启发式训练器 API：发题（next）+ 判分反馈（answer）。

无状态：/next 返回的场景带齐判分所需字段（board/hero/对手范围/底池/角色），
前端答题时原样回传给 /answer。判分为启发式近似，响应里明确标注。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.poker.postflop.analyze import analyze_spot
from app.poker.postflop.coach import build_feedback
from app.poker.postflop.scenario import ACTION_LABELS, generate_postflop_scenario
from app.poker.postflop.scoring import score_postflop

router = APIRouter(tags=["postflop"])


@router.get("/trainer/postflop/next")
def get_postflop_next(
    role: Optional[str] = Query(None, pattern="^(pfr|caller)$"),
    seed: Optional[int] = Query(None),
) -> dict:
    """生成一道翻后决策题（HU 单加注底池翻牌）。role 可选 pfr / caller。"""
    try:
        scenario = generate_postflop_scenario(role=role, seed=seed)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"scenario": scenario}


class PostflopAnswerRequest(BaseModel):
    role: str = Field(..., pattern="^(pfr|caller)$")
    hero: List[str] = Field(..., description="2 张手牌")
    board: List[str] = Field(..., description="3 张翻牌")
    villain_range: str = Field(..., description="对手范围字符串（场景回传）")
    pot_bb: float
    bet_bb: Optional[float] = None
    action: str = Field(..., description="所选动作：check/bet/fold/call/raise")
    scenario_id: Optional[str] = None


@router.post("/trainer/postflop/answer")
def post_postflop_answer(req: PostflopAnswerRequest) -> dict:
    if len(req.hero) != 2 or len(req.board) != 3:
        raise HTTPException(status_code=400, detail="需要 2 张手牌 + 3 张翻牌")
    valid = {"pfr": {"check", "bet"}, "caller": {"fold", "call", "raise"}}[req.role]
    if req.action not in valid:
        raise HTTPException(
            status_code=400, detail=f"该角色不支持动作 {req.action!r}，可选：{sorted(valid)}"
        )
    try:
        texture, hand, equity, rec = analyze_spot(
            role=req.role,
            hero=req.hero,
            board=req.board,
            villain_range=req.villain_range,
            pot_bb=req.pot_bb,
            bet_bb=req.bet_bb,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    score = score_postflop(rec, req.action)
    feedback = build_feedback(texture, hand, rec, score)
    return {
        "scenario_id": req.scenario_id,
        "role": req.role,
        "hero_class": hand.get("made_label"),
        "texture": texture,
        "hand": hand,
        "equity": round(equity, 4),
        "recommendation": rec,
        "score": score,
        "feedback": feedback,
        "action_label": ACTION_LABELS.get(req.action, req.action),
        "approximate": True,
    }
