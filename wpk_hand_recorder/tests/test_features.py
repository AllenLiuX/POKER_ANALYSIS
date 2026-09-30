from wpk_recorder.features import board_texture, derive_decisions, stack_bucket
from wpk_recorder.inference import _stack_bucket as inference_stack_bucket
from wpk_recorder.models import Action, HandHistory, Player
from wpk_recorder.opponent_model import _stack_bucket as opponent_stack_bucket


def test_positions_are_recomputed_after_full_hand_roster_arrives():
    hand = HandHistory(hand_id="incremental-roster", button_seat=3)
    hand.players = {
        1: Player(1, position="Seat 1"),
        2: Player(2, position="Seat 2"),
        3: Player(3, position="Seat 3"),
        4: Player(4, position="Seat 4"),
    }

    derive_decisions(hand)

    assert {
        seat: player.position
        for seat, player in hand.players.items()
    } == {
        1: "BB",
        2: "UTG",
        3: "BTN",
        4: "SB",
    }


def test_decision_snapshots_capture_positions_opportunities_and_sizing():
    hand = HandHistory(
        hand_id="table-1",
        button_seat=3,
        big_blind=2,
        board=["As", "Kh", "2h"],
    )
    hand.players = {
        1: Player(1, user_id="sb", alias="SB", stack_start=100),
        2: Player(2, user_id="bb", alias="BB", stack_start=100),
        3: Player(3, user_id="btn", alias="BTN", stack_start=100),
    }
    hand.actions = [
        Action("preflop", 1, "SB", "small_blind", amount=1, sequence=1, user_id="sb"),
        Action("preflop", 2, "BB", "big_blind", amount=2, sequence=2, user_id="bb"),
        Action(
            "preflop", 3, "BTN", "raise", amount=5, amount_to=5, sequence=3, user_id="btn"
        ),
        Action(
            "preflop", 2, "BB", "call", amount=3, amount_to=5, sequence=4, user_id="bb"
        ),
        Action("flop", 2, "BB", "check", sequence=5, user_id="bb"),
        Action("flop", 3, "BTN", "bet", amount=5, amount_to=5, sequence=6, user_id="btn"),
        Action("flop", 2, "BB", "fold", sequence=7, user_id="bb"),
    ]

    decisions = derive_decisions(hand)
    by_sequence = {item.action_sequence: item for item in decisions}
    assert by_sequence[3].position == "BTN"
    assert [(item.metric, item.success) for item in by_sequence[3].opportunities] == [
        ("vpip", True),
        ("pfr", True),
        ("rfi", True),
    ]
    assert ("three_bet", False) in [
        (item.metric, item.success) for item in by_sequence[4].opportunities
    ]
    assert ("flop_cbet", True) in [
        (item.metric, item.success) for item in by_sequence[6].opportunities
    ]
    assert round(by_sequence[6].bet_fraction or 0, 4) == 0.4545
    assert ("fold_to_flop_cbet", True) in [
        (item.metric, item.success) for item in by_sequence[7].opportunities
    ]


def test_board_texture_labels_common_structures():
    texture = board_texture(["As", "Ah", "Kh", "Qh"], "turn")
    assert texture["paired"] is True
    assert texture["monotone"] is True
    assert texture["connected"] is True
    assert texture["broadway_count"] == 4


def test_probe_uses_previous_street_check_through():
    hand = HandHistory(
        hand_id="probe",
        button_seat=2,
        big_blind=2,
        board=["As", "7h", "2c", "Kd"],
    )
    hand.players = {
        1: Player(1, user_id="bb", stack_start=200),
        2: Player(2, user_id="btn", stack_start=200),
    }
    hand.actions = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
        Action("turn", 1, "BB", "bet", amount=8, sequence=5, user_id="bb"),
    ]

    turn = derive_decisions(hand)[-1]
    metrics = {(item.metric, item.success) for item in turn.opportunities}

    assert ("turn_probe", True) in metrics
    assert not any(item.metric == "turn_donk" for item in turn.opportunities)


