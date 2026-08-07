"""阶段①：单张 WePoker 截图 → 观测事实（只抽看得见的，不做序列推理）。

铁律：LLM 只做视觉转写，产出的一切数字/序列都不作为判定真相；后续阶段②由引擎
按筹码守恒校验重建。这里只负责把截图变成结构化的"观测事实"。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from app.ingest.reconstruct import reconstruct_hand
from app.ingest.schemas import SCREENSHOT_TYPES, ObservationFacts
from app.llm.provider import get_provider

logger = logging.getLogger(__name__)

_SUIT_GLYPH_TO_LETTER = {"♠": "s", "♥": "h", "♦": "d", "♣": "c", "S": "s", "H": "h", "D": "d", "C": "c"}
_RANKS = "23456789TJQKA"


class LLMUnavailable(RuntimeError):
    """网关与 OpenAI 均未配置时抛出，供 API 层映射为 503。"""


EXTRACT_SYSTEM = (
    "你是一名严谨的德州扑克截图转写员，只转写截图里**肉眼可见的事实**，绝不推理、绝不编造。"
    "输入是一张 WePoker（微扑克）中文界面的对局截图。你必须输出**严格 JSON**，不要任何解释文字、不要 markdown 代码块。"
)

_PROMPT = """请把这张 WePoker 截图转写为观测事实 JSON。只写你**确实看得见**的内容，看不清/没有的字段一律省略或置 null，绝不猜测下注顺序。

牌面表示规范：
- 单张牌用「点数+花色字母」，点数 A K Q J T 9 8 7 6 5 4 3 2（10 一律写成 T），花色 s=♠ h=♥ d=♦ c=♣。例：A♠→"As"，10♥→"Th"。
- 看不到的底牌（牌背）留空数组。

请判断截图类型 screenshot_type：
- "hand_replay"：手牌回放/详情，逐街标注了每位玩家的动作与金额（底部常有 x/x 播放条）。这类信息最可靠。
- "result_summary"：仅结算画面，只有最终状态（如都显示 all-in），没有逐步动作。
- "unknown"：无法判断。

对每位玩家（players 数组）尽量填：
- alias（昵称）、position（由「大盲/小盲/D」标记 + 座位顺序推断为 BB/SB/BTN/UTG/MP/CO，拿不准就 null）、
- is_hero（是否为截图主人「我」，通常有高亮/视角，拿不准就 false）、
- hole_cards（仅摊牌可见时）、net（右侧净额，输为负）、made_hand（摊牌牌型文字，如「葫芦」）、
- actions_raw（把该玩家一行的逐街动作原文照抄，如 "加注32 → 下注38 → 跟注188 → Allin941"）、
- visible_actions（出现过的动作标签数组，取值：加注/下注/跟注/过牌/弃牌/全下）。

顶层字段：hand_id、blinds（如 "2/4(1)"）、board（已知公共牌数组）、pot（底池数字）、hero_seat、extraction_confidence（0~1，你对本次转写整体把握）、notes（简短备注，如"结算画面无逐步动作"）。

如果这张图**并不是**扑克对局/手牌截图，或你完全无法读出任何牌局信息，也要返回合法 JSON：
{"screenshot_type":"unknown","players":[],"board":[],"extraction_confidence":0,"notes":"简述原因，如：这不是扑克手牌截图 / 画面不清晰无法识别"}

务必只输出 JSON 对象本身，不要任何额外文字、不要 markdown 代码块。"""


def build_extract_prompt() -> str:
    return _PROMPT


def parse_json_block(text: str) -> Dict:
    """从模型输出里稳健地取出 JSON 对象（容忍 ```json 代码块、前后噪声）。解析失败抛 ValueError。"""
    if not text or not text.strip():
        raise ValueError("模型返回空内容")
    s = text.strip()
    # 去掉 ```json ... ``` 或 ``` ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    # 退而求其次：截取第一个 { 到最后一个 }
    if not s.startswith("{"):
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON：{exc}") from exc


def _try_parse(text: str) -> Optional[Dict]:
    """尽力解析，失败返回 None（不抛错）。"""
    try:
        return parse_json_block(text)
    except ValueError:
        return None


def _normalize_card(raw: object) -> Optional[str]:
    """'10♠' / 'As' / 'a s' → 'Ts' / 'As'；无法识别返回 None。"""
    if not isinstance(raw, str):
        return None
    c = raw.strip().replace(" ", "")
    if not c:
        return None
    # 花色字形 → 字母
    for glyph, letter in _SUIT_GLYPH_TO_LETTER.items():
        c = c.replace(glyph, letter)
    if c[:2] == "10":
        c = "T" + c[2:]
    if len(c) < 2:
        return None
    rank, suit = c[0].upper(), c[1].lower()
    if rank not in _RANKS or suit not in "shdc":
        return None
    return rank + suit


def _normalize_cards(raw: object) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        card = _normalize_card(item)
        if card and card not in out:
            out.append(card)
    return out


def _coerce_float(v: object) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        if m:
            return float(m.group())
    return None


