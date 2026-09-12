import pytest

import wpk_recorder.equity_curve as equity_curve_module
from wpk_recorder.equity_curve import (
    EquityCurveCancelled,
    build_range_equity_curve,
)
from wpk_recorder.reasoning import hero_preflop_range


def _context():
    return {
        "decision": {
            "street": "flop",
            "hero_cards": ["As", "Kh"],
            "board": ["Kd", "7s", "2c"],
            "acting_seat": 1,
            "hero_position": "BTN",
            "players_in_hand": 2,
            "table_players": 6,
            "big_blind": 2,
        },
        "action_history": [
            {
                "street": "preflop",
                "seat": 1,
                "position": "BTN",
                "action": "raise",
            },
            {
                "street": "preflop",
                "seat": 2,
                "position": "BB",
                "action": "call",
            },
        ],
        "active_opponent_profiles": [
            {
                "player": "seat_2",
                "preflop_range": {
                    "confidence": "high",
                    "weights": {
                        "AA": 100,
                        "QQ": 100,
                        "JJ": 100,
                        "AKs": 100,
                        "AQs": 100,
                    },
                },
            }
        ],
    }


def test_hero_range_conditions_on_observed_preflop_action():
    fitted = hero_preflop_range(_context())

    assert fitted["scenario"] == "unopened"
    assert fitted["action_line"] == "raise"
    assert 0 < fitted["combo_mass"] < 1326
    assert fitted["weights"]["AA"] > fitted["weights"]["72o"]


def test_range_equity_curve_orders_range_and_marks_exact_hand():
    context = _context()
    curve = build_range_equity_curve(context, hero_preflop_range(context))

    assert curve["status"] == "ok"
    assert curve["opponents"] == 1
    assert curve["hero"]["hand"] == "AKo"
    assert 0 <= curve["hero"]["equity_pct"] <= 100
    assert 0 <= curve["range_equity_pct"] <= 100
    assert len(curve["points"]) >= 15
    assert [point["equity_pct"] for point in curve["points"]] == sorted(
        point["equity_pct"] for point in curve["points"]
    )
    assert curve["points"][0]["percentile"] < curve["points"][-1]["percentile"]
    assert set(curve["quantiles"]) == {"p10", "p25", "p50", "p75", "p90"}


def test_range_equity_curve_waits_for_a_flop():
    context = _context()
    context["decision"]["street"] = "preflop"
    context["decision"]["board"] = []

    curve = build_range_equity_curve(context, hero_preflop_range(context))

    assert curve["status"] == "unavailable"


def test_range_equity_curve_stops_when_superseded():
    context = _context()

    with pytest.raises(EquityCurveCancelled):
        build_range_equity_curve(
            context,
            hero_preflop_range(context),
            cancel_check=lambda: True,
        )


def test_observer_curve_excludes_acting_player_from_opponent_ranges(
    monkeypatch,
):
    context = _context()
    context["decision"].update(
        {
            "decision_subject": "observer",
            "subject_seat": 1,
            "hero_cards": [],
        }
    )
    context["active_opponent_profiles"] = [
        {
            "player": "seat_1",
            "preflop_range": {
                "confidence": "high",
                "weights": {"AA": 100},
            },
        },
        {
            "player": "seat_2",
            "preflop_range": {
                "confidence": "high",
                "weights": {"72o": 100},
            },
        },
    ]
    seen_ranges = []

    def fake_equity(
        _hole_cards,
        _board,
        range_models,
        _opponents,
        _trials,
        _uses_ranges,
    ):
        seen_ranges.append(range_models)
        return 0.5

    monkeypatch.setattr(equity_curve_module, "_exact_equity", fake_equity)
    curve = build_range_equity_curve(
        context,
        {
            "weights": {"AKs": 100},
            "action_line": "raise",
        },
    )

    assert curve["decision_subject"] == "observer"
    assert curve["hero"] is None
    assert seen_ranges
    assert all(ranges == [{"72o": 100}] for ranges in seen_ranges)


def test_curve_prefers_validated_action_line_opponent_range(monkeypatch):
    context = _context()
    context["active_opponent_profiles"][0]["range_posterior"] = {
        "enabled": True,
        "confidence": "medium",
        "weights": {"72o": 100},
        "updates": [
            {
                "street": "flop",
                "applied": True,
            }
        ],
    }
    seen_ranges = []

    def fake_equity(
        _hole_cards,
        _board,
        range_models,
        _opponents,
        _trials,
        _uses_ranges,
    ):
        seen_ranges.append(range_models)
        return 0.5

    monkeypatch.setattr(equity_curve_module, "_exact_equity", fake_equity)

    curve = build_range_equity_curve(
        context,
        {
            "weights": {"AKs": 100},
            "action_line": "raise",
        },
    )

    assert seen_ranges
    assert all(ranges == [{"72o": 100}] for ranges in seen_ranges)
    assert curve["curve_version"] == "range-equity-curve-v2"
    assert curve["action_line_conditioned_opponents"] == 1
    assert curve["opponent_ranges"] == [
        {
            "player": "seat_2",
            "source": "action_line_posterior",
            "confidence": "medium",
            "line": None,
            "applied_streets": ["flop"],
        }
    ]
