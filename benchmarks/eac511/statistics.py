"""Deterministic, dependency-free statistics for paired EAC observations."""
from __future__ import annotations
import hashlib, math
from typing import Any, Mapping, Sequence

from .equivalence import compare_paired_pre_gate, pre_gate_snapshot_digest
from .model import MatrixCell, Scenario

PREREGISTERED_SEED = 51120260814

def _finite(x: float) -> float:
    x = float(x)
    if not math.isfinite(x): raise ValueError("values must be finite")
    return x

def wilson_interval(successes: int, trials: int, confidence: float = .95) -> dict[str, Any]:
    if trials < 0 or successes < 0 or successes > trials or confidence != .95: raise ValueError("invalid binomial input")
    if not trials: return {"estimate": None, "lower": None, "upper": None, "n": 0, "label": "uncertain"}
    z = 1.959963984540054
    p, z2, d = successes / trials, z*z, 1 + z*z/trials
    centre = (p + z2/(2*trials))/d
    half = z*math.sqrt(p*(1-p)/trials + z2/(4*trials*trials))/d
    return {"estimate": p, "lower": centre-half, "upper": centre+half, "n": trials, "label": "exploratory" if trials < 30 else "confirmatory"}

def validate_paired_keys(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    def index(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, Any], Mapping[str, Any]]:
        out = {}
        for row in rows:
            key = (row.get("scenario_id"), row.get("seed"))
            if None in key or key in out: raise ValueError("paired keys must be unique and complete")
            out[key] = row
        return out
    a, b = index(left), index(right)
    if set(a) != set(b): raise ValueError("paired keys differ")
    return [(a[k], b[k]) for k in sorted(a, key=repr)]

def exact_mcnemar(b: int, c: int) -> dict[str, Any]:
    if type(b) is not int or type(c) is not int or b < 0 or c < 0: raise ValueError("invalid McNemar counts")
    n = b + c
    tail = sum(math.comb(n, k) for k in range(min(b, c)+1)) / (2**n) if n else 1.0
    return {"b": b, "c": c, "discordant": n, "p_value": min(1.0, 2*tail), "label": "exploratory" if n < 25 else "diagnostic"}

def paired_binary_risk_difference(left: Sequence[bool], right: Sequence[bool]) -> dict[str, Any]:
    if len(left) != len(right) or any(type(x) is not bool for x in (*left, *right)): raise ValueError("paired binary values invalid")
    b = sum(not a and d for a, d in zip(left, right)); c = sum(a and not d for a, d in zip(left, right)); n = len(left)
    return {"risk_difference": (sum(right)-sum(left))/n if n else None, "n": n, "mcnemar": exact_mcnemar(b, c)}

