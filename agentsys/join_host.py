#!/usr/bin/env python3
"""Join per-tool timings with host pressure samples (Track E1: trace x hardware).

usage: join_host.py TOOLS.jsonl HISTORY.json   (TOOLS from extract_hermes.py, HISTORY from host-optimizer)

For every tool result, take the nearest host sample within 5 minutes. Then report median and p90
tool wall and model-turn time per pressure bucket, for the tool families with enough rows.
Prints counts with every number so a thin cell is visible as thin.
"""
import bisect, json, sys
from collections import defaultdict

tools_path, hist_path = sys.argv[1], sys.argv[2]
hist = sorted(json.load(open(hist_path)), key=lambda s: s["ts"])
ts = [s["ts"] for s in hist]


def bucket(v, edges, names):
    return next((n for e, n in zip(edges, names) if v < e), names[-1])


def family(r):
    if r["tool"] == "terminal":
        return "terminal:" + ("search" if r.get("search") else "sleep" if r.get("has_sleep") else "ssh" if r.get("ssh") else "other")
    if r["tool"] == "tool_call":
        inner = r.get("inner") or "?"
        return "mcp:" + (inner.split("__")[1] if inner.startswith("mcp__") else inner)[:20]
    return r["tool"]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


cells = defaultdict(lambda: {"exec": [], "think": []})
joined = skipped = 0
for line in open(tools_path):
    r = json.loads(line)
    i = bisect.bisect_left(ts, r["ts"])
    near = [j for j in (i - 1, i) if 0 <= j < len(ts)]
    if not near:
        skipped += 1
        continue
    j = min(near, key=lambda j: abs(ts[j] - r["ts"]))
    if abs(ts[j] - r["ts"]) > 300 or r["exec_s"] < 0 or r["exec_s"] > 3600:
        skipped += 1
        continue
    p = hist[j]["pressure"]
    io = bucket(p["io"]["full"]["avg60"], (2, 8), ("io<2", "io2-8", "io>8"))
    mem = bucket(p["memory"]["full"]["avg60"], (0.5, 2), ("mem<0.5", "mem0.5-2", "mem>2"))
    for key in ((family(r), io, "*"), (family(r), "*", mem), ("ALL", io, "*"), ("ALL", "*", mem)):
        cells[key]["exec"].append(r["exec_s"])
        if r.get("think_s") is not None and 0 <= r["think_s"] < 900:
            cells[key]["think"].append(r["think_s"])
    joined += 1

print(f"joined={joined} skipped={skipped} samples={len(hist)} span_h={(ts[-1] - ts[0]) / 3600:.1f}")
print(f"{'family':<26}{'io':<8}{'mem':<10}{'n':>6}{'tool p50':>10}{'tool p90':>10}{'turn p50':>10}")
for key in sorted(cells, key=lambda k: (k[0] != "ALL", k)):
    c = cells[key]
    if len(c["exec"]) < 15:
        continue
    print(f"{key[0]:<26}{key[1]:<8}{key[2]:<10}{len(c['exec']):>6}{q(c['exec'], .5):>10.2f}{q(c['exec'], .9):>10.2f}{q(c['think'], .5):>10.2f}")
