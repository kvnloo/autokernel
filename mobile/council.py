#!/usr/bin/env python3
"""Stall-triggered council for z0live autoresearch (meta-orchestrator, never writes code).

Seats are the owner's council: Opus 5.5, Fable 5.1, Astra (gpt-6-astra; 6.2 is not
served to this account), Sol 6.1. Each seat runs natively through Hermes on the owner's
subscriptions (`hermes -z`, read-only file tools), bounded by a systemd scope.

  council.py convene --rounds 1|2 [--reason TEXT]   brief from live state, R1 independent, R2 cross-exam
  council.py preflight                              tiny READY call per seat (verifies model + billing route)

Outputs land in state/council/<stamp>/ with a manifest that records requested vs observed
model, api calls and failures. No substitutions: a failed seat stays failed.
"""
import argparse, concurrent.futures as cf, datetime, hashlib, json, os, pathlib, signal, subprocess, time

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state"
SEATS = [
    ("opus", "anthropic", "claude-opus-5-5", "architecture and synthesis critic; owns the final ranking of attack surfaces"),
    ("fable", "anthropic", "claude-fable-5-1", "adversarial auditor: benchmark gaming, measurement validity, quality regressions"),
    ("astra", "openai-codex", "gpt-6-astra", "hardware/systems economist for Snapdragon 8 Elite (Oryon CPU, Adreno 830, Hexagon v79)"),
    ("sol", "openai-codex", "gpt-6.1-sol", "practical implementer: cheapest next experiment that could falsify the leading idea"),
]


def tail_ledger(n=40):
    p = STATE / "ledger.jsonl"
    if not p.exists():
        return []
    out = []
    for l in p.read_text().splitlines()[-n:]:
        e = json.loads(l)
        out.append({k: e.get(k) for k in ("at", "cmd", "lane", "id", "decision", "fails", "ci", "need", "hypothesis")} |
                   {"objective": {s: (v or {}).get("objective") for s, v in (e.get("summary") or {}).items()}})
    return out


def brief(reason):
    def rd(p, limit=6000):
        p = ROOT / p
        return p.read_text()[:limit] if p.exists() else "(missing)"
    research = sorted((ROOT / "research").glob("*.md")) if (ROOT / "research").exists() else []
    return f"""You are a seat on Kevin's research council for z0live: a fully local, offline voice assistant on his
Galaxy S25 Ultra that should get as close as possible to GPT-Live (natural voice, fast full-duplex turn-taking).
An autoresearch loop (driver: Opus) edits candidate configs / build variants / kernel patches, and a FIXED on-device
benchmark (mobile/bench_s25.py) measures them interleaved against the incumbent and keeps or reverts.
You are convened because: {reason}

Read these files with your read-only file tools (do not edit anything, do not run commands):
- {ROOT}/program_mobile.md (loop rules), {ROOT}/hw/s25.json (measured hardware)
- {ROOT}/bench_s25.py (the referee), {ROOT}/state/incumbents.json, {ROOT}/state/stall.json, {ROOT}/state/noise.json
- {ROOT}/state/ledger.jsonl (full experiment history; failures included)
- research digests: {', '.join(str(p) for p in research) or '(none yet)'}
- optional if present: {ROOT}/STAGES.md, {ROOT}/variants/README.md, {ROOT}/QUALITY.md

Recent ledger (newest last):
{json.dumps(tail_ledger(), indent=0)[:9000]}

Answer in at most 900 words, labelling claims [Measured] (from the ledger), [Source] (cite file/URL) or [Inference]:
1. Diagnosis: why are results stalling (or what is the binding constraint now)? Use Amdahl: which stage of the
   voice turn (turn detect, ASR, LLM first token, TTS first audio, playback) dominates user-perceived latency or naturalness?
2. Rank the next 5 attack surfaces specific to THIS hardware (CPU core placement/threads, ISA variants, KleidiAI,
   OpenMP, Adreno OpenCL/Vulkan, Hexagon QNN/QAIRT/LiteRT, quantization, streaming chunking, model swap, pipeline overlap,
   speculative/early-start TTS, warm residency). For each: predicted effect size on the objective, cost, a falsifier,
   and the exact first experiment (a candidate JSON diff or a build variant).
3. Measurement validity: any way the loop could be fooling itself (thermal drift, owner activity, cold vs warm
   process, quality gate blind spots, eval set too small)? What must change in the bench (new epoch) if anything?
4. Should any track be retargeted or stopped? Say so plainly.
Do not invent numbers. Do not simulate other seats.
"""