def _counter(seed: int, draw: int, n: int) -> int:
    digest = hashlib.sha256(f"eac511:{seed}:{draw}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % n

def paired_bootstrap_ci(left: Sequence[float], right: Sequence[float], statistic: str = "mean", resamples: int = 10000, seed: int = PREREGISTERED_SEED, confidence: float = .95) -> dict[str, Any]:
    if len(left) != len(right) or not left or resamples <= 0 or not 0 < confidence < 1: raise ValueError("invalid paired bootstrap configuration")
    diffs = [_finite(b)-_finite(a) for a, b in zip(left, right)]
    if statistic == "mean": stat = lambda x: sum(x)/len(x)
    elif statistic == "median":
        stat = lambda x: (sorted(x)[(len(x) - 1) // 2] + sorted(x)[len(x) // 2]) / 2
    else: raise ValueError("statistic must be mean or median")
    draws = [stat([diffs[_counter(seed, draw*len(diffs) + i, len(diffs))] for i in range(len(diffs))]) for draw in range(resamples)]
    draws.sort(); alpha = (1-confidence)/2
    lower_index = max(0, math.ceil(alpha * resamples) - 1)
    upper_index = min(resamples - 1, math.ceil((1 - alpha) * resamples) - 1)
    return {"estimate": stat(diffs), "lower": draws[lower_index], "upper": draws[upper_index], "resamples": resamples, "seed": seed}

def bootstrap_ci(values: Sequence[float], statistic: str = "mean", resamples: int = 10000, seed: int = PREREGISTERED_SEED, confidence: float = .95) -> dict[str, Any]:
    """SHA256-counter bootstrap for a single deterministic sample."""
    vals = [_finite(v) for v in values]
    if not vals:
        return {"estimate": None, "lower": None, "upper": None, "resamples": resamples, "seed": seed, "label": "uncertain"}
    return paired_bootstrap_ci([0.] * len(vals), vals, statistic, resamples, seed, confidence)

def validate_numeric_pairs(left: Sequence[float], right: Sequence[float], nonnegative: bool = False) -> list[tuple[float, float]]:
    if len(left) != len(right): raise ValueError("paired lengths differ")
    pairs = [(_finite(a), _finite(b)) for a, b in zip(left, right)]
    if nonnegative and any(a < 0 or b < 0 for a, b in pairs): raise ValueError("latencies must be nonnegative")
    return pairs

def paired_count_difference(left: Sequence[float], right: Sequence[float]) -> dict[str, Any]:
    pairs = validate_numeric_pairs(left, right); diffs = sorted(b-a for a, b in pairs)
    median = (diffs[(len(diffs)-1)//2] + diffs[len(diffs)//2])/2 if diffs else None
    return {"n": len(diffs), "median_difference": median, "label": "exploratory" if len(diffs) < 30 else "confirmatory"}

def paired_latency_median_difference(left: Sequence[float], right: Sequence[float]) -> dict[str, Any]:
    pairs = validate_numeric_pairs(left, right, nonnegative=True)
    return paired_count_difference([a for a, _ in pairs], [b for _, b in pairs])

def compare_conditions(
    observations: Mapping[str, Sequence[Mapping[str, Any]]], metric: str,
    resamples: int = 10000, seed: int = PREREGISTERED_SEED,
    *, paired_pre_gate: Sequence[tuple[MatrixCell, MatrixCell, Scenario,
                                       Mapping[str, Any], Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    if paired_pre_gate is None:
        raise ValueError("paired analysis requires canonical pre-gate snapshots")
    expected_pairs = validate_paired_keys(observations["advisory"], observations["authority"])
    expected_keys = {(left["scenario_id"], left["seed"]) for left, unused in expected_pairs}
    observed_keys: set[tuple[str, int]] = set()
    for advisory_cell, authority_cell, scenario, advisory_snapshot, authority_snapshot in paired_pre_gate:
        compare_paired_pre_gate(advisory_cell, authority_cell, scenario,
                                advisory_snapshot, authority_snapshot)
        key = (scenario.scenario_id, advisory_cell.seed)
        if key in observed_keys:
            raise ValueError("paired pre-gate contexts must be unique")
        observed_keys.add(key)
        pair = next((pair for pair in expected_pairs
                     if (pair[0]["scenario_id"], pair[0]["seed"]) == key), None)
        if pair is None or pair[0].get("pre_gate_snapshot_digest") != pre_gate_snapshot_digest(advisory_snapshot) or \
                pair[1].get("pre_gate_snapshot_digest") != pre_gate_snapshot_digest(authority_snapshot):
            raise ValueError("analysis observations do not bind the supplied pre-gate snapshots")
    if observed_keys != expected_keys:
        raise ValueError("paired pre-gate contexts must cover every Advisory/Authority unit")
    out = {}
    for name, a, b in (("baseline-vs-advisory", "baseline", "advisory"), ("advisory-vs-authority", "advisory", "authority"), ("baseline-vs-authority", "baseline", "authority")):
        pairs = validate_paired_keys(observations[a], observations[b])
        out[name] = paired_bootstrap_ci([_finite(x[metric]) for x, _ in pairs], [_finite(y[metric]) for _, y in pairs], resamples=resamples, seed=seed)
    return out

def benjamini_hochberg(p_values: Sequence[float], q: float = .05) -> list[dict[str, Any]]:
    if not 0 < q < 1: raise ValueError("q must be in (0,1)")
    ps = [_finite(p) for p in p_values]
    if any(p < 0 or p > 1 for p in ps): raise ValueError("p-values must be in [0,1]")
    order = sorted(range(len(ps)), key=lambda i: (ps[i], i)); adj = [1.] * len(ps); running = 1.
    for rank, i in reversed(list(enumerate(order, 1))): running = min(running, ps[i]*len(ps)/rank); adj[i] = running
    return [{"p_value": ps[i], "q_value": adj[i], "rejected": adj[i] <= q} for i in range(len(ps))]

wilson_95 = wilson_interval
paired_risk_difference = paired_binary_risk_difference
mc_nemar_exact = exact_mcnemar
bootstrap_95 = bootstrap_ci
benjamini_hochberg_correction = benjamini_hochberg
