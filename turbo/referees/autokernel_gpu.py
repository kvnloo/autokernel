#!/usr/bin/env python3
"""Launch-configuration hill climb for one autokernel Triton kernel.

Each invocation measures the incumbent against one mutated candidate in
alternating pairs with the fixed bench.py harness (its correctness stages are
the oracle) and writes output/result.json for the HC-TURBO dispatcher.
Incumbent and tried genomes persist in --state across runs.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

EXHAUSTED = "all available bounded candidates have been measured"  # turbo.queue.EXHAUSTED

WARPS = (1, 2, 4, 8, 16)
STAGES = (1, 2, 3, 4, 5)
BLOCKS = (16, 32, 64, 128, 256)
LAUNCH = re.compile(r"(\w+_kernel\[[^\]\n]+\]\()")


def literal_blocks(source: str) -> dict[str, int]:
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^\s+(BLOCK[A-Z_]*)\s*=\s*(\d+)\s*$", source, re.MULTILINE)}


def apply(source: str, genome: dict) -> str:
    for name, value in genome.get("blocks", {}).items():
        source = re.sub(rf"^(\s+{name}\s*=\s*)\d+\s*$", rf"\g<1>{value}", source, flags=re.MULTILINE)
    options = f"num_warps={genome['num_warps']}, num_stages={genome['num_stages']}"
    launches = list(LAUNCH.finditer(source))
    if not launches:
        raise ValueError("kernel has no name_kernel[grid](...) launch to tune")
    for m in reversed(launches):  # append kwargs before each call's closing paren
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        head = source[:i - 1].rstrip()
        sep = " " if head.endswith(",") else ", "
        source = head + sep + options + source[i - 1:]
    return source


def key(genome: dict) -> str:
    return json.dumps(genome, sort_keys=True)


def mutate(genome: dict, tried: set[str], rng: random.Random) -> dict | None:
    for _ in range(500):
        child = json.loads(json.dumps(genome))
        for _ in range(rng.choice((1, 1, 1, 2))):
            gene = rng.choice(["num_warps", "num_stages", *child["blocks"]])
            if gene == "num_warps":
                child[gene] = rng.choice([w for w in WARPS if w != child[gene]])
            elif gene == "num_stages":
                child[gene] = rng.choice([s for s in STAGES if s != child[gene]])
            else:
                child["blocks"][gene] = rng.choice([b for b in BLOCKS if b != child["blocks"][gene]])
        if key(child) not in tried:
            return child
    return None


def bench(python: str, repo: Path, workdir: Path, source: str, kernel: str, timeout: int) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    for name in ("bench.py", "reference.py"):
        shutil.copy(repo / name, workdir / name)
    (workdir / "kernel.py").write_text(source)
    try:
        out = subprocess.run([python, "bench.py", "--quick", "--kernel", kernel], cwd=workdir,
                             capture_output=True, text=True, timeout=timeout)
        text = out.stdout + out.stderr
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout if isinstance(exc.stdout, str) else "") + "\nTIMEOUT"
    (workdir / "bench.log").write_text(text)
    final = text.rsplit("=== FINAL ===", 1)[-1]
    correct = re.search(r"^correctness:\s*PASS", final, re.MULTILINE) is not None
    latency = re.findall(r"^kernel_latency_us:\s*([0-9.]+)", text, re.MULTILINE)
    return {"correct": correct, "latency_us": float(latency[-1]) if latency else None}


def emit(result: dict) -> None:
    Path("result.json").write_text(json.dumps(result))
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--python", required=True)
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=400)
    args = ap.parse_args()
    out = Path("bench")  # cwd is the dispatcher's per-run referee directory
    out.mkdir(exist_ok=True)
    state_dir = args.state / args.kernel
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    base_source = (args.repo / "kernels" / f"{args.kernel}.py").read_text()
    if state_file.exists():
        state = json.loads(state_file.read_text())
    else:
        state = {"incumbent": {"num_warps": 4, "num_stages": 2, "blocks": literal_blocks(base_source)},
                 "tried": [], "history": []}
    tried = set(state["tried"]) | {key(state["incumbent"])}
    rng = random.Random()
    candidate = mutate(state["incumbent"], tried, rng)
    result = {"decision": "NOISE", "ci": None, "oracle_passed": False, "checks": {},
              "kernel": args.kernel, "incumbent": state["incumbent"], "candidate": candidate}
    if candidate is None:
        result["reason"] = EXHAUSTED
        emit(result)
        return 0
    sources = {"inc": apply(base_source, state["incumbent"]), "cand": apply(base_source, candidate)}
    runs = {"inc": [], "cand": []}
    for i in range(args.pairs):
        order = ("inc", "cand") if i % 2 == 0 else ("cand", "inc")
        for slot in order:
            run = bench(args.python, args.repo, out / f"{slot}-{i}", sources[slot], args.kernel, args.timeout)
            runs[slot].append(run)
            if not run["correct"] or run["latency_us"] is None:
                break  # a wrong, timed-out or crashing side needs no more timing
        if any(not r["correct"] or r["latency_us"] is None for r in runs["inc"] + runs["cand"]):
            break
    inc_ok = bool(runs["inc"]) and all(r["correct"] and r["latency_us"] for r in runs["inc"])
    cand_ok = bool(runs["cand"]) and all(r["correct"] and r["latency_us"] for r in runs["cand"])
    if inc_ok:
        tried.add(key(candidate))  # only a measured candidate counts as tried
    if not inc_ok:
        result.update(decision="FAIL", reason="incumbent failed correctness or timing (GPU contention?)")
    elif not cand_ok:
        result.update(decision="REVERT", reason="candidate failed bench.py correctness or did not compile")
    else:
        ratios = sorted(c["latency_us"] / i["latency_us"] for c, i in zip(runs["cand"], runs["inc"]))
        ci = {"lo": ratios[0], "mid": statistics.median(ratios), "hi": ratios[-1]}
        result.update(ci=ci, oracle_passed=True, checks={"correctness_pass": True})
        if ci["hi"] < 1:
            result.update(decision="KEEP", reason=f"every pair faster; median ratio {ci['mid']:.3f}")
            state["incumbent"] = candidate
        elif ci["lo"] > 1:
            result.update(decision="REVERT", reason=f"REVERT_SLOWER; median ratio {ci['mid']:.3f}")
        else:
            result.update(reason=f"pairs disagree; median ratio {ci['mid']:.3f}")
    result["runs"] = runs
    state["tried"] = sorted(tried)
    state["history"].append({k: result.get(k) for k in ("decision", "ci", "candidate", "reason")})
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(state_file)
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
