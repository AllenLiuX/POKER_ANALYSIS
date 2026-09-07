from wpk_recorder.analytics import connect_readonly, hand_detail
from wpk_recorder.inference import (
    inference_backtest,
    normalize_preflop_position,
    player_profile_evidence,
    preflop_range_profile,
)
from wpk_recorder.models import Action, HandHistory, Player
from wpk_recorder.poker import best_rank, equity_vs_random, hand_features
from wpk_recorder.storage import RecorderStore


def test_hand_evaluator_and_equity_labels():
    assert best_rank(["As", "Ah", "Ad", "Kc", "Kd", "2s", "3h"])[0] == 6
    features = hand_features(["Ah", "Kh"], ["Qh", "2h", "7c"])
    assert features["flush_draw"] is True
    assert equity_vs_random(["As", "Ah"], [], trials=400) > 0.8
    assert equity_vs_random(["7s", "2h"], [], trials=400) < 0.45


def test_showdown_labels_predictions_and_leave_one_hand_backtest(tmp_path):
    store = RecorderStore(tmp_path)
    samples = [
        ("room-1", "u1", ["As", "Ah"], "raise"),
        ("room-2", "u2", ["Ks", "Kh"], "raise"),
        ("room-3", "u3", ["7s", "2h"], "fold"),
    ]
    for number, (hand_id, user_id, cards, action) in enumerate(samples, 1):
        hand = HandHistory(
            hand_id=hand_id,
            started_at=f"2026-01-01T00:0{number}:00+00:00",
            status="completed",
            button_seat=1,
            big_blind=2,
        )
        hand.players = {
            1: Player(
                1,
                user_id=user_id,
                alias=user_id,
                stack_start=200,
                hole_cards=cards,
            )
        }
        hand.actions = [
            Action(
                "preflop",
                1,
                user_id,
                action,
                amount=6 if action == "raise" else 0,
                amount_to=6 if action == "raise" else None,
                user_id=user_id,
                action_id=f"a{number}",
                sequence=1,
            )
        ]
        store.save_hand(hand)
    store.close()

    detail = hand_detail(tmp_path, "room-3")
    assert detail is not None
    assert detail["range_predictions"][0]["training_samples"] == 2
    connection = connect_readonly(tmp_path)
    try:
        backtest = inference_backtest(connection)
        range_profile = preflop_range_profile(
            connection, "u1", position="SB", line="open_raise"
        )
        prior_only = preflop_range_profile(
            connection, "u3", position="SB", line="vpip"
        )
        evidence = player_profile_evidence(connection, "u1")
    finally:
        connection.close()
    assert backtest["labeled_observations"] == 3
    assert backtest["scored_observations"] == 3
    assert backtest["method"] == "leave-one-hand-out"
    assert len(range_profile["matrix"]) == 169
    assert range_profile["evidence"]["player_revealed_samples"] == 1
    weights = {item["hand"]: item["weight_pct"] for item in range_profile["matrix"]}
    assert weights["AA"] > weights["72o"]
    assert range_profile["evidence"]["prior_dependence_pct"] < 100
    assert prior_only["evidence"]["prior_dependence_pct"] == 100
    assert normalize_preflop_position("BTN/SB") == "BTN/SB"
    assert normalize_preflop_position("SMALL_BLIND") == "SB"
    assert evidence["available_cases"] == 1
    assert evidence["cases"][0]["case_id"] == "case_01"
    assert evidence["cases"][0]["shown_hand"] == "AA"
    assert evidence["cases"][0]["preflop_line"] == "open_raise"
    assert evidence["cases"][0]["actions"][0]["action"] == "raise"
    assert range_profile["method"] == "hierarchical-bayesian-shown-hand-v1"
