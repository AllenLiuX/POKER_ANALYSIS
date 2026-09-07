from wpk_recorder.features import board_texture, derive_decisions
from wpk_recorder.models import Action, HandHistory, Player


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