def test_donk_delayed_cbet_and_barrel_are_separate_metrics():
    donk_hand = HandHistory(
        hand_id="donk",
        button_seat=2,
        big_blind=2,
        board=["As", "7h", "2c"],
    )
    donk_hand.players = {
        1: Player(1, user_id="bb", stack_start=200),
        2: Player(2, user_id="btn", stack_start=200),
    }
    donk_hand.actions = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "bet", amount=8, sequence=3, user_id="bb"),
    ]
    donk = derive_decisions(donk_hand)[-1]
    assert ("flop_donk", True) in {
        (item.metric, item.success) for item in donk.opportunities
    }

    delayed_hand = HandHistory(
        hand_id="delayed",
        button_seat=2,
        big_blind=2,
        board=["As", "7h", "2c", "Kd", "9s"],
    )
    delayed_hand.players = donk_hand.players
    delayed_hand.actions = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
        Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
        Action("turn", 2, "BTN", "bet", amount=8, sequence=6, user_id="btn"),
        Action("turn", 1, "BB", "call", amount=8, sequence=7, user_id="bb"),
        Action("river", 1, "BB", "check", sequence=8, user_id="bb"),
        Action("river", 2, "BTN", "bet", amount=20, sequence=9, user_id="btn"),
    ]
    decisions = {item.action_sequence: item for item in derive_decisions(delayed_hand)}
    delayed_metrics = {
        (item.metric, item.success) for item in decisions[6].opportunities
    }
    river_metrics = {
        (item.metric, item.success) for item in decisions[9].opportunities
    }

    assert ("turn_delayed_cbet", True) in delayed_metrics
    assert ("river_barrel", True) in river_metrics
    assert not any(
        item.metric == "river_triple_barrel"
        for item in decisions[9].opportunities
    )


def test_facing_raise_name_and_response_split_use_full_action_line():
    hand = HandHistory(
        hand_id="raise-response",
        button_seat=2,
        big_blind=2,
        board=["As", "7h", "2c"],
    )
    hand.players = {
        1: Player(1, user_id="bb", stack_start=200),
        2: Player(2, user_id="btn", stack_start=200),
    }
    hand.actions = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
        Action("flop", 1, "BB", "raise", amount=24, sequence=5, user_id="bb"),
        Action("flop", 2, "BTN", "fold", sequence=6, user_id="btn"),
    ]

    decisions = {item.action_sequence: item for item in derive_decisions(hand)}
    raise_metrics = {
        (item.metric, item.success) for item in decisions[5].opportunities
    }
    fold_metrics = {
        (item.metric, item.success) for item in decisions[6].opportunities
    }

    assert ("raise_vs_flop_bet", True) in raise_metrics
    assert ("fold_to_flop_raise", True) in fold_metrics
    assert ("call_vs_flop_bet", False) in fold_metrics


def test_stack_bucket_is_shared_across_model_features():
    expected = {
        19.9: "short",
        20: "medium",
        60: "deep",
        150: "very_deep",
    }
    for value, label in expected.items():
        assert stack_bucket(value) == label
        assert inference_stack_bucket(value) == label
        assert opponent_stack_bucket(value) == label


def _hu_hand(hand_id, board, actions, big_blind=2, stacks=400, holes=None):
    hand = HandHistory(
        hand_id=hand_id,
        button_seat=2,
        big_blind=big_blind,
        board=board,
    )
    hand.players = {
        1: Player(1, user_id="bb", alias="BB", stack_start=stacks),
        2: Player(2, user_id="btn", alias="BTN", stack_start=stacks),
    }
    if holes:
        for seat, cards in holes.items():
            hand.players[seat].hole_cards = list(cards)
    hand.actions = actions
    return hand


