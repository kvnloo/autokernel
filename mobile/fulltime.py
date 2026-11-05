#!/usr/bin/env python3
"""Full-time supervisor for the z0live S25 autoresearch loop (owner ask N03, 2026-11-05).

Keeps the phone busy with AutoKernel's mobile loop, model-free:
  1. baseline any track that has a `*-base` candidate but no incumbent
  2. screen every candidate never measured in this epoch (screen tier: no idle gate in the bench; the supervisor still yields to the owner)
  3. confirm PROMISING candidates (idle-gated tier, the only one that can KEEP; KEEP promotes the incumbent,
     everything else is a REVERT, all appended to state/ledger.jsonl by bench_s25.py)
  4. refresh the A/A noise floor of each track once a day
  5. otherwise wait 15 min and rescan candidates/ (driver sessions add new ones)

Guards, on top of bench_s25.py's own gates (AP <= 42 C, battery <= 38 C before every block, focus-change abort):
  - watchdog thread polls HAL temperatures every 20 s; battery > 40 C, skin > 44 C, or AP > 60 C for 60 s kills the phone jobs at once and holds new work until AP < 42 C and battery < 37.5 C (battery-health
    guard; level is ignored at home). A run cut by a trip is re-queued.
  - owner yield: no work while the owner is in the foreground (awake, owner input < 120 s ago; agent pokes and
    virtual-display input do not count, see bench_s25.owner_activity) or on a call (telephony
    or VoIP audio mode); polled every 20 s, running phone jobs are killed and re-queued.
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

# Trip on battery heat (battery health) or skin heat (owner comfort) at once; the AP die spikes to 50-58 C for
# seconds under any heavy block, so AP trips only when it stays above 60 C for two polls in a row.
TRIP_AP, TRIP_SKIN, TRIP_BAT, RESUME_AP, RESUME_BAT = 60.0, 44.0, 40.0, 42.0, 37.5
AA_EVERY_S = 86400
RECEIPTS = STATE / "fulltime.jsonl"
hold = threading.Event()   # set = thermal trip or owner yield active, do not start new work
# Owner yield (Kevin, 2026-11-05): no phone work while he is in the foreground or on a call. Foreground = screen
# awake and user input within YIELD_IDLE_S; call = telephony offhook/ringing or audio mode in-call/communication.
YIELD_IDLE_S, POLL_S = 120, 20
why_hold = {"thermal": False, "owner": False}
stop = threading.Event()


def log(kind, **kw):
    rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, **kw}
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


current = {"proc": None}


def halt_work():
    """Kill the running bench (it would otherwise go on to its next block) and every phone job."""
    pr = current.get("proc")
    if pr and pr.poll() is None:
        pr.kill()
    b.kill_phone_jobs()


def stop_requested():
    if (STATE / "STOP").exists():
        return "groot STOP file"
    try:
        if b.sh(f"[ -e {b.PD}/STOP ] && echo yes", timeout=30).strip() == "yes":
            return "phone STOP file"
    except Exception:
        pass
    return None


def owner_busy(t):
    call = (t.get("call_state") or 0) != 0 or (t.get("audio_mode") or 0) in (1, 2, 3)
    fg = bool(t.get("awake")) and (t.get("owner_idle_s") is None or t["owner_idle_s"] < YIELD_IDLE_S)
    return ("call" if call else "foreground") if (call or fg) else None


def watchdog():
    n = 0
    while not stop.is_set():
        try:
            t = b.telemetry()
            ap, bat = t.get("AP"), (t.get("batt_dC") or 0) / 10
            hot_ap = bool(ap and ap > TRIP_AP)
            # AP must stay hot for 60 s (3 polls); battery/skin trip at once
            watchdog.hot_n = watchdog.hot_n + 1 if hot_ap else 0
            tripped = watchdog.hot_n >= 3 or bat > TRIP_BAT or (t.get("SKIN") or 0) > TRIP_SKIN
            busy = owner_busy(t)
            if tripped and not why_hold["thermal"]:
                why_hold["thermal"] = True
                halt_work()
                log("thermal_trip", AP=ap, battery_C=bat, SKIN=t.get("SKIN"), action="killed phone jobs, holding")
            elif why_hold["thermal"] and (ap or 0) < RESUME_AP and bat < RESUME_BAT:
                why_hold["thermal"] = False
                log("thermal_resume", AP=ap, battery_C=bat)
            if busy and not why_hold["owner"]:
                why_hold["owner"] = True
                halt_work()
                log("owner_yield", reason=busy, owner_idle_s=t.get("owner_idle_s"), activity_idle_s=t.get("activity_idle_s"),
                    agent_sources=t.get("agent_sources"), call_state=t.get("call_state"),
                    audio_mode=t.get("audio_mode"), action="killed phone jobs, holding")
            elif why_hold["owner"] and not busy:
                why_hold["owner"] = False
                log("owner_resume", owner_idle_s=t.get("owner_idle_s"))
            hold.set() if any(why_hold.values()) else hold.clear()
            watchdog.polled.set()
            n += 1
            if n % 3 == 0 or hold.is_set() != watchdog.last_hold:
                watchdog.last_hold = hold.is_set()
                log("telemetry", AP=ap, SKIN=t.get("SKIN"), battery_C=bat, level=t.get("level"), plugged=t.get("plugged"),
                    owner_idle_s=t.get("owner_idle_s"), activity_idle_s=t.get("activity_idle_s"), agent_sources=t.get("agent_sources"),
                    call_state=t.get("call_state"), focus_display=t.get("focus_display"),
                    hold=hold.is_set(), why=[k for k, v in why_hold.items() if v])
            why = stop_requested()
            if why:
                log("kill_switch", reason=why)
                stop.set()
                b.kill_phone_jobs()
                os._exit(0)  # the bench subprocess dies with our process group under systemd
        except Exception as e:  # adb hiccup: keep polling
            log("telemetry_error", error=f"{type(e).__name__}: {e}"[:200])
        stop.wait(POLL_S)


watchdog.hot_n = 0
watchdog.polled = threading.Event()
watchdog.last_hold = None


def live_serial():
    """The S25's current adb serial. Wireless debugging changes port on reconnect, so a startup pin goes stale."""
    def state(ser):
        r = subprocess.run(["adb", "-s", ser, "get-state"], capture_output=True, text=True, timeout=15)
        return r.stdout.strip()
    if b.SER and state(b.SER) == "device":
        return b.SER
    out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=15).stdout
    hits = sorted((l.split()[0] for l in out.splitlines() if "model:SM_S938U" in l and " device " in l),
                  key=lambda s: ("_adb-tls" in s, s))
    return hits[0] if hits else None


