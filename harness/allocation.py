"""Sample the confirmed candidate pools once, without model inference."""
from pathlib import Path
import random
import secrets

from .models import CandidatePools
from .storage import canonical, resource, sha

POLICY = "confirmed-candidate-pools/random-order-1"


def load_pools():
    return CandidatePools.model_validate_json(resource("harness", "candidate_pools.json")).model_dump()


def choose_candidates(pools, available, seed):
    pools = CandidatePools.model_validate(pools).model_dump()
    flattened = [cid for ids in pools.values() for cid in ids]
    if len(flattened) != len(set(flattened)):
        raise ValueError("후보군 사이에 중복 후보가 있습니다")
    missing = sorted(set(flattened) - set(available))
    if missing:
        raise ValueError("확정 후보군의 자료가 필요합니다: " + ", ".join(missing))
    rng = random.Random(seed)
    remaining = [rng.choice(pools[name]) for name in ("upper_mid", "lower_mid", "high")]
    rng.shuffle(remaining)
    return [pools["average"][0], *remaining]


def allocate(seed=None):
    from .candidate import validate_bundle
    available = validate_bundle()
    pools = load_pools()
    seed = secrets.randbits(53) if seed is None else seed
    ids = choose_candidates(pools, available, seed)
    return ids, {"policy": POLICY, "seed": seed, "candidate_ids": ids,
                 "basis": "user_confirmed_candidate_pools", "pools_sha256": sha(canonical(pools))}