def test_portrait_metrics_detect_calldown_lead_and_missed_initiative():
    calldown = _hu_hand(
        "calldown",
        ["As", "7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "check", sequence=6, user_id="bb"),
            Action("turn", 2, "BTN", "bet", amount=40, sequence=7, user_id="btn"),
            Action("turn", 1, "BB", "call", amount=40, sequence=8, user_id="bb"),
        ],
        stacks=80,
    )
    turn_call = {
        item.metric: item.success
        for item in derive_decisions(calldown)
        if item.action_sequence == 8
        for item in item.opportunities
    }
    assert turn_call["big_pot_calldown"] is True
    assert turn_call["call_vs_turn_bet"] is True

    lead = _hu_hand(
        "lead",
        ["As", "7h", "2c", "Kd"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "bet", amount=16, sequence=6, user_id="bb"),
        ],
    )
    lead_metrics = {
        item.metric: item.success
        for item in derive_decisions(lead)[-1].opportunities
    }
    assert lead_metrics["call_then_lead"] is True
    assert lead_metrics["turn_donk"] is True

    missed = _hu_hand(
        "missed",
        ["As", "7h", "2c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
        ],
    )
    missed_metrics = {
        item.metric: item.success
        for item in derive_decisions(missed)[-1].opportunities
    }
    assert missed_metrics["missed_initiative"] is True
    assert missed_metrics["flop_cbet"] is False


def test_ip_river_checkback_and_small_bet_overfold():
    checkback = _hu_hand(
        "checkback",
        ["As", "7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "check", sequence=8, user_id="btn"),
        ],
    )
    river = {
        item.metric: item.success
        for item in derive_decisions(checkback)
        if item.action_sequence == 8
        for item in item.opportunities
    }
    assert river["ip_river_checkback"] is True
    assert river["missed_initiative"] is True

    overfold = _hu_hand(
        "overfold",
        ["As", "7h", "2c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=2, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "fold", sequence=5, user_id="bb"),
        ],
    )
    fold_metrics = {
        item.metric: item.success
        for item in derive_decisions(overfold)[-1].opportunities
    }
    assert fold_metrics["overfold_small_bet"] is True
    assert fold_metrics["fold_to_flop_cbet"] is True


def _metrics_at(hand, sequence):
    return {
        item.metric: item.success
        for snapshot in derive_decisions(hand)
        if snapshot.action_sequence == sequence
        for item in snapshot.opportunities
    }


def test_showdown_splits_trap_from_scared_pair_check():
    trap = _hu_hand(
        "trap",
        ["7h", "2c", "2d"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        ],
        holes={1: ["7s", "7d"]},
    )
    trap_metrics = _metrics_at(trap, 3)
    assert trap_metrics["slowplay_two_pair_plus"] is True
    assert "checked_strong_pair" not in trap_metrics

    scared = _hu_hand(
        "scared-pair",
        ["As", "8c", "3d"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        ],
        holes={1: ["Ah", "Kd"]},
    )
    scared_metrics = _metrics_at(scared, 3)
    assert scared_metrics["checked_strong_pair"] is True
    assert "slowplay_two_pair_plus" not in scared_metrics


def test_showdown_tags_require_hole_to_beat_the_board():
    board_made = _hu_hand(
        "board-two-pair",
        ["7h", "7c", "2d", "2s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
        ],
        holes={1: ["9c", "8d"]},
    )
    board_metrics = _metrics_at(board_made, 5)
    assert "slowplay_two_pair_plus" not in board_metrics
    assert "checked_strong_pair" not in board_metrics
    assert "thin_value_medium" not in board_metrics

    second_pair = _hu_hand(
        "second-pair-check",
        ["As", "8c", "3d"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        ],
        holes={1: ["8h", "7d"]},
    )
    second_metrics = _metrics_at(second_pair, 3)
    assert "checked_strong_pair" not in second_metrics
    assert "slowplay_two_pair_plus" not in second_metrics

    playing_pair = _hu_hand(
        "board-pair",
        ["Kh", "Kc", "Qd"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        ],
        holes={1: ["8d", "3s"]},
    )
    pair_metrics = _metrics_at(playing_pair, 3)
    assert "checked_strong_pair" not in pair_metrics
    assert "slowplay_two_pair_plus" not in pair_metrics

    streets = [
        Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
        Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
        Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
        Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
        Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
        Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
    ]
    board_hearts = ["5h", "7h", "9h", "Jh", "3h"]
    nut_flush = _metrics_at(
        _hu_hand("nut-flush", board_hearts, streets, holes={1: ["Ah", "2c"]}),
        7,
    )
    assert nut_flush["slowplay_two_pair_plus"] is True
    board_flush = _metrics_at(
        _hu_hand("board-flush", board_hearts, streets, holes={1: ["2c", "4d"]}),
        7,
    )
    assert "slowplay_two_pair_plus" not in board_flush
    assert board_flush.get("river_air_bluff") is not True
    assert "checked_strong_pair" not in board_flush


