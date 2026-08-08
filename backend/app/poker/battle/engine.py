"""HU 对战状态机 + 编排。

authoritative 状态 = deal_seed + hero_pos + history（动作序列）。每次 /act 由这三者
**重放**出完整状态（含各家底牌、判分），再应用英雄新动作、跑对手到英雄行动或摊牌。
数值(底池/筹码)全部由重放重算，不信任前端回传，天然防篡改。
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import eval7

from app.poker.battle import grade as G
from app.poker.battle import policy as P
from app.poker.battle import ranges as R
from app.poker.battle.deal import (
    BB,
    OPEN_TO,
    SB,
    START_STACK,
    THREEBET_TO,
    deal,
    full_board,
    other,
)
from app.poker.evaluate import to_eval7
from app.poker.postflop.heuristics import BET_SIZE_BUCKETS
from app.poker.preflop.handclass import hand_class
from app.poker.preflop.scenario import card_glyph

ACTION_LABELS = {"fold": "弃牌", "call": "跟注", "raise": "加注", "check": "过牌", "bet": "下注"}
STREETS = ["preflop", "flop", "turn", "river"]
RAISE_BUCKETS = [
    {"id": "small", "label": "加注 (底池)", "frac": 1.0},
    {"id": "big", "label": "大加注 (1.5×池)", "frac": 1.5},
]


def _r1(x: float) -> float:
    return round(x + 1e-9, 1)


@dataclass
class Battle:
    deal_seed: int
    hero_pos: str  # BTN / BB
    d: Dict[str, object] = field(default_factory=dict)  # 发牌结果
    street: str = "preflop"
    invested: Dict[str, float] = field(default_factory=dict)  # {'hero','villain'} 累计投入
    aggr_this_street: int = 0
    acted_this_street: int = 0
    to_act: str = "hero"
    board: List[str] = field(default_factory=list)  # 已翻开公共牌
    history: List[Dict[str, object]] = field(default_factory=list)
    grades: List[Dict[str, object]] = field(default_factory=list)
    result: Optional[Dict[str, object]] = None
    hero_range: str = ""
    villain_range: str = ""
    message: str = ""

    # ---------- 便捷 ----------
    @property
    def villain_pos(self) -> str:
        return other(self.hero_pos)

    def pos_of(self, actor: str) -> str:
        return self.hero_pos if actor == "hero" else self.villain_pos

    def cards(self, actor: str) -> List[str]:
        return list(self.d["hero" if actor == "hero" else "villain"])  # type: ignore[index]

    def pot(self) -> float:
        return _r1(self.invested["hero"] + self.invested["villain"])

    def max_inv(self) -> float:
        return max(self.invested.values())

    def to_call(self, actor: str) -> float:
        return _r1(max(0.0, self.max_inv() - self.invested[actor]))

    def stack(self, actor: str) -> float:
        return _r1(START_STACK - self.invested[actor])

    def _first_actor(self, street: str) -> str:
        btn = "hero" if self.hero_pos == "BTN" else "villain"
        bb = "hero" if self.hero_pos == "BB" else "villain"
        return btn if street == "preflop" else bb

    # ---------- 合法动作 / 尺度 ----------
    def legal_actions(self, actor: str) -> List[str]:
        if self.result is not None:
            return []
        tc = self.to_call(actor)
        if self.street == "preflop" and self.aggr_this_street == 0:
            return ["fold", "raise"]  # 开池节点：加注或弃牌（不设跛入）
        if tc <= 1e-9:
            acts = ["check"]
            if self.stack(actor) > 1e-9:
                acts.append("bet")
            return acts
        acts = ["fold", "call"]
        if self.aggr_this_street < 2 and self.stack(actor) > tc + 1e-9:
            acts.append("raise")
        return acts

    def bet_size_options(self, actor: str) -> List[Dict[str, object]]:
        if self.street == "preflop":
            return []
        pot = self.pot()
        st = self.stack(actor)
        out = []
        seen = set()
        for b in BET_SIZE_BUCKETS:
            amt = min(_r1(pot * float(b["frac"] if "frac" in b else b["fraction"])), st)
            if amt <= 0 or amt in seen:
                continue
            seen.add(amt)
            out.append({"id": b["id"], "label": b["label"], "amount_bb": amt})
        return out

    def raise_size_options(self, actor: str) -> List[Dict[str, object]]:
        if self.street == "preflop":
            return []  # 翻前加注尺度固定（开池/3bet），无需选
        tc = self.to_call(actor)
        pot_after = self.pot() + tc
        st_cap = START_STACK - self.invested[actor]
        out = []
        seen = set()
        for b in RAISE_BUCKETS:
            raise_to = min(_r1(self.invested[actor] + tc + b["frac"] * pot_after), _r1(self.invested[actor] + st_cap))
            if raise_to <= self.max_inv() + 1e-9 or raise_to in seen:
                continue
            seen.add(raise_to)
            out.append({"id": b["id"], "label": b["label"], "amount_bb": raise_to})
        return out

    # ---------- 应用一个动作 ----------
    def apply(self, actor: str, action: str, size: Optional[str], *, do_grade: bool) -> None:
        if self.result is not None:
            raise ValueError("手牌已结束")
        if actor != self.to_act:
            raise ValueError(f"非 {actor} 行动轮")
        legal = self.legal_actions(actor)
        if action not in legal:
            # 兜底：非法则退化为最保守合法动作（对手策略越界时）
            action = "call" if "call" in legal else ("check" if "check" in legal else "fold")
            size = None

        amount_to = self.invested[actor]  # 该动作后本家累计投入
        hero_grade: Optional[Dict[str, object]] = None
        if do_grade and actor == "hero":
            hero_grade = self._grade_hero(action, size)
            self.grades.append(hero_grade)

        if action == "fold":
            self._settle_fold(folder=actor)
            self._log(actor, action, size, self.invested[actor], hero_grade)
            return
        elif action == "check":
            pass
        elif action == "call":
            amount_to = min(self.max_inv(), START_STACK)
            self.invested[actor] = _r1(amount_to)
        elif action == "bet":
            amount_to = self._bet_to(actor, size)
            self.invested[actor] = amount_to
            self.aggr_this_street += 1
        elif action == "raise":
            amount_to = self._raise_to(actor, size)
            self.invested[actor] = amount_to
            self.aggr_this_street += 1
        else:
            raise ValueError(f"未知动作 {action}")

        self.acted_this_street += 1
        self._log(actor, action, size, self.invested[actor], hero_grade)
        self._advance_betting()

    def _bet_to(self, actor: str, size: Optional[str]) -> float:
        opts = {o["id"]: o for o in self.bet_size_options(actor)}
        chosen = opts.get(size or "half") or (next(iter(opts.values())) if opts else None)
        if not chosen:
            return _r1(self.invested[actor] + min(self.pot() * 0.5, self.stack(actor)))
        return _r1(self.invested[actor] + float(chosen["amount_bb"]))

    def _raise_to(self, actor: str, size: Optional[str]) -> float:
        if self.street == "preflop":
            raise_to = OPEN_TO if self.aggr_this_street == 0 else THREEBET_TO
            return _r1(min(raise_to, START_STACK))
        opts = {o["id"]: o for o in self.raise_size_options(actor)}
        chosen = opts.get(size or "big") or (next(iter(opts.values())) if opts else None)
        if not chosen:
            return _r1(min(self.max_inv() + self.pot(), START_STACK))
        return _r1(float(chosen["amount_bb"]))

    # ---------- 结算 / 推进 ----------
    def _settle_fold(self, folder: str) -> None:
        winner = "villain" if folder == "hero" else "hero"
        pot = self.pot()
        hero_net = _r1((pot - self.invested["hero"]) if winner == "hero" else -self.invested["hero"])
        self.result = self._make_result(reason="fold", winner=winner, hero_net=hero_net)

    def _advance_betting(self) -> None:
        if self.result is not None:
            return
        equal = abs(self.invested["hero"] - self.invested["villain"]) < 1e-9
        both_acted = self.acted_this_street >= 2
        if not (equal and both_acted):
            self.to_act = other_actor(self.to_act)
            return
        # 本街下注结束
        allin = self.stack("hero") <= 1e-9 or self.stack("villain") <= 1e-9
        if self.street == "river" or allin:
            self._runout_and_showdown()
            return
        self._next_street()

    def _next_street(self) -> None:
        idx = STREETS.index(self.street)
        self.street = STREETS[idx + 1]
        self._reveal_for_street()
        self.aggr_this_street = 0
        self.acted_this_street = 0
        self.to_act = self._first_actor(self.street)
        if self.street == "flop":  # 翻前收官，锁定双方翻后范围
            self._assign_postflop_ranges()

    def _reveal_for_street(self) -> None:
        if self.street == "flop":
            self.board = list(self.d["flop"])  # type: ignore[index]
        elif self.street == "turn":
            self.board = list(self.d["flop"]) + [str(self.d["turn"])]  # type: ignore[index]
        elif self.street == "river":
            self.board = list(self.d["flop"]) + [str(self.d["turn"]), str(self.d["river"])]  # type: ignore[index]

    def _runout_and_showdown(self) -> None:
        self.board = full_board(self.d)  # 直接翻到河牌
        self.street = "river"
        self._showdown()

    def _assign_postflop_ranges(self) -> None:
        if self.aggr_this_street_pre() >= 2:  # 走到 3-bet：3bettor=BB，caller=BTN
            btn_range = R.vs_3bet_call_range_str()
            bb_range = R.bb_3bet_range_str()
        else:  # 单加注底池：BTN 开池、BB 跟注
            btn_range = R.btn_open_range_str()
            bb_range = R.bb_call_range_str()
        if self.hero_pos == "BTN":
            self.hero_range, self.villain_range = btn_range, bb_range
        else:
            self.hero_range, self.villain_range = bb_range, btn_range

    def aggr_this_street_pre(self) -> int:
        """翻前总加注数（从 history 数，因为进入 flop 后 aggr_this_street 已清零）。"""
        return sum(1 for e in self.history if e["street"] == "preflop" and e["action"] in ("raise",))

    def _showdown(self) -> None:
        board5 = full_board(self.d)
        hero_c = to_eval7(self.cards("hero") + board5)
        vill_c = to_eval7(self.cards("villain") + board5)
        hs = eval7.evaluate(hero_c)
        vs = eval7.evaluate(vill_c)
        pot = self.pot()
        if hs > vs:
            winner = "hero"
            hero_net = _r1(pot - self.invested["hero"])
        elif hs < vs:
            winner = "villain"
            hero_net = _r1(-self.invested["hero"])
        else:
            winner = "split"
            hero_net = _r1(pot / 2 - self.invested["hero"])
        self.result = self._make_result(reason="showdown", winner=winner, hero_net=hero_net)

    def _make_result(self, *, reason: str, winner: str, hero_net: float) -> Dict[str, object]:
        villain = self.cards("villain")
        vcls = hand_class(villain[0], villain[1])
        board5 = full_board(self.d)
        review = self._build_review(hero_net=hero_net, reason=reason, winner=winner)
        return {
            "reason": reason,
            "winner": winner,
            "hero_net": hero_net,
            "pot_bb": self.pot(),
            "villain": villain,
            "villain_glyphs": [card_glyph(c) for c in villain],
            "villain_class": vcls,
            "board": board5 if reason == "showdown" else self.board,
            "review": review,
        }

    def _build_review(self, *, hero_net: float, reason: str, winner: str) -> Dict[str, object]:
        mistakes = [g for g in self.grades if g.get("grade") == "mistake"]
        max_sev = max((float(g.get("severity", 0.0)) for g in self.grades), default=0.0)
        is_problem = bool(mistakes)
        is_big = bool(mistakes) and (max_sev >= 0.5 or hero_net <= -8.0)
        return {
            "is_problem": is_problem,
            "is_big": is_big,
            "mistakes": len(mistakes),
            "max_severity": round(max_sev, 3),
            "hero_net": hero_net,
        }

    # ---------- 判分 ----------
    def _grade_hero(self, action: str, size: Optional[str]) -> None:
        actor = "hero"
        cls = hand_class(*self.cards(actor))
        if self.street == "preflop":
            if self.aggr_this_street == 0:  # 开池（BTN）
                g = G.grade_preflop(cls=cls, freqs=R.btn_open_freqs(cls), spot="RFI", action=action)
            elif self.aggr_this_street == 1 and self.hero_pos == "BB":  # 防守开池
                g = G.grade_preflop(cls=cls, freqs=R.bb_defend_freqs(cls), spot="vs_RFI", action=action)
            else:  # 面对 3-bet：无范围数据
                g = G.grade_preflop_ungrounded(cls=cls, spot="vs_3bet", action=action)
        else:
            g = G.grade_postflop(
                street=self.street,
                hero=self.cards(actor),
                board=self.board,
                hero_range=self.hero_range,
                villain_range=self.villain_range,
                pot_bb=self.pot(),
                bet_bb=(self.to_call(actor) if self.to_call(actor) > 0 else None),
                action=action,
                size=size,
            )
        g["hand_class"] = cls
        return g

    # ---------- 日志 ----------
    def _log(
        self,
        actor: str,
        action: str,
        size: Optional[str],
        amount_to: float,
        hero_grade: Optional[Dict[str, object]] = None,
    ) -> None:
        ev: Dict[str, object] = {
            "actor": actor,
            "pos": self.pos_of(actor),
            "street": self.street,
            "action": action,
            "size": size,
            "amount_to": _r1(amount_to),
            "label": ACTION_LABELS.get(action, action),
        }
        if hero_grade is not None:
            ev["hero_grade"] = hero_grade  # 嵌入判分，供重放回收（避免二次蒙特卡洛）
        self.history.append(ev)

    # ---------- 对手自动行动 ----------
    def advance_villain(self) -> None:
        """跑对手直到轮到英雄行动或手牌结束。"""
        guard = 0
        while self.result is None and self.to_act == "villain":
            guard += 1
            if guard > 40:
                raise RuntimeError("对战推进超出预期步数")
            self._villain_step()

    def _villain_step(self) -> None:
        actor = "villain"
        cls = hand_class(*self.cards(actor))
        rng = random.Random(_node_seed(self.deal_seed, self.street, len(self.history)))
        if self.street == "preflop":
            if self.aggr_this_street == 0:
                situation = "open"
            elif self.aggr_this_street == 1 and self.villain_pos == "BB":
                situation = "defend"
            else:
                situation = "vs_3bet"
            action = P.villain_preflop(situation=situation, cls=cls, rng=rng)
            size = None
        else:
            action, size = P.villain_postflop(
                villain=self.cards(actor),
                board=self.board,
                villain_range=self.villain_range,
                hero_range=self.hero_range,
                pot_bb=self.pot(),
                to_call=self.to_call(actor),
                rng=rng,
            )
        self.apply(actor, action, size, do_grade=False)

    # ---------- 序列化（对手底牌仅摊牌时下发）----------
    def to_public(self) -> Dict[str, object]:
        actor = "hero"
        complete = self.result is not None
        legal = [] if complete else self.legal_actions(actor)
        board_glyphs = [card_glyph(c) for c in self.board]
        villain_last = next(
            (e for e in reversed(self.history) if e["actor"] == "villain"), None
        )
        hero = self.cards("hero")
        return {
            "deal_seed": self.deal_seed,
            "hero_pos": self.hero_pos,
            "villain_pos": self.villain_pos,
            "blinds": {"sb": SB, "bb": BB},
            "start_stack": START_STACK,
            "street": self.street,
            "board": self.board,
            "board_glyphs": board_glyphs,
            "hero": hero,
            "hero_glyphs": [card_glyph(c) for c in hero],
            "hero_class": hand_class(hero[0], hero[1]),
            "pot_bb": self.pot(),
            "to_call_bb": self.to_call(actor),
            "hero_stack_bb": self.stack("hero"),
            "villain_stack_bb": self.stack("villain"),
            "to_act": None if complete else self.to_act,
            "complete": complete,
            "available_actions": legal,
            "action_labels": {a: ACTION_LABELS[a] for a in legal},
            "bet_sizes": self.bet_size_options(actor) if ("bet" in legal) else [],
            "raise_sizes": self.raise_size_options(actor) if ("raise" in legal) else [],
            "history": self.history,
            "villain_last": villain_last,
            "message": self._message(),
            "grades": self.grades,
            "result": self.result,
        }

    def _message(self) -> str:
        if self.result is not None:
            r = self.result
            if r["reason"] == "fold":
                who = "对手" if r["winner"] == "hero" else "你"
                return f"{who}弃牌，本手结束。"
            outcome = {"hero": "你赢了", "villain": "对手赢了", "split": "平分底池"}[str(r["winner"])]
            return f"摊牌：{outcome}。"
        last = next((e for e in reversed(self.history) if e["actor"] == "villain"), None)
        if self.street == "preflop" and not self.history:
            return "翻前：轮到你行动。"
        if last and last["street"] == self.street:
            amt = last["amount_to"]
            act = last["label"]
            extra = f"到 {amt}bb" if last["action"] in ("raise", "bet", "call") else ""
            return f"对手在{self.street_cn()} {act}{extra}，轮到你。"
        return f"{self.street_cn()}：轮到你行动。"

    def street_cn(self) -> str:
        return {"preflop": "翻前", "flop": "翻牌", "turn": "转牌", "river": "河牌"}.get(self.street, self.street)


def other_actor(a: str) -> str:
    return "villain" if a == "hero" else "hero"


def _node_seed(deal_seed: int, street: str, n: int) -> int:
    return zlib.crc32(f"{deal_seed}|{street}|{n}".encode()) & 0xFFFFFFFF


def _fresh(deal_seed: int, hero_pos: str) -> Battle:
    d = deal(deal_seed)
    b = Battle(deal_seed=deal_seed, hero_pos=hero_pos, d=d)
    # 翻前盲注：BTN 投 0.5，BB 投 1.0
    btn = "hero" if hero_pos == "BTN" else "villain"
    bb = "hero" if hero_pos == "BB" else "villain"
    b.invested = {btn: SB, bb: BB}
    # 统一 key 顺序
    b.invested = {"hero": b.invested.get("hero", 0.0), "villain": b.invested.get("villain", 0.0)}
    b.to_act = b._first_actor("preflop")
    return b


def new_hand(deal_seed: int, hero_pos: str) -> Battle:
    """开一手新牌，跑对手到轮到英雄行动或结束。"""
    if hero_pos not in ("BTN", "BB"):
        raise ValueError("hero_pos 只能是 BTN / BB")
    b = _fresh(deal_seed, hero_pos)
    b.advance_villain()
    return b


def replay(deal_seed: int, hero_pos: str, history: List[Dict[str, object]]) -> Battle:
    """从动作序列重放出当前状态（含英雄判分）。history 里的对手动作按记录重放。"""
    b = _fresh(deal_seed, hero_pos)
    for e in history:
        actor = str(e["actor"])
        action = str(e["action"])
        size = e.get("size")
        size = str(size) if size is not None else None
        if b.result is not None:
            break
        # 只有轮到该 actor 时才应用；否则说明 history 不一致（忽略以求稳）
        if b.to_act != actor:
            continue
        # 重放不重算判分：直接回收 history 事件里嵌入的判分，避免二次蒙特卡洛（性能关键）。
        b.apply(actor, action, size, do_grade=False)
        embedded = e.get("hero_grade")
        if actor == "hero" and isinstance(embedded, dict) and b.history:
            b.history[-1]["hero_grade"] = embedded
            b.grades.append(embedded)
    return b


def act(deal_seed: int, hero_pos: str, history: List[Dict[str, object]], action: str, size: Optional[str]) -> Battle:
    """应用英雄动作后跑对手到下一次英雄行动或结束。"""
    b = replay(deal_seed, hero_pos, history)
    if b.result is not None:
        raise ValueError("手牌已结束")
    if b.to_act != "hero":
        raise ValueError("当前不是你的行动轮")
    b.apply("hero", action, size, do_grade=True)
    b.advance_villain()
    return b
