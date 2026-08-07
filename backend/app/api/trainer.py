"""翻前训练器 API：发题（next）+ 判分反馈（answer）。

设计为无状态：/next 返回的场景里带齐评分所需字段，前端答题时原样回传给 /answer。
无需登录、无需服务端会话即可"练一把→即时反馈"。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.llm import get_provider
from app.poker.preflop.coach import build_feedback
from app.poker.preflop.coach_llm import COACH_SYSTEM, build_coach_prompt
from app.poker.preflop.handclass import hand_class
from app.poker.preflop.ranges import load_spot
from app.poker.preflop.scenario import ACTION_LABELS, available_spots, card_glyph, generate_scenario
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

    hero_position = str(ps.meta.get("hero_position", req.position))
    opener_position = ps.meta.get("opener_position")
    opener_position = str(opener_position) if opener_position else None

    score = score_action(freqs, req.action)
    feedback = build_feedback(
        hero_position=hero_position,
        spot=req.spot,
        hand_class=cls,
        score=score,
        opener_position=opener_position,
    )
    return {
        "scenario_id": req.scenario_id,
        "hand_class": cls,
        "position": req.position,
        "hero_position": hero_position,
        "opener_position": opener_position,
        "spot": req.spot,
        "score": score,
        "feedback": feedback,
        "meta": ps.meta,
    }


class TrainerCoachRequest(BaseModel):
    format: str = Field("6max_100bb")
    spot: str = Field("RFI")
    position: str
    hero: List[str] = Field(..., description="2 张手牌，如 ['As','Kd']")
    action: str = Field(..., description="玩家所选动作")


@router.post("/trainer/coach")
def post_trainer_coach(req: TrainerCoachRequest) -> dict:
    """可选的 LLM 深度讲解：以引擎事实为准，解释「为什么这样打」。

    数字/对错仍由范围表得出并传给 LLM；LLM 只负责解释，不产生新数字。
    走网关（office），失败自动兜底到 OpenAI；两者都没配则 503。
    """
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
    if freqs is None or req.action not in freqs:
        raise HTTPException(status_code=400, detail=f"非法动作或类别：{req.action} / {cls}")

    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise HTTPException(status_code=503, detail="LLM 未配置（见 backend/.env.example）")

    hero_position = str(ps.meta.get("hero_position", req.position))
    opener_position = ps.meta.get("opener_position")
    opener_position = str(opener_position) if opener_position else None

    score = score_action(freqs, req.action)
    glyphs = " ".join(card_glyph(c) for c in req.hero)
    prompt = build_coach_prompt(
        hero_position=hero_position,
        spot=req.spot,
        hand_class=cls,
        hero_glyphs=glyphs,
        score=score,
        opener_position=opener_position,
    )
    try:
        text = provider.text(prompt, system=COACH_SYSTEM, max_tokens=500)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc

    return {
        "hand_class": cls,
        "position": req.position,
        "action": req.action,
        "coaching": text,
        "action_label": ACTION_LABELS.get(req.action, req.action),
    }
