"""Parity gate — decide whether a CANDIDATE engine may replace the BASELINE.

This encodes the CoLearn swap rule: we ship our baseline, and only swap in their
engine when the harness proves it is at least as good AND leaks nothing.

Gate (all must pass for the candidate to be eligible):
  1. SAFETY   candidate ACL-leak total == 0           (hard, non-negotiable)
  2. QUALITY  recall@k(cand)     >= recall@k(base)  - eps
  3. QUALITY  citation_rate(cand) >= citation(base) - eps
  4. PERF     latency p95(cand)  <= latency_budget_ms

eps is a small tolerance so identical-quality engines aren't blocked by noise.
The gate is evaluated on the HOLDOUT split by default (report on data you didn't
tune on).
"""
from __future__ import annotations

from dataclasses import dataclass

from runner import RunReport


@dataclass
class GateConfig:
    recall_eps: float = 0.0
    citation_eps: float = 0.0
    latency_budget_ms: float = 1500.0


@dataclass
class GateResult:
    passed: bool
    reasons: list[str]


def evaluate_gate(base: RunReport, cand: RunReport, cfg: GateConfig = GateConfig()) -> GateResult:
    reasons: list[str] = []
    b, c = base.agg, cand.agg

    # 1) SAFETY — any leak fails outright.
    if c.acl_leak_total != 0:
        reasons.append(f"FAIL safety: candidate ACL leaks = {c.acl_leak_total} (must be 0)")
    else:
        reasons.append("PASS safety: 0 ACL leaks")

    # 2) recall
    if c.recall_at_k + cfg.recall_eps >= b.recall_at_k:
        reasons.append(f"PASS recall@k: {c.recall_at_k:.3f} >= {b.recall_at_k:.3f} (base)")
    else:
        reasons.append(f"FAIL recall@k: {c.recall_at_k:.3f} < {b.recall_at_k:.3f} (base)")

    # 3) citation rate
    if c.citation_rate + cfg.citation_eps >= b.citation_rate:
        reasons.append(f"PASS citation: {c.citation_rate:.3f} >= {b.citation_rate:.3f} (base)")
    else:
        reasons.append(f"FAIL citation: {c.citation_rate:.3f} < {b.citation_rate:.3f} (base)")

    # 4) latency budget
    if c.latency_p95_ms <= cfg.latency_budget_ms:
        reasons.append(f"PASS latency p95: {c.latency_p95_ms:.0f}ms <= {cfg.latency_budget_ms:.0f}ms")
    else:
        reasons.append(f"FAIL latency p95: {c.latency_p95_ms:.0f}ms > {cfg.latency_budget_ms:.0f}ms")

    passed = all(r.startswith("PASS") for r in reasons)
    return GateResult(passed=passed, reasons=reasons)


def print_parity(base: RunReport, cand: RunReport, gate: GateResult) -> None:
    b, c = base.agg, cand.agg
    rows = [
        ("recall@k", b.recall_at_k, c.recall_at_k, "higher"),
        ("precision@k", b.precision_at_k, c.precision_at_k, "higher"),
        ("MRR", b.mrr, c.mrr, "higher"),
        ("citation rate", b.citation_rate, c.citation_rate, "higher"),
        ("ACL leaks", b.acl_leak_total, c.acl_leak_total, "zero"),
        ("latency p95 (ms)", b.latency_p95_ms, c.latency_p95_ms, "lower"),
    ]
    print()
    print(f"PARITY  baseline={base.engine_name} v{base.engine_version}  "
          f"candidate={cand.engine_name} v{cand.engine_version}  split={base.split}")
    print(f"{'metric':<20}{'baseline':>12}{'candidate':>12}{'delta':>12}  goal")
    print("-" * 70)
    for name, bv, cv, goal in rows:
        delta = cv - bv
        print(f"{name:<20}{bv:>12.3f}{cv:>12.3f}{delta:>+12.3f}  {goal}")
    print("-" * 70)
    for r in gate.reasons:
        print("  " + r)
    print()
    print("GATE:", "CANDIDATE ELIGIBLE TO REPLACE BASELINE" if gate.passed
          else "CANDIDATE BLOCKED — keep baseline")