def test_showdown_delayed_value_and_check_raise_nuts():
    delayed = _hu_hand(
        "delayed",
        ["7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "bet", amount=10, sequence=5, user_id="bb"),
        ],
        holes={1: ["7s", "7d"]},
    )
    delayed_metrics = _metrics_at(delayed, 5)
    assert delayed_metrics["delayed_value"] is True
    assert delayed_metrics["slowplay_two_pair_plus"] is False

    cr = _hu_hand(
        "cr-nuts",
        ["7h", "2c", "2d"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "raise", amount=24, sequence=5, user_id="bb"),
        ],
        holes={1: ["7s", "7d"]},
    )
    cr_metrics = _metrics_at(cr, 5)
    assert cr_metrics["check_raise_nuts"] is True
    assert cr_metrics["slowplay_two_pair_plus"] is False


def test_showdown_hit_then_lead_miss_give_up_and_air_bluff():
    lead = _hu_hand(
        "hit-lead",
        ["Ah", "7h", "2c", "3h"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "bet", amount=16, sequence=6, user_id="bb"),
        ],
        holes={1: ["6h", "5h"]},
    )
    lead_metrics = _metrics_at(lead, 6)
    assert lead_metrics["call_then_lead"] is True
    assert lead_metrics["hit_then_lead"] is True
    assert lead_metrics["shown_lead_was_hit"] is True

    missed = _hu_hand(
        "miss-give-up",
        ["Ah", "7h", "2c", "Kd", "Qc"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "check", sequence=6, user_id="bb"),
            Action("turn", 2, "BTN", "bet", amount=16, sequence=7, user_id="btn"),
            Action("turn", 1, "BB", "call", amount=16, sequence=8, user_id="bb"),
            Action("river", 1, "BB", "check", sequence=9, user_id="bb"),
        ],
        holes={1: ["6h", "5h"]},
    )
    miss_metrics = _metrics_at(missed, 9)
    assert miss_metrics["miss_then_give_up"] is True
    assert miss_metrics["call_then_lead"] is False

    air = _hu_hand(
        "river-air",
        ["As", "7h", "2c", "Kd", "Qc"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "bet", amount=12, sequence=8, user_id="btn"),
        ],
        holes={2: ["9c", "8d"]},
    )
    air_metrics = _metrics_at(air, 8)
    assert air_metrics["shown_air_aggression"] is True
    assert air_metrics["river_air_bluff"] is True
    assert air_metrics.get("slowplay_two_pair_plus") is not True


def test_remaining_catalog_action_and_showdown_metrics():
    aversion = _hu_hand(
        "aversion",
        ["As", "7h", "2c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
        ],
    )
    assert _metrics_at(aversion, 3)["bet_aversion"] is True

    raise_call = _hu_hand(
        "call-raise",
        ["As", "7h", "2c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "raise", amount=24, sequence=5, user_id="bb"),
            Action("flop", 2, "BTN", "call", amount=16, sequence=6, user_id="btn"),
        ],
    )
    call_metrics = _metrics_at(raise_call, 6)
    assert call_metrics["call_vs_raise"] is True
    assert call_metrics["fold_to_postflop_raise"] is False

    overbet = _hu_hand(
        "overbet",
        ["As", "7h", "2c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=20, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=20, sequence=5, user_id="bb"),
        ],
    )
    assert _metrics_at(overbet, 5)["overcall_overbet"] is True

    donk = _hu_hand(
        "draw-donk",
        ["Ah", "7h", "2c", "3h"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "bet", amount=10, sequence=5, user_id="bb"),
        ],
        holes={1: ["6h", "5h"]},
    )
    donk_metrics = _metrics_at(donk, 5)
    assert donk_metrics["draw_complete_donk"] is True
    assert donk_metrics.get("call_then_lead") is not True

    small_air = _hu_hand(
        "small-air",
        ["As", "7h", "2c", "Kd", "Qc"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "bet", amount=4, sequence=8, user_id="btn"),
        ],
        holes={2: ["9c", "8d"]},
    )
    assert _metrics_at(small_air, 8)["bluff_size_split"] is True

    lost = _hu_hand(
        "lost-sd",
        ["As", "7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "check", sequence=6, user_id="bb"),
            Action("turn", 2, "BTN", "bet", amount=40, sequence=7, user_id="btn"),
            Action("turn", 1, "BB", "call", amount=40, sequence=8, user_id="bb"),
        ],
        stacks=80,
        holes={1: ["Ah", "Kd"]},
    )
    lost.players[1].net = -50
    assert any(
        item.metric == "low_wsd_large_pot" and item.success
        for snapshot in derive_decisions(lost)
        if snapshot.seat == 1
        for item in snapshot.opportunities
    )


