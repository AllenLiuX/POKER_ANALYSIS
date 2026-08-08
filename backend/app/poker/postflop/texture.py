"""翻牌面纹理分类：驱动 c-bet 频率/尺度与防守判断。

输出的 wetness（0~1）是一个透明的启发式加权分，不是 solver 结论：
  同花性、连接性、成对都会影响它，UI 会如实标注"启发式"。
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from app.poker.cards import normalize_cards

RANK_VAL = {r: i for i, r in enumerate("23456789TJQKA", start=2)}  # 2..14(A)


def _straightiness(ranks: List[int]) -> int:
    """任意 5 连续 rank 窗口内，牌面覆盖到的不同 rank 数的最大值（含 A 作 1）。

    3 表示三张连张/极易成顺，2 一般，1 基本无连接。
    """
    uniq = set(ranks)
    if 14 in uniq:
        uniq.add(1)  # A 可作 1（轮子）
    best = 0
    for low in range(1, 11):  # 窗口起点 1..10 -> 覆盖 A2345 .. TJQKA
        window = set(range(low, low + 5))
        best = max(best, len(uniq & window))
    return best


def classify_board(board: List[str]) -> Dict[str, object]:
    cards = normalize_cards(board)
    if not 3 <= len(cards) <= 5:
        raise ValueError("公共牌必须是 3~5 张（翻牌/转牌/河牌）")
    ranks = sorted((RANK_VAL[c[0]] for c in cards), reverse=True)
    suits = [c[1] for c in cards]

    rank_counts = Counter(ranks)
    paired = any(v >= 2 for v in rank_counts.values())
    trips = any(v >= 3 for v in rank_counts.values())

    suit_counts = Counter(suits)
    max_suit = max(suit_counts.values())
    # 3+ 同花即进入"同花区"（翻牌=成花可能；转/河可能已成花）。3 张时与旧行为一致。
    if max_suit >= 3:
        suitedness = "monotone"
    elif max_suit == 2:
        suitedness = "two-tone"
    else:
        suitedness = "rainbow"

    high = ranks[0]
    straightiness = _straightiness(ranks)

    # 透明的湿度加权
    wet = 0.0
    if suitedness == "two-tone":
        wet += 0.30
    elif suitedness == "monotone":
        wet += 0.55
    if straightiness >= 3:
        wet += 0.35
    elif straightiness == 2 and not paired:
        wet += 0.10
    if paired:
        wet -= 0.10  # 成对面通常利于持范围方小注，偏"干"
    wet = max(0.0, min(1.0, wet))

    if wet >= 0.5:
        wet_label = "湿"
    elif wet < 0.28:
        wet_label = "干"
    else:
        wet_label = "中性"

    high_label = "高张面" if high >= RANK_VAL["T"] else "低张面"

    parts = []
    if trips:
        parts.append("三条面")
    elif paired:
        parts.append("成对面")
    parts.append({"monotone": "同花", "two-tone": "两色", "rainbow": "彩虹"}[suitedness])
    if straightiness >= 3:
        parts.append("强连接")

    return {
        "board": cards,
        "paired": paired,
        "trips": trips,
        "suitedness": suitedness,
        "straightiness": straightiness,
        "high_card": high,
        "high_label": high_label,
        "wetness": round(wet, 3),
        "wet_label": wet_label,
        "descriptor": f"{high_label}·" + "·".join(parts) + f"（{wet_label}）",
    }
