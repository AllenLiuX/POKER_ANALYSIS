"""翻前训练器：场景生成 + API 判分测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.poker.preflop.coach_llm import build_coach_prompt
from app.poker.preflop.handclass import hand_class
from app.poker.preflop.scenario import POSITION_ORDER, generate_scenario
from app.poker.preflop.scoring import score_action

client = TestClient(app)


def test_scenario_reproducible_with_seed():
    a = generate_scenario(seed=42)
    b = generate_scenario(seed=42)
    # 除随机 id 外应完全一致
    a.pop("id")
    b.pop("id")
    assert a == b


def test_scenario_shape():
    s = generate_scenario(spot="RFI", position="CO", seed=7)
    assert s["position"] == "CO"
    assert s["spot"] == "RFI"
    assert len(s["hero"]) == 2
    assert s["hero_class"] == hand_class(s["hero"][0], s["hero"][1])
    assert "fold" in s["available_actions"]
    assert "raise" in s["available_actions"]
    assert len(s["seats"]) == 6
    # RFI 下 CO 之前的 UTG/MP 应为 folded
    by_pos = {seat["position"]: seat for seat in s["seats"]}
    assert by_pos["UTG"]["status"] == "folded"
    assert by_pos["MP"]["status"] == "folded"
    assert by_pos["CO"]["status"] == "hero"
    assert by_pos["BTN"]["status"] == "waiting"


def test_scenario_utg_none_folded():
    s = generate_scenario(spot="RFI", position="UTG", seed=1)
    statuses = {seat["position"]: seat["status"] for seat in s["seats"]}
    assert statuses["UTG"] == "hero"
    assert "folded" not in statuses.values()


def test_next_endpoint():
    r = client.get("/api/trainer/next", params={"seed": 123, "position": "BTN"})
    assert r.status_code == 200
    scen = r.json()["scenario"]
    assert scen["position"] == "BTN"
    assert scen["prompt"]


def test_answer_optimal():
    # 强制发到 UTG，然后用 AA raise 应为 optimal
    r = client.post(
        "/api/trainer/answer",
        json={"position": "UTG", "hero": ["As", "Ad"], "action": "raise"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hand_class"] == "AA"
    assert body["score"]["grade"] == "optimal"
    assert body["feedback"]["headline"] == "正解"


def test_answer_mistake():
    r = client.post(
        "/api/trainer/answer",
        json={"position": "UTG", "hero": ["7c", "2d"], "action": "raise"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["score"]["grade"] == "mistake"
    assert body["feedback"]["headline"] == "偏离"
    assert body["feedback"]["explanation"]


def test_answer_fold_trash_is_optimal():
    r = client.post(
        "/api/trainer/answer",
        json={"position": "UTG", "hero": ["7c", "2d"], "action": "fold"},
    )
    body = r.json()
    assert body["score"]["grade"] == "optimal"


def test_next_bad_position_404():
    r = client.get("/api/trainer/next", params={"position": "ZZ"})
    assert r.status_code == 404


def test_position_order_constant():
    assert POSITION_ORDER == ["UTG", "MP", "CO", "BTN", "SB", "BB"]


def test_coach_prompt_grounds_facts():
    # 纯函数：不联网，只验证 prompt 里带上了事实（频率 + 手牌 + 位置）
    score = score_action({"raise": 1.0, "fold": 0.0}, "raise")
    p = build_coach_prompt(
        hero_position="UTG", spot="RFI", hand_class="AA", hero_glyphs="A♠ A♦", score=score
    )
    assert "AA" in p
    assert "UTG" in p
    assert "加注 100%" in p  # 事实频率被写入 prompt
    assert "A♠ A♦" in p


def test_coach_prompt_vs_rfi_mentions_opener():
    score = score_action({"raise": 0.0, "call": 1.0, "fold": 0.0}, "call")
    p = build_coach_prompt(
        hero_position="BB",
        spot="vs_RFI",
        hand_class="T9s",
        hero_glyphs="T♠ 9♠",
        score=score,
        opener_position="BTN",
    )
    assert "BB" in p and "BTN" in p
    assert "跟注 100%" in p


def test_vs_rfi_scenario_has_raiser_seat():
    s = generate_scenario(spot="vs_RFI", position="BB_vs_BTN", seed=3)
    assert s["spot"] == "vs_RFI"
    assert s["hero_position"] == "BB"
    assert s["opener_position"] == "BTN"
    assert s["facing"]["open_size_bb"] == 2.5
    assert s["pot_bb"] == 4.0
    by_pos = {seat["position"]: seat for seat in s["seats"]}
    assert by_pos["BTN"]["status"] == "raiser"
    assert by_pos["BB"]["status"] == "hero"
    assert by_pos["SB"]["status"] == "folded"  # SB 在 hero 之前且非 opener
    assert "call" in s["available_actions"] and "raise" in s["available_actions"]


def test_vs_rfi_answer_call_optimal():
    # T9s BB vs BTN -> call 应为 optimal
    r = client.post(
        "/api/trainer/answer",
        json={
            "spot": "vs_RFI",
            "position": "BB_vs_BTN",
            "hero": ["Ts", "9s"],
            "action": "call",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["hand_class"] == "T9s"
    assert body["hero_position"] == "BB"
    assert body["opener_position"] == "BTN"
    assert body["score"]["grade"] == "optimal"


def test_vs_rfi_trash_fold_optimal():
    r = client.post(
        "/api/trainer/answer",
        json={
            "spot": "vs_RFI",
            "position": "SB_vs_BTN",
            "hero": ["7c", "2d"],
            "action": "fold",
        },
    )
    assert r.json()["score"]["grade"] == "optimal"


def test_trainer_spots_includes_vs_rfi():
    r = client.get("/api/trainer/spots")
    spots = r.json()["spots"]
    kinds = {s["spot"] for s in spots}
    assert "RFI" in kinds and "vs_RFI" in kinds
