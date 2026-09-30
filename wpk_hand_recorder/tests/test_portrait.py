from wpk_recorder.portrait import classify_portrait_tags, match_tags_to_spot


def _metric(mean, pool, n, low=None, high=None, successes=None):
    return {
        "mean_pct": mean,
        "population_mean_pct": pool,
        "opportunities": n,
        "successes": successes if successes is not None else round(mean * n / 100),
        "low_pct": low if low is not None else mean - 4,
        "high_pct": high if high is not None else mean + 4,
        "confidence": "low",
    }


def test_classify_portrait_tags_is_multilabel():
    tags = classify_portrait_tags(
        {
            "big_pot_calldown": _metric(72, 52, 20, low=64, high=80),
            "missed_initiative": _metric(61, 38, 24, low=53, high=69),
            "call_then_lead": _metric(41, 22, 18, low=33, high=49),
        }
    )
    ids = {tag["id"] for tag in tags}
    assert "big_pot_calldown" in ids
    assert "missed_initiative" in ids
    assert "call_then_lead" in ids
    assert all(tag["exploit"] for tag in tags)


def test_classify_portrait_tags_requires_sample_and_delta():
    tags = classify_portrait_tags(
        {
            "big_pot_calldown": _metric(54, 52, 4),
            "missed_initiative": _metric(40, 38, 30, low=34, high=46),
        }
    )
    assert tags == []


def test_match_tags_to_spot_keeps_street_relevant_leaks():
    tags = [
        {
            "id": "big_pot_calldown",
            "streets": ["turn", "river"],
            "label": "大底池跟住",
        },
        {
            "id": "call_then_lead",
            "streets": ["flop", "turn", "river"],
            "label": "买牌领打",
        },
    ]
    river = match_tags_to_spot(tags, street="river", limit=2)
    assert [item["id"] for item in river] == ["big_pot_calldown", "call_then_lead"]
    flop = match_tags_to_spot(tags, street="flop", limit=2)
    assert [item["id"] for item in flop] == ["call_then_lead"]


def test_showdown_tags_mark_selection_bias_and_stay_multilabel():
    tags = classify_portrait_tags(
        {
            "slowplay_two_pair_plus": _metric(72, 35, 12, low=60, high=82),
            "shown_air_aggression": _metric(48, 22, 16, low=38, high=58),
            "hit_then_lead": _metric(81, 55, 11, low=70, high=90),
        }
    )
    by_id = {tag["id"]: tag for tag in tags}
    assert "slowplay_two_pair_plus" in by_id
    assert "shown_air_aggression" in by_id
    assert "hit_then_lead" in by_id
    assert by_id["slowplay_two_pair_plus"]["selection_bias"] == "showdown_only"
    assert by_id["shown_air_aggression"]["family"] == "air"


def test_remaining_catalog_tags_fire():
    tags = classify_portrait_tags(
        {
            "call_vs_raise": _metric(62, 42, 20, low=54, high=70),
            "bet_aversion": _metric(64, 48, 30, low=56, high=72),
            "overcall_overbet": _metric(71, 38, 12, low=60, high=82),
            "draw_complete_donk": _metric(80, 55, 9, low=68, high=90),
            "bluff_size_split": _metric(70, 45, 16, low=60, high=80),
            "low_wsd_large_pot": _metric(74, 52, 11, low=64, high=84),
        }
    )
    by_id = {tag["id"]: tag for tag in tags}
    assert "call_vs_raise" in by_id
    assert "bet_aversion" in by_id
    assert "overcall_overbet" in by_id
    assert "draw_complete_donk" in by_id
    assert by_id["draw_complete_donk"]["selection_bias"] == "showdown_only"
    assert "bluff_size_split" in by_id
    assert "low_wsd_large_pot" in by_id


def test_showdown_strength_band_tags_fire():
    tags = classify_portrait_tags(
        {
            "thin_value_medium": _metric(72, 45, 12, low=60, high=84),
            "weak_pays_big": _metric(68, 38, 11, low=56, high=80),
            "medium_calls_big": _metric(74, 50, 12, low=62, high=86),
            "river_weak_call": _metric(66, 42, 12, low=54, high=78),
        }
    )
    by_id = {tag["id"]: tag for tag in tags}
    assert by_id["thin_value_medium"]["selection_bias"] == "showdown_only"
    assert "weak_pays_big" in by_id
    assert "medium_calls_big" in by_id
    assert "river_weak_call" in by_id

    inverse = classify_portrait_tags(
        {
            "thin_value_medium": _metric(22, 45, 12, low=12, high=32),
            "weak_pays_big": _metric(12, 38, 11, low=4, high=22),
        }
    )
    inverse_ids = {tag["id"] for tag in inverse}
    assert "checks_medium" in inverse_ids
    assert "folds_weak_big" in inverse_ids

