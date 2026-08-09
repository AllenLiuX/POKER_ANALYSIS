"""翻后启发式训练器 API：发题（next）+ 判分反馈（answer）。

无状态：/next 返回的场景带齐判分所需字段（board/hero/对手范围/底池/角色），
前端答题时原样回传给 /answer。判分为启发式近似，响应里明确标注。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.llm import get_provider
from app.poker.postflop.analyze import analyze_spot
from app.poker.postflop.coach import build_feedback
from app.poker.postflop.coach_llm import POSTFLOP_COACH_SYSTEM, build_postflop_coach_prompt
from app.poker.postflop.heuristics import size_label
from app.poker.postflop.scenario import (
    ACTION_LABELS,
    CONFIGS,
    _matchup_label,
    generate_postflop_scenario,
)
from app.poker.postflop.scoring import score_postflop
from app.poker.preflop.scenario import card_glyph

router = APIRouter(tags=["postflop"])


@router.get("/trainer/postflop/matchups")
def get_postflop_matchups() -> dict:
    """可选对位列表（不同位置对抗）。"""
    return {
        "matchups": [
            {
                "matchup": c["vs_spot"],
                "label": _matchup_label(str(c["pfr"]), str(c["caller"])),
                "pfr": c["pfr"],
                "caller": c["caller"],
            }
            for c in CONFIGS
        ]
    }


@router.get("/trainer/postflop/next")
def get_postflop_next(
    role: Optional[str] = Query(None, pattern="^(pfr|caller)$"),
    matchup: Optional[str] = Query(None, description="指定对位，如 BB_vs_BTN；缺省随机"),
    seed: Optional[int] = Query(None),
) -> dict:
    """生成一道翻后决策题（HU 单加注底池翻牌）。role 可选 pfr / caller；matchup 可指定对位。"""
    try:
        scenario = generate_postflop_scenario(role=role, matchup=matchup, seed=seed)
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
    size: Optional[str] = Field(None, description="下注/加注尺度桶 id（如 small/half/big/pot）")
    hero_range: Optional[str] = Field(None, description="英雄整段范围（场景回传，用于范围优势）")
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
            hero_range=req.hero_range,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    score = score_postflop(rec, req.action, req.size)
    if score.get("recommended_size"):
        score["recommended_size_label"] = size_label(req.action, score["recommended_size"])
    if score.get("size"):
        score["size_label"] = size_label(req.action, str(score["size"]))
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


class PostflopCoachRequest(BaseModel):
    role: str = Field(..., pattern="^(pfr|caller)$")
    hero: List[str] = Field(..., description="2 张手牌")
    board: List[str] = Field(..., description="3 张翻牌")
    villain_range: str = Field(..., description="对手范围字符串（场景回传）")
    pot_bb: float
    bet_bb: Optional[float] = None
    hero_position: str = Field("BB", description="英雄位置（展示用）")
    villain_position: str = Field("CO", description="对手位置（展示用）")
    action: str = Field(..., description="玩家所选动作")
    size: Optional[str] = Field(None, description="下注/加注尺度桶 id")


@router.post("/trainer/postflop/coach")
def post_postflop_coach(req: PostflopCoachRequest) -> dict:
    """可选的 LLM 深度讲解：以引擎事实为准，解释翻后「为什么这样打」。

    胜率/MDF/赔率/建议动作仍由启发式引擎得出并传给 LLM；LLM 只负责解释，不产生新数字。
    走网关（office），失败自动兜底到 OpenAI；两者都没配则 503。
    """
    if len(req.hero) != 2 or len(req.board) != 3:
        raise HTTPException(status_code=400, detail="需要 2 张手牌 + 3 张翻牌")
    valid = {"pfr": {"check", "bet"}, "caller": {"fold", "call", "raise"}}[req.role]
    if req.action not in valid:
        raise HTTPException(
            status_code=400, detail=f"该角色不支持动作 {req.action!r}，可选：{sorted(valid)}"
        )

    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")

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

    score = score_postflop(rec, req.action, req.size)
    if score.get("recommended_size"):
        score["recommended_size_label"] = size_label(req.action, score["recommended_size"])
    if score.get("size"):
        score["size_label"] = size_label(req.action, str(score["size"]))

    prompt = build_postflop_coach_prompt(
        role=req.role,
        hero_pos=req.hero_position,
        villain_pos=req.villain_position,
        board_glyphs=" ".join(card_glyph(c) for c in req.board),
        texture=texture,
        hand=hand,
        equity=equity,
        rec=rec,
        score=score,
    )
    try:
        text = provider.text(prompt, system=POSTFLOP_COACH_SYSTEM, max_tokens=500)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc

    return {
        "role": req.role,
        "action": req.action,
        "coaching": text,
        "action_label": ACTION_LABELS.get(req.action, req.action),
    }
