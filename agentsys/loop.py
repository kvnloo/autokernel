#!/usr/bin/env python3
"""AgentSys loop: AutoKernel's keep/revert decision for one surface at a time.

  loop.py aa   --episodes AA.jsonl                          -> noise floor (A/A half-width)
  loop.py ab   --episodes AB.jsonl --incumbent NAME --candidate NAME --track T --surface S
               [--ledger LEDGER.jsonl] [--promote STATE_DIR]

Statistic (council, 2026-11-03): deadline-capped mean of charged wall seconds (failures charged
at the deadline), computed as a paired per-task ratio candidate/incumbent with a bootstrap CI.
Turns (tool events) are co-primary. Guards: pass rate may not drop by more than one task, p90
charged wall may not rise more than 10%.

KEEP   iff ratio CI upper bound < 0.97, the latest A/A CI contains 1, and no guard fails.
DROP   iff ratio CI lower bound > 1 or a guard fails.
otherwise INCONCLUSIVE (a valid outcome; grow the set or redesign).
"""
import argparse, json, random, shutil, statistics, time
from collections import defaultdict
from pathlib import Path


def load(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def per_task(eps, key):
    by = defaultdict(lambda: defaultdict(list))
    for e in eps:
        by[e["task"]][key(e)].append(e)
    return by


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else float("nan")


def boot_ratio(pairs, n=4000, seed=7):
    """pairs: [(incumbent_charged_mean, candidate_charged_mean)] per task. Ratio of sums."""
    rnd = random.Random(seed)
    point = sum(c for _, c in pairs) / sum(i for i, _ in pairs)
    stats = []
    for _ in range(n):
        s = [pairs[rnd.randrange(len(pairs))] for _ in pairs]
        stats.append(sum(c for _, c in s) / max(1e-9, sum(i for i, _ in s)))
    stats.sort()
    return point, stats[int(0.025 * n)], stats[int(0.975 * n)]


def summarize(eps):
    return {"n": len(eps), "pass_rate": round(mean([e["pass"] for e in eps]), 3),
            "charged_mean_s": round(mean([e["charged_s"] for e in eps]), 1),
            "charged_p90_s": round(p90([e["charged_s"] for e in eps]), 1),
            "tools_mean": round(mean([e["tool_events"] for e in eps]), 2),
            "turns_mean": round(mean([e.get("tool_turns", e["tool_events"]) + 1 for e in eps]), 2),
            "tokens_mean": round(mean([(e.get("tokens") or {}).get("total", 0) for e in eps]))}


def cmd_aa(a):
    eps = load(a.episodes)
    if a.pair:  # within-block A/A: two identically configured arms interleaved in the same blocks
        x, y = a.pair.split(",")
        eps = [e for e in eps if e["arm"] in (x, y)]
        by = per_task(eps, lambda e: 0 if e["arm"] == x else 1)
    elif a.arm:  # A/A from an A/B run: the incumbent's rep 0 vs rep 1 on the same tasks
        eps = [e for e in eps if e["arm"] == a.arm]
        by = per_task(eps, lambda e: e["rep"])
    else:
        by = per_task(eps, lambda e: e["order"])
    pairs = [(mean([e["charged_s"] for e in d[0]]), mean([e["charged_s"] for e in d[1]]))
             for d in by.values() if d.get(0) and d.get(1)]
    point, lo, hi = boot_ratio(pairs)
    half = (hi - lo) / 2
    out = {"kind": "aa", "tasks": len(pairs), "ratio": round(point, 3), "ci": [round(lo, 3), round(hi, 3)],
           "half_width": round(half, 3), "summary": summarize(eps), "ts": time.time(), "episodes": a.episodes}
    print(json.dumps(out, indent=1))
    if a.ledger:
        with open(a.ledger, "a") as fh:
            fh.write(json.dumps(out) + "\n")


def last_aa(ledger):
    rows = [r for r in load(ledger) if r.get("kind") == "aa"] if ledger and Path(ledger).exists() else []
    return rows[-1] if rows else None


def cmd_ab(a):
    eps = load(a.episodes)
    inc = [e for e in eps if e["arm"] == a.incumbent]
    cand = [e for e in eps if e["arm"] == a.candidate]
    by = per_task(eps, lambda e: e["arm"])
    pairs = [(mean([e["charged_s"] for e in d[a.incumbent]]), mean([e["charged_s"] for e in d[a.candidate]]))
             for d in by.values() if d.get(a.incumbent) and d.get(a.candidate)]
    point, lo, hi = boot_ratio(pairs)
    turns = lambda e: e.get("tool_turns", e["tool_events"]) + 1  # +1 is the final answer turn
    tpairs = [(mean([turns(e) for e in d[a.incumbent]]), mean([turns(e) for e in d[a.candidate]]))
              for d in by.values() if d.get(a.incumbent) and d.get(a.candidate)]
    tpoint, tlo, thi = boot_ratio(tpairs)
    si, sc = summarize(inc), summarize(cand)
    aa = last_aa(a.ledger)
    # Epoch 2 rule: the paired bootstrap CI already carries the referee noise, so subtracting the
    # A/A half-width again made any realistic effect unkeepable (epoch 1: a 4x win stayed
    # INCONCLUSIVE). The A/A instead must show the referee is unbiased (its CI contains 1).
    threshold = 0.97
    guards = {
        "pass_noninferior": sc["pass_rate"] >= si["pass_rate"] - 1 / max(1, len(pairs)),
        "p90_not_worse": sc["charged_p90_s"] <= si["charged_p90_s"] * 1.10,
        "aa_unbiased": aa is not None and aa["ci"][0] <= 1 <= aa["ci"][1],
    }
    if all(guards.values()) and hi < threshold:
        verdict = "KEEP"
    elif lo > 1 or not guards["pass_noninferior"] or not guards["p90_not_worse"]:
        verdict = "DROP"
    else:
        verdict = "INCONCLUSIVE"
    out = {"kind": "ab", "track": a.track, "surface": a.surface, "incumbent": a.incumbent,
           "candidate": a.candidate, "tasks": len(pairs), "wall_ratio": round(point, 3),
           "wall_ci": [round(lo, 3), round(hi, 3)], "turn_ratio": round(tpoint, 3),
           "turn_ci": [round(tlo, 3), round(thi, 3)], "keep_threshold": round(threshold, 3),
           "guards": guards, "incumbent_summary": si, "candidate_summary": sc, "verdict": verdict,
           "ts": time.time(), "episodes": a.episodes}
    print(json.dumps(out, indent=1))
    if a.ledger:
        with open(a.ledger, "a") as fh:
            fh.write(json.dumps(out) + "\n")
    if verdict == "KEEP" and a.promote:  # candidate becomes the incumbent for the next experiment
        arms = Path(a.promote)  # the directory holding <arm>.json files
        arm = json.loads((arms / f"{a.candidate}.json").read_text())
        old = arms / "incumbent.json"
        shutil.copy(old, arms / f"incumbent-before-{a.candidate}.json")  # revert point
        arm.update(name="incumbent", promoted_from=a.candidate, promoted_at=time.time())
        old.write_text(json.dumps(arm, indent=1))
        print("promoted", a.candidate, "-> incumbent")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("aa")
    s.add_argument("--episodes", required=True)
    s.add_argument("--ledger")
    s.add_argument("--arm", help="derive A/A from this arm's rep 0 vs rep 1")
    s.add_argument("--pair", help="A,B: A/A between two identical arms run in the same blocks")
    s.set_defaults(fn=cmd_aa)
    s = sub.add_parser("ab")
    for k in ("episodes", "incumbent", "candidate", "track", "surface"):
        s.add_argument("--" + k, required=True)
    s.add_argument("--ledger")
    s.add_argument("--promote")
    s.set_defaults(fn=cmd_ab)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
