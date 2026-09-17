import json

import pytest

from harness.allocation import allocate, choose_candidates, load_pools
from harness.__main__ import main
from harness.finalize import ranking_context
from conftest import Clock, runner_for

POOLS = {"average": ["P07"], "upper_mid": ["P03", "P01"],
         "lower_mid": ["P04", "P08", "P02"], "high": ["P06", "P10"]}
AVAILABLE = [f"P{i:02d}" for i in range(1, 11)]


def test_saved_pools_contain_only_the_confirmed_candidate_ids():
    assert load_pools() == POOLS


def test_random_selection_has_average_first_and_one_from_each_other_band():
    assignments = set()
    for seed in range(30):
        ids = choose_candidates(load_pools(), AVAILABLE, seed)
        assert ids[0] == "P07"
        assert len(set(ids)) == 4
        for band in ("upper_mid", "lower_mid", "high"):
            assert len(set(ids) & set(POOLS[band])) == 1
        assert not set(ids) & {"P05", "P09"}
        assert ids == choose_candidates(load_pools(), AVAILABLE, seed)
        assignments.add(tuple(ids))
    assert len(assignments) > 10
    assert len({ids[1] for ids in assignments}) > 3


@pytest.mark.parametrize("bad", [
    {**POOLS, "high": ["P01", "P06"]},
    {**POOLS, "high": ["P06", "P11"]},
    {**POOLS, "high": []},
    {**POOLS, "average": ["P07", "P05"]},
    {**POOLS, "future_scenario": "must not be stored in this config"},
])
def test_empty_duplicate_unknown_candidates_or_extra_fields_are_rejected(bad):
    with pytest.raises(ValueError):
        choose_candidates(bad, AVAILABLE, 123)


def test_first_init_allocates_once_and_second_init_preserves_assignment(tmp_path, monkeypatch):
    from harness import __main__ as cli
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"own_author": "me", "pairing_verified": True,
                                  "shared_peer": {"chat": "operator-room", "author": "candidate"}}))
    def model_must_not_be_used(*args, **kwargs):
        pytest.fail("후보 배정 중 Claude를 호출했습니다")

    monkeypatch.setattr(cli, "Claude", model_must_not_be_used)
    monkeypatch.setattr(cli, "ensure_claude_login", lambda: "claude")
    directory = tmp_path / "session"
    args = ["--session", str(directory), "init", "--config", str(config), "--model", "test"]
    assert main(args) == 0
    before = (directory / "state.json").read_bytes()

    def must_not_redraw(*args, **kwargs):
        pytest.fail("같은 세션을 재시작하며 다시 추첨했습니다")

    monkeypatch.setattr(cli, "allocate", must_not_redraw)
    assert main(args) == 0
    assert (directory / "state.json").read_bytes() == before
    state = json.loads(before)
    assert state["interviews"][0]["candidate_id"] == "P07"
    assert [iv["mode"] for iv in state["interviews"]] == ["human", "agent", "agent", "agent"]
    assert state["assignment"]["basis"] == "user_confirmed_candidate_pools"
    assert set(state["assignment"]) == {"policy", "seed", "candidate_ids", "basis", "pools_sha256"}
    assert all("band" not in iv for iv in state["interviews"])


def test_shared_room_draws_from_confirmed_pools(session):
    path = session.directory / "config.json"
    config = json.loads(path.read_text())
    config["peers"] = {}
    config["shared_peer"] = {"chat": "operator-room", "author": "candidate"}
    path.write_text(json.dumps(config))

    ids, metadata = allocate(path, seed=42)
    assert ids == choose_candidates(POOLS, AVAILABLE, 42)
    assert "average" not in metadata


def test_missing_room_does_not_silently_shrink_the_confirmed_pools(session):
    path = session.directory / "config.json"
    config = json.loads(path.read_text())
    del config["peers"]["P03"]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="P03"):
        allocate(path, seed=42)


def test_excluded_candidates_do_not_need_rooms(session):
    path = session.directory / "config.json"
    config = json.loads(path.read_text())
    for cid in ("P05", "P09"):
        del config["peers"][cid]
    path.write_text(json.dumps(config))
    assert allocate(path, seed=42)[0] == choose_candidates(POOLS, AVAILABLE, 42)


def test_interviewer_does_not_receive_allocation_metadata(session):
    session.state["assignment"] = {"pools": POOLS, "marker": "PRIVATE_ALLOCATION_SENTINEL"}
    runner, _ = runner_for(session, Clock())
    runner.step()
    context = json.dumps(runner.context(), ensure_ascii=False)
    assert "PRIVATE_ALLOCATION_SENTINEL" not in context
    assert "upper_mid" not in context
    assert "lower_mid" not in context
    runner.shutdown()


def test_ranker_does_not_receive_allocation_metadata(completed):
    completed.state["assignment"] = {"pools": POOLS, "marker": "PRIVATE_ALLOCATION_SENTINEL"}
    context = json.dumps(ranking_context(completed)[0], ensure_ascii=False)
    assert "PRIVATE_ALLOCATION_SENTINEL" not in context
    assert "upper_mid" not in context
    assert "lower_mid" not in context
