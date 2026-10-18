"""Simulated community vote distribution (until real votes exist).

Contract (see show_community_distribution): `community_distribution` is four integer percentages [A, B, C, D]
summing to 100, `community_distribution_meta` = {"source": "simulated", "sample_size": N, "generated_at": ISO}.
Deterministic per question id, so reruns give the same numbers; real votes can later replace both fields.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Probability that a WRONG option leads, by difficulty (harder questions fool more people).
WRONG_LEADER = {"Kolay": 0.08, "Orta": 0.18, "Zor": 0.30}
LETTERS = "ABCD"


def _difficulty(data: Mapping[str, Any]) -> str:
    for key in ("difficulty", "difficulty_global", "difficulty_local"):
        if data.get(key) in WRONG_LEADER:
            return data[key]
    return "Orta"


def _split(total: int, weights: List[float], cap: int) -> List[int]:
    """Largest-remainder integer split of `total` by `weights`, every share <= cap."""
    scale = total / sum(weights)
    raw = [w * scale for w in weights]
    parts = [min(cap, int(x)) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    while sum(parts) < total:
        progressed = False
        for i in order:
            if sum(parts) == total:
                break
            if parts[i] < cap:
                parts[i] += 1
                progressed = True
        if not progressed:
            break
    return parts


def simulate_distribution(doc_id: str, data: Mapping[str, Any]) -> Optional[Tuple[List[int], int]]:
    """([A,B,C,D] percentages, sample_size) or None when the question has no valid correct answer."""
    answer = data.get("correct_answer")
    if answer not in tuple(LETTERS):
        return None
    correct = LETTERS.index(answer)
    rng = random.Random(int(hashlib.sha256(str(doc_id).encode("utf-8")).hexdigest()[:16], 16))
    level = _difficulty(data)
    wrong_wins = rng.random() < WRONG_LEADER[level]
    strongest = data.get("strongest_distractor_index")
    wrongs = [i for i in range(4) if i != correct]
    if wrong_wins:
        leader = strongest if strongest in wrongs and rng.random() < 0.7 else rng.choice(wrongs)
        lead_pct = rng.randint(35, 55)
    else:
        leader = correct
        low = {"Kolay": 55, "Orta": 45, "Zor": 38}[level]
        high = {"Kolay": 75, "Orta": 70, "Zor": 60}[level]
        lead_pct = rng.randint(low, high)
    others = [i for i in range(4) if i != leader]
    weights = [rng.uniform(0.4, 1.6) for _ in others]
    if not wrong_wins and strongest in others:  # the strongest distractor attracts the most of the remainder
        weights[others.index(strongest)] += 1.0
    if wrong_wins:  # the correct answer still gets a sizeable share
        weights[others.index(correct)] += 0.8
    shares = _split(100 - lead_pct, weights, lead_pct - 1)
    result = [0, 0, 0, 0]
    result[leader] = lead_pct
    for index, share in zip(others, shares):
        result[index] = share
    return result, rng.randint(300, 1500)


def apply_simulated_distribution(doc_id: str, data: Dict[str, Any], now: Optional[str] = None) -> bool:
    """Fill community_distribution(+meta) on `data` when it has none; True when it was set."""
    if data.get("community_distribution"):
        return False
    simulated = simulate_distribution(doc_id, data)
    if simulated is None:
        return False
    distribution, sample = simulated
    data["community_distribution"] = distribution
    data["community_distribution_meta"] = {
        "source": "simulated", "sample_size": sample,
        "generated_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return True
