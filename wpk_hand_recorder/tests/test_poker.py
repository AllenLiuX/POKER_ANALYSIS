import itertools
import random

import wpk_recorder.poker as poker


def test_fast_five_to_seven_card_rank_matches_combination_evaluator():
    deck = [
        f"{rank}{suit}"
        for rank in poker.RANKS
        for suit in poker.SUITS
    ]
    rng = random.Random(20260909)
    fixtures = [
        ["As", "Ks", "Qs", "Js", "Ts", "2h", "3c"],
        ["Ac", "Ad", "Ah", "As", "Kd", "Kh", "2c"],
        ["Ac", "Ad", "Ah", "Ks", "Kd", "Kh", "2c"],
        ["As", "Qs", "9s", "6s", "3s", "2s", "Kh"],
        ["As", "2d", "3c", "4h", "5s", "Kd", "Qh"],
    ]
    fixtures.extend(
        rng.sample(deck, size)
        for size in (5, 6, 7)
        for _ in range(750)
    )

    for cards in fixtures:
        expected = max(
            poker._five_card_rank(combo)
            for combo in itertools.combinations(cards, 5)
        )
        assert poker.best_rank(cards) == expected