def test_showdown_strength_band_thin_value_and_weak_pays():
    thin = _hu_hand(
        "thin-value",
        ["As", "8c", "3d", "2h", "4c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "bet", amount=8, sequence=8, user_id="btn"),
        ],
        holes={2: ["Ah", "Kd"]},
    )
    assert _metrics_at(thin, 8)["thin_value_medium"] is True
    assert "thin_value_medium" not in _metrics_at(thin, 4)

    checks = _hu_hand(
        "checks-medium",
        ["As", "8c", "3d", "2h", "4c"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "check", sequence=8, user_id="btn"),
        ],
        holes={2: ["Ah", "Kd"]},
    )
    assert _metrics_at(checks, 8)["thin_value_medium"] is False

    weak = _hu_hand(
        "weak-pays",
        ["As", "7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "check", sequence=6, user_id="bb"),
            Action("turn", 2, "BTN", "bet", amount=40, sequence=7, user_id="btn"),
            Action("turn", 1, "BB", "call", amount=40, sequence=8, user_id="bb"),
        ],
        stacks=80,
        holes={1: ["4c", "4d"]},
    )
    weak_metrics = _metrics_at(weak, 8)
    assert weak_metrics["weak_pays_big"] is True
    assert "medium_calls_big" not in weak_metrics

    medium = _hu_hand(
        "medium-calls",
        ["As", "7h", "2c", "Kd", "9s"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "bet", amount=8, sequence=4, user_id="btn"),
            Action("flop", 1, "BB", "call", amount=8, sequence=5, user_id="bb"),
            Action("turn", 1, "BB", "check", sequence=6, user_id="bb"),
            Action("turn", 2, "BTN", "bet", amount=40, sequence=7, user_id="btn"),
            Action("turn", 1, "BB", "call", amount=40, sequence=8, user_id="bb"),
        ],
        stacks=80,
        holes={1: ["Ah", "Qd"]},
    )
    medium_metrics = _metrics_at(medium, 8)
    assert medium_metrics["medium_calls_big"] is True
    assert "weak_pays_big" not in medium_metrics

    river_weak = _hu_hand(
        "river-weak-call",
        ["As", "7h", "2c", "Kd", "Qc"],
        [
            Action("preflop", 2, "BTN", "raise", amount=6, sequence=1, user_id="btn"),
            Action("preflop", 1, "BB", "call", amount=6, sequence=2, user_id="bb"),
            Action("flop", 1, "BB", "check", sequence=3, user_id="bb"),
            Action("flop", 2, "BTN", "check", sequence=4, user_id="btn"),
            Action("turn", 1, "BB", "check", sequence=5, user_id="bb"),
            Action("turn", 2, "BTN", "check", sequence=6, user_id="btn"),
            Action("river", 1, "BB", "check", sequence=7, user_id="bb"),
            Action("river", 2, "BTN", "bet", amount=3, sequence=8, user_id="btn"),
            Action("river", 1, "BB", "call", amount=3, sequence=9, user_id="bb"),
        ],
        holes={1: ["9c", "8d"]},
    )
    river_metrics = _metrics_at(river_weak, 9)
    assert river_metrics["river_weak_call"] is True
    assert "weak_pays_big" not in river_metrics

