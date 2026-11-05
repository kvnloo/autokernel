#!/usr/bin/env python3
"""Full-time supervisor for the z0live S25 autoresearch loop (owner ask N03, 2026-11-05).

Keeps the phone busy with AutoKernel's mobile loop, model-free:
  1. baseline any track that has a `*-base` candidate but no incumbent
  2. screen every candidate never measured in this epoch (screen tier: runs while the owner uses the phone)
  3. confirm PROMISING candidates (idle-gated tier, the only one that can KEEP; KEEP promotes the incumbent,
     everything else is a REVERT, all appended to state/ledger.jsonl by bench_s25.py)
  4. refresh the A/A noise floor of each track once a day
  5. otherwise wait 15 min and rescan candidates/ (driver sessions add new ones)

Guards, on top of bench_s25.py's own gates (AP <= 42 C, battery <= 38 C before every block, focus-change abort):
  - watchdog thread polls HAL temperatures every 60 s; AP > 46 C or battery > 40 C kills the phone jobs at once
    and holds new work until AP < 40 C and battery < 36 C (battery-health guard; level is ignored at home)
  - kill switch: `touch mobile/state/STOP` on groot, or `adb shell touch /data/local/tmp/z0live-ar/STOP`,
    or `systemctl --user stop z0live-fulltime`. Any of them kills phone jobs and exits.
Everything runs over adb shell in /data/local/tmp (no app, UI, mic, speaker or focus change).
Receipts: state/fulltime.jsonl (supervisor actions + telemetry) and state/ledger.jsonl (keep/revert decisions).
"""
import datetime, fcntl, json, os, pathlib, subprocess, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state"
os.environ.setdefault("ZLV_MIN_BATT", "0")  # owner: battery level is irrelevant at home; temperature is the guard
sys.path.insert(0, str(ROOT))
import bench_s25 as b  # noqa: E402

TRIP_AP, TRIP_BAT, RESUME_AP, RESUME_BAT = 46.0, 40.0, 40.0, 36.0
AA_EVERY_S = 86400
RECEIPTS = STATE / "fulltime.jsonl"
hold = threading.Event()   # set = thermal trip active, do not start new work
stop = threading.Event()


def log(kind, **kw):
    rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, **kw}
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def stop_requested():
    if (STATE / "STOP").exists():
        return "groot STOP file"
    try:
        if b.sh(f"[ -e {b.PD}/STOP ] && echo yes", timeout=30).strip() == "yes":
            return "phone STOP file"
    except Exception:
        pass
    return None


def watchdog():
    while not stop.is_set():
        try:
            t = b.telemetry()
            ap, bat = t.get("AP"), (t.get("batt_dC") or 0) / 10
            tripped = (ap and ap > TRIP_AP) or bat > TRIP_BAT
            if tripped and not hold.is_set():
                hold.set()
                b.kill_phone_jobs()
                log("thermal_trip", AP=ap, battery_C=bat, action="killed phone jobs, holding")
            elif hold.is_set() and (ap or 0) < RESUME_AP and bat < RESUME_BAT:
                hold.clear()
                log("thermal_resume", AP=ap, battery_C=bat)
            log("telemetry", AP=ap, SKIN=t.get("SKIN"), battery_C=bat, level=t.get("level"), plugged=t.get("plugged"),
                owner_idle_s=t.get("owner_idle_s"), focus_display=t.get("focus_display"), hold=hold.is_set())
            why = stop_requested()
            if why:
                log("kill_switch", reason=why)
                stop.set()
                b.kill_phone_jobs()
                os._exit(0)  # the bench subprocess dies with our process group under systemd
        except Exception as e:  # adb hiccup: keep polling
            log("telemetry_error", error=f"{type(e).__name__}: {e}"[:200])
        stop.wait(60)


def bench(*args):
    while hold.is_set() and not stop.is_set():
        time.sleep(30)
    if stop.is_set():
        return None
    log("start", args=list(args))
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "bench_s25.py"), *args], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "S25_SERIAL": b.SER})
    tail = [l for l in (p.stdout + p.stderr).splitlines() if any(k in l for k in ("METRIC", "decision", "GATE", "ABORT", "rror"))]
    log("end", args=list(args), rc=p.returncode, wall_s=round(time.time() - t0), tail=tail[-6:])
    return p.returncode


def ledger():
    f = STATE / "ledger.jsonl"
    return [json.loads(l) for l in open(f)] if f.exists() else []


def next_item():
    L = [x for x in ledger() if x.get("epoch") == b.BENCH_EPOCH]
    inc = json.loads((STATE / "incumbents.json").read_text()) if (STATE / "incumbents.json").exists() else {}
    cands = {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "candidates").glob("*.json"))}
    measured = {x.get("id") for x in L}
    # done = finished cleanly, or failed 3 times; a GATE timeout (thermal/owner wait) is not an attempt
    ends = [r for r in map(json.loads, open(RECEIPTS)) if r.get("kind") == "end"] if RECEIPTS.exists() else []
    fails = {}
    for r in ends:
        if r["rc"] and not any("GATE" in l for l in r.get("tail", [])):
            fails[r["args"][-1]] = fails.get(r["args"][-1], 0) + 1
    tried = {r["args"][-1] for r in ends if r["rc"] == 0} | {k for k, n in fails.items() if n >= 3}
    for cid, c in cands.items():
        if c["track"] not in inc and cid.endswith("-base") and f"candidates/{cid}.json" not in tried:
            return ["baseline", "--tier", "screen", "--candidate", f"candidates/{cid}.json"]
    for cid, c in cands.items():
        if c["track"] in inc and cid not in measured and cid != inc[c["track"]]["cfg"]["id"] \
                and f"candidates/{cid}.json" not in tried:
            return ["run", "--tier", "screen", "--candidate", f"candidates/{cid}.json"]
    confirmed = {x["id"] for x in L if x.get("cmd") == "run"}
    for cid in dict.fromkeys((STATE / "confirm_queue.txt").read_text().split() if (STATE / "confirm_queue.txt").exists() else []):
        if cid not in confirmed and f"candidates/{cid}.json" not in tried:
            return ["run", "--candidate", f"candidates/{cid}.json"]
    for track in inc:
        last = max((x["at"] for x in L if x.get("cmd") == "aa" and x.get("lane") == track), default="")
        recent = [r["at"] for r in map(json.loads, open(RECEIPTS)) if r.get("kind") == "end"
                  and r.get("args") == ["aa", "--track", track]] if RECEIPTS.exists() else []
        last = max([last, *recent])
        if not last or time.time() - datetime.datetime.fromisoformat(last).timestamp() > AA_EVERY_S:
            return ["aa", "--track", track]
    return None


def main():
    STATE.mkdir(exist_ok=True)
    lock = open("/tmp/s25-fulltime.lock", "w")  # one supervisor; bench_s25.py itself serialises on s25-bench.lock
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    b.SER = b.serial()
    (STATE / "STOP").unlink(missing_ok=True) if os.environ.get("ZLV_CLEAR_STOP") else None
    log("supervisor_start", serial=b.SER, epoch=b.BENCH_EPOCH, pid=os.getpid(), min_batt=os.environ["ZLV_MIN_BATT"])
    threading.Thread(target=watchdog, daemon=True).start()
    while not stop.is_set():
        item = next_item()
        if item is None:
            log("idle", note="queue empty; rescanning candidates in 15 min")
            stop.wait(900)
            continue
        bench(*item)
        time.sleep(5)


if __name__ == "__main__":
    main()
