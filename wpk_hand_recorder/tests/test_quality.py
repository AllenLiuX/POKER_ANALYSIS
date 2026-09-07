from wpk_recorder.models import Action, HandHistory
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