def _normalize(data: Dict) -> Dict:
    """把模型输出对齐到 ObservationFacts 结构，做牌面/数字的宽松清洗。"""
    stype = str(data.get("screenshot_type") or "unknown").strip().lower()
    if stype not in SCREENSHOT_TYPES:
        stype = "unknown"

    players_out: List[Dict] = []
    for p in data.get("players") or []:
        if not isinstance(p, dict):
            continue
        players_out.append(
            {
                "seat": p.get("seat") if isinstance(p.get("seat"), int) else None,
                "alias": (str(p["alias"]).strip() if p.get("alias") else None),
                "position": (str(p["position"]).strip().upper() if p.get("position") else None),
                "is_hero": bool(p.get("is_hero", False)),
                "hole_cards": _normalize_cards(p.get("hole_cards")),
                "stack_end": _coerce_float(p.get("stack_end")),
                "net": _coerce_float(p.get("net")),
                "made_hand": (str(p["made_hand"]).strip() if p.get("made_hand") else None),
                "actions_raw": (str(p["actions_raw"]).strip() if p.get("actions_raw") else None),
                "visible_actions": [str(a).strip() for a in (p.get("visible_actions") or []) if str(a).strip()],
            }
        )

    conf = _coerce_float(data.get("extraction_confidence"))
    conf = min(1.0, max(0.0, conf)) if conf is not None else 0.0

    return {
        "screenshot_type": stype,
        "hand_id": (str(data["hand_id"]).strip() if data.get("hand_id") else None),
        "blinds": (str(data["blinds"]).strip() if data.get("blinds") else None),
        "board": _normalize_cards(data.get("board")),
        "pot": _coerce_float(data.get("pot")),
        "hero_seat": data.get("hero_seat") if isinstance(data.get("hero_seat"), int) else None,
        "players": players_out,
        "extraction_confidence": conf,
        "notes": (str(data["notes"]).strip() if data.get("notes") else None),
    }


def _vision_once(image: bytes, model: Optional[str], max_tokens: int, log_id: Optional[str]) -> str:
    provider = get_provider()
    return provider.vision(
        build_extract_prompt(),
        images=[image],
        system=EXTRACT_SYSTEM,
        max_tokens=max_tokens,
        log_id=log_id,
        model=model,
    )


def _unrecognized(raw: str, reason: str) -> Dict:
    """无法识别为扑克手牌时的优雅返回（不抛错），由前端给出友好提示。"""
    facts = ObservationFacts(screenshot_type="unknown", notes=reason)
    return {
        "stage": "observations",
        "recognized": False,
        "facts": facts.model_dump(),
        "reconstruction": None,
        "raw_model_output": raw,
        "note": "未识别为可解析的微扑克手牌截图。",
    }


def extract_observations(image: bytes, *, mime: Optional[str] = None, log_id: Optional[str] = None) -> Dict:
    """单张截图 → 观测事实 + 重建。LLM 不可用时抛 LLMUnavailable；无法识别时优雅降级（不抛错）。"""
    provider = get_provider()
    if not (provider.gateway_ready or provider.openai_ready):
        raise LLMUnavailable(
            "LLM 未配置：请在 backend/.env 设置 MODEL_GATEWAY_KEY（网关）或 OPENAI_API_KEY（兜底）。"
        )

    # 首选视觉模型（默认 gemini-flash）；空/非 JSON 时用更稳的 gpt-4o 重试一次。
    raw = ""
    data: Optional[Dict] = None
    try:
        raw = _vision_once(image, model=None, max_tokens=2000, log_id=log_id)
        data = _try_parse(raw)
    except Exception as exc:  # noqa: BLE001 — 首选失败不致命，走重试
        logger.warning("[ingest] primary vision failed: %s", exc)

    if data is None:
        try:
            raw2 = _vision_once(image, model="gpt-4o", max_tokens=3000, log_id=log_id)
            parsed2 = _try_parse(raw2)
            if parsed2 is not None:
                data, raw = parsed2, raw2
            elif raw2:
                raw = raw2
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ingest] gpt-4o retry failed: %s", exc)

    if data is None:
        return _unrecognized(
            raw,
            "模型未能从这张图中读出结构化的牌局信息——可能不是微扑克「手牌回放/详情」截图，"
            "或画面不清晰。请上传底部带播放条、逐街标注了各玩家动作与净额的手牌回放截图。",
        )

    facts_model = ObservationFacts.model_validate(_normalize(data))
    facts = facts_model.model_dump()

    recognized = bool(
        facts.get("players") or facts.get("board") or facts.get("pot") is not None
    )
    if not recognized:
        reason = facts.get("notes") or (
            "这张图似乎不是微扑克手牌截图，或画面不清晰。请上传「手牌回放/详情」截图"
            "（底部有播放条、逐街动作与净额）。"
        )
        out = _unrecognized(raw, str(reason))
        out["facts"] = facts  # 保留模型给出的（可能部分）字段
        return out

    reconstruction = reconstruct_hand(facts)
    return {
        "stage": "observations",
        "recognized": True,
        "facts": facts,
        "reconstruction": reconstruction,
        "raw_model_output": raw,
        "note": "阶段①观测提取 + 阶段②下注序列重建（引擎校验净额守恒/底池一致）。数字/合法性以引擎为准。",
    }
