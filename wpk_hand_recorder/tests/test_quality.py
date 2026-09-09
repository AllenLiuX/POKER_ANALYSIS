from wpk_recorder.models import Action, HandHistory, Player
from wpk_recorder.quality import assess_hand


def test_completed_duplicate_actions_are_bad_and_excluded():
    hand = HandHistory(hand_id="bad", status="completed")
    hand.actions = [
        Action("preflop", 1, "A", "raise", action_id="same", sequence=1),
        Action("preflop", 1, "A", "raise", action_id="same", sequence=2),
    ]
    quality, reasons, excluded = assess_hand(hand)
    assert quality == "bad"
    assert excluded is True
    assert "检测到重复 action ID" in reasons


def test_interrupted_and_live_hands_are_not_used_for_stats():
    interrupted = HandHistory(hand_id="partial", status="interrupted")
    assert assess_hand(interrupted)[0] == "partial"
    live = HandHistory(hand_id="live", status="in_progress")
    assert assess_hand(live) == ("live", [], True)
    assert assess_hand(live, stale_in_progress=True)[0] == "partial"


def test_insurance_result_and_fund_balance_player_net():
    hand = HandHistory(hand_id="insured", status="completed")
    hand.actions = [Action("preflop", 1, "Buyer", "call", sequence=1)]
    hand.players = {
        1: Player(1, net=395, insurance_result=-10, fund=11),
        2: Player(2, net=-416),
    }

    assert assess_hand(hand) == ("good", [], False)


def test_player_net_difference_within_rake_tolerance_is_good():
    hand = HandHistory(hand_id="raked", status="completed")
    hand.actions = [Action("preflop", 1, "Winner", "call", sequence=1)]
    hand.players = {
        1: Player(1, net=100),
        2: Player(2, net=-90),
    }

    assert assess_hand(hand) == ("good", [], False)


def test_player_net_difference_over_rake_tolerance_is_bad():
    hand = HandHistory(hand_id="unbalanced", status="completed")
    hand.actions = [Action("preflop", 1, "Winner", "call", sequence=1)]
    hand.players = {
        1: Player(1, net=100),
        2: Player(2, net=-89),
    }

    quality, reasons, excluded = assess_hand(hand)
    assert quality == "bad"
    assert reasons == ["玩家净输赢不平衡：11.00"]
    assert excluded is True