def bench(*args):
    while hold.is_set() and not stop.is_set():
        time.sleep(30)
    if stop.is_set():
        return None
    log("start", args=list(args))
    t0 = time.time()
    p = subprocess.Popen([sys.executable, str(ROOT / "bench_s25.py"), *args], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, env={**os.environ, "S25_SERIAL": b.SER})
    current["proc"] = p
    out, _ = p.communicate()
    current["proc"] = None
    p.stdout, p.stderr = out or "", ""
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
    # a candidate whose build variant is not built yet waits; queueing it would burn its 3 attempts on SystemExit
    cands = {k: c for k, c in cands.items() if (b.VARIANTS / c.get("variant", "base") / "audiocpp_cli").exists()}
    measured = {x.get("id") for x in L}
    # done = finished cleanly, or failed 3 times; a GATE timeout (thermal/owner wait) is not an attempt
    ends = [r for r in map(json.loads, open(RECEIPTS)) if r.get("kind") == "end"] if RECEIPTS.exists() else []
    fails = {}
    for r in ends:
        # adb transport loss (phone asleep/offline, port change) says nothing about the candidate
        if r["rc"] and not any("GATE" in l or "adb shell failed" in l or "not attached" in l for l in r.get("tail", [])):
            fails[r["args"][-1]] = fails.get(r["args"][-1], 0) + 1
    # a run cut by a thermal trip is not an attempt either: it is re-queued (its partial ledger row stays as a receipt)
    trips = [r["at"] for r in map(json.loads, open(RECEIPTS)) if r.get("kind") in ("thermal_trip", "owner_yield")] if RECEIPTS.exists() else []
    def cut(r):
        t1 = datetime.datetime.fromisoformat(r["at"]); t0 = t1 - datetime.timedelta(seconds=r.get("wall_s", 0))
        return any(t0 <= datetime.datetime.fromisoformat(x) <= t1 for x in trips)
    tried = {r["args"][-1] for r in ends if r["rc"] == 0 and not cut(r)} | {k for k, n in fails.items() if n >= 3}
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
    b.SER = live_serial() or os.environ.get("S25_SERIAL")
    (STATE / "STOP").unlink(missing_ok=True) if os.environ.get("ZLV_CLEAR_STOP") else None
    log("supervisor_start", serial=b.SER, epoch=b.BENCH_EPOCH, pid=os.getpid(), min_batt=os.environ["ZLV_MIN_BATT"])
    threading.Thread(target=watchdog, daemon=True).start()
    watchdog.polled.wait(120)  # never start work before the first owner/thermal poll
    while not stop.is_set():
        item = next_item()
        if item is None:
            log("idle", note="queue empty; rescanning candidates in 15 min")
            stop.wait(900)
            continue
        ser = live_serial()
        if ser is None:  # unreachable phone is a wait, not a failed attempt (it burned whole queues at 02:45)
            log("phone_unreachable", last_serial=b.SER)
            stop.wait(120)
            continue
        if ser != b.SER:
            log("serial_changed", old=b.SER, new=ser)
            b.SER = ser
        bench(*item)
        time.sleep(5)


if __name__ == "__main__":
    main()
