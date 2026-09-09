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
