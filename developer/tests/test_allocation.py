import pytest
from harness.allocation import choose_candidates, load_pools, allocate

POOLS = {"average": ["P07"], "upper_mid": ["P03", "P01"],
         "lower_mid": ["P04", "P08", "P02"], "high": ["P06", "P10"]}
AVAILABLE = [f"P{i:02d}" for i in range(1, 11)]

def test_confirmed_pools_and_random_order():
    assert load_pools() == POOLS
    assignments = set()
    for seed in range(30):
        ids = choose_candidates(POOLS, AVAILABLE, seed)
        assert ids[0] == "P07" and len(set(ids)) == 4
        assert not set(ids) & {"P05", "P09"}
        for band in ("upper_mid", "lower_mid", "high"):
            assert len(set(ids) & set(POOLS[band])) == 1
        assert ids == choose_candidates(POOLS, AVAILABLE, seed)
        assignments.add(tuple(ids))
    assert len(assignments) > 10

@pytest.mark.parametrize("bad", [
    {**POOLS, "high": ["P01", "P06"]}, {**POOLS, "high": ["P06", "P11"]},
    {**POOLS, "high": []}, {**POOLS, "average": ["P07", "P05"]},
    {**POOLS, "future_scenario": "must not be used"},
])
def test_invalid_pools_are_rejected(bad):
    with pytest.raises(ValueError): choose_candidates(bad, AVAILABLE, 123)

def test_missing_persona_does_not_shrink_candidate_pool(monkeypatch):
    from harness import candidate
    monkeypatch.setattr(candidate, "validate_bundle", lambda: [x for x in AVAILABLE if x != "P03"])
    with pytest.raises(ValueError, match="P03"): allocate(seed=42)
