#!/usr/bin/env python3
"""AgentSys referee: run frozen Locate tasks through real Hermes oneshot episodes, per arm.

This is AutoKernel's bench.py for the agent system. An *arm* is one configuration of the one
editable surface under test (JSON: name, preamble_file, model, provider, reasoning, max_turns,
budget_s). An *episode* is a fresh `hermes chat --oneshot` session on one task.

Scores per episode: pass (an answer key appears in the final ANSWER line or reply), wall
seconds (failures/timeouts are charged at the deadline), tool turns, tokens.

Episodes are interleaved ABBA per task and gated on host idleness (memory PSI and available
RAM), so the comparison is paired and no arm systematically gets the quieter host.

usage:
  referee.py run --tasks T.jsonl --arm A.json [--arm B.json] --reps N --out EPISODES.jsonl
             [--split dev] [--limit K] [--jobs J]
"""
import argparse, concurrent.futures as cf, json, os, re, subprocess, tempfile, threading, time, uuid
from pathlib import Path

HOME = str(Path.home())
EPISODE_TAG = "[agentsys-episode]"
INSTRUCTIONS = (
    f"{EPISODE_TAG} Read-only locate task on this machine. Find the answer using any read-only "
    "tools. Do not modify files, services, git state or remote systems. Be fast. End with one line "
    "exactly: ANSWER: <absolute path, URL, issue/PR reference or identifier>.\n\nTask: ")
LOCK = threading.Lock()


def norm(s):
    return (s or "").replace("~/", HOME + "/").replace("/workspace/hermes-home", HOME + "/.hermes").lower()


def git_checkout_ok(answer, remote, branch=None):
    """Sufficiency rubric for "where is a checkout of owner/repo": any checkout of that remote
    counts (machines hold several), and when the task names a branch it must exist there."""
    m = re.search(r"(/[\w.@+/-]+)", (answer or "").replace("~/", HOME + "/"))
    if not m or not os.path.isdir(m.group(1)):
        return False
    path = m.group(1)
    run = lambda *a: subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, timeout=10)
    slugs = {m.group(1).lower() for m in re.finditer(r"github\.com[:/]([^\s]+?)(?:\.git)?\s", run("remote", "-v").stdout)}
    if remote.lower() not in slugs:
        return False
    return not branch or run("rev-parse", "--verify", "--quiet", branch).returncode == 0 or \
        run("rev-parse", "--verify", "--quiet", "origin/" + branch).returncode == 0


def judge(task, text):
    m = re.findall(r"ANSWER:\s*(.+)", text or "")
    answer = m[-1].strip()[:300] if m else None
    if task.get("git_remote"):
        return git_checkout_ok(answer or text, task["git_remote"], task.get("branch")), answer
    ans = norm(m[-1] if m else text)
    keys = [norm(k) for k in task["keys"]]
    return any(k and k in ans for k in keys), answer


def host_idle(min_avail_gb=3.0, max_mem_full=5.0, wait_s=600):
    """Block until the host is quiet enough; return the covariates seen at start."""
    t0 = time.time()
    while True:
        psi = Path("/proc/pressure/memory").read_text()
        full = float(re.search(r"full avg10=([\d.]+)", psi).group(1))
        avail = int(re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) / 1048576
        io = float(re.search(r"full avg10=([\d.]+)", Path("/proc/pressure/io").read_text()).group(1))
        load = os.getloadavg()[0]
        if (full < max_mem_full and avail >= min_avail_gb) or time.time() - t0 > wait_s:
            return {"mem_full10": full, "avail_gb": round(avail, 2), "io_full10": io,
                    "load1": round(load, 2), "gate_wait_s": round(time.time() - t0, 1)}
        time.sleep(5)


