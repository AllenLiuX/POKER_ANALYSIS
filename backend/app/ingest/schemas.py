"""观测事实（阶段①）数据模型。

只承载"截图里看得见的事实"，不含任何推理出的下注序列。字段尽量宽松（都可空），
因为不同截图（回放 vs 结算）能看到的信息差别很大。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

SCREENSHOT_TYPES = ("hand_replay", "result_summary", "unknown")

# actions_by_street 的合法街道键（按顺序）
STREET_KEYS = ("preflop", "flop", "turn", "river")


class PlayerObs(BaseModel):
    seat: Optional[int] = Field(None, description="座位号（从上/顺时针，若可辨认）")
    alias: Optional[str] = Field(None, description="昵称")
    position: Optional[str] = Field(None, description="位置：BTN/SB/BB/UTG/MP/CO（由大盲/小盲/D 标记推断）")
    is_hero: bool = Field(False, description="是否为截图主人（我）")
    hole_cards: List[str] = Field(default_factory=list, description="底牌（摊牌可见，否则空）")
    stack_end: Optional[float] = Field(None, description="本手结束后的筹码")
    net: Optional[float] = Field(None, description="本手净额（右侧显示，输为负；通常已含保险盈亏）")
    insurance: Optional[float] = Field(
        None,
        description="保险净额（该玩家买保险/得赔付带来的净额：赔付为正、保费为负）。用于把净额还原为纯桌面结果对账。",
    )
    made_hand: Optional[str] = Field(None, description="摊牌牌型文字（如 葫芦/两对）")
    actions_raw: Optional[str] = Field(None, description='逐街动作原文，如 "加注32 → 下注38 → 跟注188 → Allin941"')
    # 分街动作：{"preflop": ["加注32"], "flop": ["下注38"], "turn": ["跟注188"], "river": ["Allin941"]}
    # 由模型按截图回放的分街布局归位；缺失/为空时引擎退回按 actions_raw 的位置推断。
    actions_by_street: Optional[Dict[str, List[str]]] = Field(
        None, description="按街分组的动作原文（preflop/flop/turn/river），比 actions_raw 更可靠"
    )
    visible_actions: List[str] = Field(default_factory=list, description="可见动作标签（加注/跟注/全下/弃牌/过牌）")


class ObservationFacts(BaseModel):
    screenshot_type: str = Field("unknown", description="hand_replay | result_summary | unknown")
    hand_id: Optional[str] = None
    blinds: Optional[str] = Field(None, description='盲注/前注文字，如 "2/4(1)"')
    board: List[str] = Field(default_factory=list, description="公共牌（已知的，标准 2 字符）")
    pot: Optional[float] = None
    hero_seat: Optional[int] = None
    players: List[PlayerObs] = Field(default_factory=list)
    extraction_confidence: float = Field(0.0, ge=0.0, le=1.0)
    notes: Optional[str] = None
