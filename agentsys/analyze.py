#!/usr/bin/env python3
import json, sys, statistics as st, collections as C
K=('sid','tool','exec_s','think_s','fanout','result_chars','head','search','has_sleep','ssh')
rows = [{k:v for k,v in json.loads(l).items() if k in K} for l in open(sys.argv[1])]
# parallel results share a parent; only first result of a parent carries think time once
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs)-1, int(p*len(xs)))] if xs else None
def summ(xs):
    return dict(n=len(xs), p50=q(xs,.5), p90=q(xs,.9), p99=q(xs,.99), hours=round(sum(xs)/3600,1))
valid = [r for r in rows if r["exec_s"] is not None and 0 <= r["exec_s"] < 6*3600]
print("rows", len(rows), "valid", len(valid))
tot_exec = sum(r["exec_s"] for r in valid)
# think: dedupe per parent (sid, think_s, fanout) approx by first row of a burst
seen=set(); thinks=[]
for r in valid:
    k=(r["sid"], r["think_s"])
    if r["think_s"] is not None and k not in seen and 0<=r["think_s"]<3600:
        seen.add(k); thinks.append(r["think_s"])
print(f"TOTAL tool-exec h={tot_exec/3600:.0f}  model-think h={sum(thinks)/3600:.0f}  think p50={q(thinks,.5)} p90={q(thinks,.9)}")
by = C.defaultdict(list)
for r in valid: by[r["tool"]].append(r["exec_s"])
print("\n== per tool (sorted by total hours)")
for t, xs in sorted(by.items(), key=lambda kv: -sum(kv[1]))[:30]:
    s = summ(xs); print(f"{t:38s} n={s['n']:6d} p50={s['p50']:8.2f} p90={s['p90']:9.2f} p99={s['p99']:9.1f} hours={s['hours']:7.1f} share={100*sum(xs)/tot_exec:5.1f}%")
term = [r for r in valid if r["tool"]=="terminal"]
print("\n== terminal by head command (top 25 by hours)")
bh = C.defaultdict(list)
for r in term: bh[r.get("head","?")].append(r["exec_s"])
for h, xs in sorted(bh.items(), key=lambda kv: -sum(kv[1]))[:25]:
    s=summ(xs); print(f"{h:22s} n={s['n']:6d} p50={s['p50']:7.2f} p90={s['p90']:8.2f} hours={s['hours']:6.1f}")
print("\n== terminal sleep/poll cost")
sl=[r["exec_s"] for r in term if r.get("has_sleep")]; print("with sleep:", summ(sl))
print("ssh:", summ([r["exec_s"] for r in term if r.get("ssh")]))
print("\n== search-in-terminal: tool x result-size bucket (p50 exec s)")
def bucket(c): return "<2k" if c<2000 else "<20k" if c<20000 else ">=20k"
sb = C.defaultdict(list)
for r in term:
    for s in r.get("search") or []:
        if len(r["search"])==1: sb[(s,bucket(r["result_chars"]))].append(r["exec_s"])
for k in sorted(sb):
    xs=sb[k]
    if len(xs)>=30: s=summ(xs); print(f"{k[0]:8s} {k[1]:6s} n={s['n']:6d} p50={s['p50']:6.2f} p90={s['p90']:7.2f}")
for t in ["search_files","read_file","mcp__z0_file_search","z0_file_search","session_search","web_search","web_extract","browser_navigate","delegate_task","skill_view","skills_list","memory","execute_code","patch","write_file"]:
    xs=[r["exec_s"] for r in valid if r["tool"]==t]
    if xs:
        sm=[r["exec_s"] for r in valid if r["tool"]==t and r["result_chars"]<2000]
        print(f"{t:22s} all p50={q(xs,.5):.2f} p90={q(xs,.9):.2f} | small-result p50={q(sm,.5) if sm else None}")
print("\n== fanout (calls per assistant turn) distribution of results")
fo=C.Counter(min(r["fanout"],6) for r in valid); print(dict(sorted(fo.items())))