def episode(task, arm, rep, order):
    if task.get("setup"):  # a live fixture (e.g. a job that writes a line later), unique per episode
        nonce = uuid.uuid4().hex[:8]
        task = {**task, "setup": task["setup"].replace("{nonce}", nonce),
                "question": task["question"].replace("{nonce}", nonce),
                "keys": [k.replace("{nonce}", nonce) for k in task["keys"]]}
    pre = Path(arm["preamble_file"]).read_text() + "\n\n" if arm.get("preamble_file") else ""
    query = task.get("instructions", INSTRUCTIONS) + task["question"]
    if arm.get("oracle_entry"):  # Exp 3 upper bound: hand the agent the right starting point
        key = task.get("entry") or task["keys"][0]
        query += f"\n\nHint: start looking at or near `{os.path.dirname(key.rstrip('/')) if key.startswith(('/', '~')) else key}`."
    if pre:
        query = pre + query
    with tempfile.TemporaryDirectory(prefix="agsys-") as wd:
        qf = Path(wd) / "q.txt"
        qf.write_text(query)
        cmd = ["hermes", "chat", "--query-file", str(qf), "--oneshot", "--format", "stream-json",
               "--max-turns", str(arm.get("max_turns", 40))]
        for flag, key in (("-m", "model"), ("--provider", "provider"), ("--reasoning", "reasoning")):
            if arm.get(key):
                cmd += [flag, arm[key]]
        deadline = float(arm.get("budget_s", 300))
        cov = host_idle()
        if task.get("setup"):
            subprocess.Popen(task["setup"], shell=True, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        timed_out, events = False, []
        try:
            p = subprocess.run(cmd, cwd=HOME, capture_output=True, text=True, timeout=deadline + 30)
            raw = p.stdout
        except subprocess.TimeoutExpired as exc:
            timed_out, raw = True, (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        wall = time.time() - t0
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    tools = [e for e in events if e.get("type") == "tool_use"]
    # A model turn that calls tools emits its tool_use events back to back; a tool_use right
    # after a tool_result starts the next turn. Batched calls therefore share one turn.
    turns, prev = 0, None
    for e in events:
        if e.get("type") == "tool_use" and prev != "tool_use":
            turns += 1
        if e.get("type") in ("tool_use", "tool_result"):
            prev = e["type"]
    tool_ms = sum(e.get("duration_ms") or 0 for e in events if e.get("type") == "tool_result")
    text = result.get("text") or "".join(e.get("text", "") for e in events if e.get("type") == "text")
    ok, answer = judge(task, text)
    ok = ok and not timed_out and wall <= deadline and not result.get("error")
    return {"task": task["id"], "kind": task.get("kind"), "arm": arm["name"], "rep": rep, "order": order,
            "pass": ok, "wall_s": round(wall, 2), "charged_s": round(wall if ok else deadline, 2),
            "timed_out": timed_out, "tool_events": len(tools), "tool_turns": turns, "tool_s": round(tool_ms / 1000, 2), "answer": answer,
            "tokens": result.get("tokens"), "session_id": result.get("session_id"),
            "error": result.get("error"), "ts": time.time(), "host": cov}


def schedule(tasks, arms, reps):
    """ABBA per task: rep r of task i runs A,B on even (i+r) and B,A on odd."""
    jobs = []
    for r in range(reps):
        for i, t in enumerate(tasks):
            pair = arms if len(arms) == 1 or (i + r) % 2 == 0 else arms[::-1]
            if len(arms) == 1:  # A/A: two replicas of the same arm, labelled by order
                jobs += [(t, arms[0], r, 0), (t, arms[0], r, 1)]
            else:
                jobs += [(t, a, r, k) for k, a in enumerate(pair)]
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "rescore"])
    ap.add_argument("--episodes", help="rescore: episodes file to re-judge in place with the current rubric")
    ap.add_argument("--deadline", type=float, help="rescore: the run's budget_s (failures are charged at it)")
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--arm", action="append")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "rescore":  # rubric fixes apply to both arms alike; answers and walls are kept
        tasks = {t["id"]: t for t in map(json.loads, open(a.tasks))}
        eps = [json.loads(l) for l in open(a.episodes)]
        changed = 0
        for e in eps:
            ok = judge(tasks[e["task"]], "ANSWER: " + (e["answer"] or ""))[0] and not e["timed_out"] and e["wall_s"] <= a.deadline
            changed += ok != e["pass"]
            e["pass"], e["charged_s"] = ok, (e["wall_s"] if ok else a.deadline)
        Path(a.episodes).write_text("".join(json.dumps(e) + "\n" for e in eps))
        print(f"rescored {len(eps)} episodes, {changed} verdicts changed")
        return
    tasks = [json.loads(l) for l in open(a.tasks) if l.strip()]
    tasks = [t for t in tasks if t.get("split", "dev") == a.split][: a.limit]
    arms = [json.loads(Path(p).read_text()) for p in a.arm]
    jobs = schedule(tasks, arms, a.reps)
    done = set()
    if os.path.exists(a.out):  # resume: skip episodes already recorded
        for l in open(a.out):
            e = json.loads(l)
            done.add((e["task"], e["arm"], e["rep"], e["order"]))
    jobs = [j for j in jobs if (j[0]["id"], j[1]["name"], j[2], j[3]) not in done]
    print(f"{len(tasks)} tasks x {len(arms)} arm(s) x {a.reps} rep(s): {len(jobs)} episodes to run", flush=True)
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(episode, *j) for j in jobs]
        for f in cf.as_completed(futs):
            e = f.result()
            with LOCK, open(a.out, "a") as fh:
                fh.write(json.dumps(e) + "\n")
            print(f"{e['task']:<10} {e['arm']:<14} r{e['rep']}o{e['order']} pass={int(e['pass'])} "
                  f"wall={e['wall_s']:.0f}s tools={e['tool_events']}", flush=True)


if __name__ == "__main__":
    main()