def run_seat(seat, label, prompt, outdir, timeout):
    name, prov, model, role = seat
    stem = outdir / f"{name}-{label}"
    stem.with_suffix(".prompt.txt").write_text(prompt)
    usage = stem.with_suffix(".usage.json")
    cmd = ["hermes", "-p", "clean", "--provider", prov, "-m", model, "--ignore-rules", "-t", "file",
           "--usage-file", str(usage), "-z", prompt + f"\n\nYour assigned role: {role}.\nPrint your answer as your final message."]
    unit = f"zlv-council-{name}-{label}-{os.getpid()}"
    full = ["systemd-run", "--user", "--scope", "--unit", unit, "--slice=agent.slice", "-p", "MemoryMax=3G", "--"] + cmd
    t = time.monotonic()
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout); code = p.returncode
    except subprocess.TimeoutExpired:
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", unit + ".scope"], capture_output=True)
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=15); code = 124
    stem.with_suffix(".md").write_text(out)
    stem.with_suffix(".stderr.log").write_text(err)
    u = json.loads(usage.read_text()) if usage.exists() else {}
    status = "ok"
    if code or not out.strip():
        status = f"failed: exit {code}"
    elif u.get("failed") or (u.get("api_calls") or 0) < 1:
        status = "failed: usage file shows no successful api call"
    elif u.get("model") and model not in str(u.get("model")):
        status = f"failed: observed model {u.get('model')} != {model}"
    return {"seat": name, "model": model, "provider": prov, "status": status, "exit": code,
            "observed_model": u.get("model"), "api_calls": u.get("api_calls"), "elapsed_s": round(time.monotonic() - t, 1),
            "answer": str(stem.with_suffix(".md")), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}


def convene(rounds, reason, timeout=1500):
    d = STATE / "council" / f"{datetime.datetime.now():%Y%m%d-%H%M%S}"
    d.mkdir(parents=True)
    b = brief(reason)
    (d / "brief.md").write_text(b)
    with cf.ThreadPoolExecutor(4) as ex:
        r1 = list(ex.map(lambda s: run_seat(s, "r1", b, d, timeout), SEATS))
    manifest = {"reason": reason, "r1": r1}
    if rounds >= 2:
        def cross(seat):
            others = "\n\n".join(f"## {x['seat']} ({x['model']}) round 1, status {x['status']}\n" +
                                 (pathlib.Path(x["answer"]).read_text()[:7000] if x["status"] == "ok" else "(unavailable)")
                                 for x in r1 if x["seat"] != seat[0])
            p = (b + "\n\nROUND 2 (cross-examination). Here are the other seats' round-1 answers. Quote what they actually wrote; "
                 "state agreements, objections with quotes, revisions to your own position, and your final top-3 next experiments. "
                 "Max 700 words.\n\n" + others)
            return run_seat(seat, "r2", p, d, timeout)
        with cf.ThreadPoolExecutor(4) as ex:
            manifest["r2"] = list(ex.map(cross, SEATS))
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
    st = json.loads((STATE / "stall.json").read_text()) if (STATE / "stall.json").exists() else {}
    st.setdefault("_council", []).append({"at": d.name, "rounds": rounds, "reason": reason,
                                          "ok": [x["seat"] for x in manifest.get("r2", r1) if x["status"] == "ok"]})
    tracks = {k: v for k, v in st.items() if not k.startswith("_")}
    st["_experiments_at_last_council"] = sum(v.get("experiments", 0) for v in tracks.values())
    stalled = any(v.get("since_keep", 0) >= 3 for v in tracks.values())
    st["_stall_streak"] = st.get("_stall_streak", 0) + 1 if stalled else 0
    (STATE / "stall.json").write_text(json.dumps(st, indent=2))
    print(json.dumps({"dir": str(d), "seats": [(x["seat"], x["status"]) for x in manifest.get("r2", r1)]}, indent=1))


def due():
    """Stall policy (owner): council steps in when hill-climbing stalls, more often the longer it stalls.
    A track stalls after 3 consecutive non-keeps. Level 1 (first stall since last council): 1 round.
    Level 2: 2 rounds (cross-exam) + fresh profiling. Level 3: retarget or stop the track.
    Independently, every 10 experiments overall get a 1-round check."""
    st = json.loads((STATE / "stall.json").read_text()) if (STATE / "stall.json").exists() else {}
    councils = st.get("_council", [])
    last = councils[-1]["at"] if councils else ""
    tracks = {k: v for k, v in st.items() if not k.startswith("_")}
    stalled = {k: v for k, v in tracks.items() if v.get("since_keep", 0) >= 3}
    total = sum(v.get("experiments", 0) for v in tracks.values())
    since = total - st.get("_experiments_at_last_council", 0)
    level = 0
    if stalled:
        streak = st.get("_stall_streak", 0) + 1  # consecutive councils without an intervening keep
        level = min(streak, 3)
    elif since >= 10:
        level = 1
    return {"level": level, "stalled": sorted(stalled), "experiments": total, "since_last_council": since, "last_council": last,
            "rounds": {0: 0, 1: 1, 2: 2, 3: 2}[level],
            "action": {0: "keep hill-climbing", 1: "convene 1 round", 2: "convene 2 rounds + fresh profiling + new research wave",
                       3: "convene 2 rounds; retarget or stop stalled tracks; record null result"}[level]}


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("due")
    c = sp.add_parser("convene"); c.add_argument("--rounds", type=int, default=1); c.add_argument("--reason", default="scheduled check")
    sp.add_parser("preflight")
    a = ap.parse_args()
    if a.cmd == "due":
        print(json.dumps(due(), indent=1))
    elif a.cmd == "convene":
        convene(a.rounds, a.reason)
    else:
        d = STATE / "council" / "preflight"; d.mkdir(parents=True, exist_ok=True)
        with cf.ThreadPoolExecutor(4) as ex:
            res = list(ex.map(lambda s: run_seat(s, "ready", "Reply with exactly READY. Do not use tools.", d, 240), SEATS))
        print(json.dumps([(r["seat"], r["status"], r["observed_model"], r["api_calls"]) for r in res]))


if __name__ == "__main__":
    main()
