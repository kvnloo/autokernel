#!/usr/bin/env python3
"""Fixed on-device benchmark for z0live autoresearch on the Galaxy S25 Ultra.

This file is the referee. The research loop edits candidate configs, build
variants and kernel patches, never this file. Changing it (or evalset/) starts a
new baseline epoch: bump BENCH_EPOCH and re-run `baseline` for every lane.

Commands
  baseline --candidate C   measure C alone; becomes the lane incumbent if none exists
  run      --candidate C   interleaved A/B against the lane incumbent, then KEEP/REVERT
  aa       --track T       incumbent vs itself: measures the noise floor for track T (e.g. tts/gradium_phonon)
  status                   incumbents, noise floors, stall counters

Phone rules (owner): adb shell CLI only. No app launch, no UI, no mic/speaker, no
focus change. Runs only while the owner has been idle >= OWNER_IDLE_S, battery >= 30%
(or charging), and AP/battery temperatures are under the caps. Focus is snapshotted
before and after every block, and any change aborts the run.
"""
import argparse, datetime, hashlib, json, os, pathlib, random, re, shlex, statistics, subprocess, sys, time, wave

BENCH_EPOCH = 4  # epoch 4: thermal gate reads live HAL temps, not the cached list
ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state"
ZLV = pathlib.Path(os.environ.get("ZLV_ROOT", "/mnt/zer0models/models/zlv-tts"))
MODELS = ZLV / "acpp-models"
VARIANTS = ZLV / "phone-bundle" / "variants"
PD = "/data/local/tmp/z0live-ar"
OWNER_IDLE_S = int(os.environ.get("ZLV_OWNER_IDLE_S", "90"))
# Epoch 2 ran while the owner used the phone: an A/A test (same config both sides) differed by 1.38-1.73x,
# so decisions need idle windows. Epoch 3 splits tiers: `--tier screen` runs any time (no idle gate) and only
# nominates big wins (PROMISING); `confirm` (default) is idle-gated and is the only tier that can KEEP.
MAX_AP_C, MAX_BAT_C = 42.0, 38.0
MIN_BATT = int(os.environ.get("ZLV_MIN_BATT", "30"))  # gate only; owner: level is irrelevant at home (fulltime.py sets 0)
EVALSET = json.loads((ROOT / "evalset" / "v1.json").read_text())
# Realtime-stage lanes (asr/vad/turn/llm) use their own frozen set; v1 (tts/s2s) is untouched.
EVALSET.update({k: v for k, v in json.loads((ROOT / "evalset" / "stages-v1.json").read_text()).items() if k in ("asr", "vad", "turn", "llm")})
INPUT_LANES = ("asr", "vad", "turn")
LLM_DIR = "/data/local/tmp/z0live-ar/llm"  # pre-staged llama.cpp (CPU/OpenCL/Hexagon) + GGUFs; not managed by sync()


# ---------------------------------------------------------------- adb plumbing
def serial():
    if os.environ.get("S25_SERIAL"):
        return os.environ["S25_SERIAL"]
    out = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True).stdout
    hits = [l.split()[0] for l in out.splitlines() if "model:SM_S938U" in l and " device " in l]
    if not hits:
        sys.exit("S25 not attached over adb")
    hits.sort(key=lambda s: ("_adb-tls" in s, s))  # prefer the stable tailnet ip:port
    return hits[0]


SER = None


def sh(cmd, timeout=600, check=False):
    p = subprocess.run(["adb", "-s", SER, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"adb shell failed ({p.returncode}): {cmd[:200]}\n{p.stderr[-800:]}")
    return p.stdout


def adb(*args, timeout=1800):
    return subprocess.run(["adb", "-s", SER, *args], capture_output=True, text=True, timeout=timeout)


# Owner activity (2026-11-05 false yield): lastUserActivityTime is per power group, and agents poke it too:
# agent_dark.py's `PanelPower hold` calls IPowerManager.userActivity every 2 s, and input injected on a virtual
# display in power group 0 counts as well. While such an agent source is live, owner activity is only what an
# agent cannot fake: a physical-device touch DOWN on display 0, or a wake that no agent issues. If the phone does
# not expose the display-0 touch record, the raw signal is kept (fail safe: yield). Display 0 is never relaxed.
AGENT_WAKE_REASONS = (2, 6)  # WAKE_REASON_APPLICATION, WAKE_REASON_WAKE_KEY (`input keyevent WAKEUP`)


def owner_activity(out):
    ago = lambda pat: (lambda m: int(m[1]) / 1000 if m else None)(re.search(pat, out))
    act = ago(r"lastUserActivityTime=\d+ \((\d+) ms ago\)")
    wake = ago(r"mLastWakeTime=\d+ \((\d+) ms ago\)")
    m = re.search(r"mLastWakeUpReason: (\d+)", out)
    reason = int(m[1]) if m else None
    m = re.search(r"lastUserActivityTime=(\d+) \((\d+) ms ago\)", out) or re.search(r"mLastWakeTime=(\d+) \((\d+) ms ago\)", out)
    now_ms = int(m[1]) + int(m[2]) if m else None  # uptime clock, same base as input event times
    m = re.search(r"Filter verifier on display 0:\s*\n\s*Down times by device: \{([^}]*)\}", out)
    downs = [int(ns) for dev, ns in re.findall(r"DeviceId\((-?\d+)\): (\d+)", m[1]) if int(dev) >= 0] if m else None
    touch = (now_ms - max(downs) / 1e6) / 1000 if downs and now_ms else None
    m = re.search(r"agent_panel_hold=(\d+)", out)
    sources = (["panel_hold"] if m and int(m[1]) else []) + \
        [f"vd:{d}" for d in sorted(set(map(int, re.findall(r"Viewport VIRTUAL: displayId=(\d+)", out))))]
    owner = act
    if sources and downs is not None and now_ms:
        seen = [x for x in (touch, None if reason in AGENT_WAKE_REASONS else wake) if x is not None]
        owner = min(seen) if seen else max(x for x in (wake, act, 0) if x is not None)
    return {"owner_idle_s": owner, "activity_idle_s": act, "touch0_idle_s": touch, "wake_idle_s": wake,
            "wake_reason": reason, "agent_sources": sources}


def telemetry():
    out = sh("dumpsys battery | grep -E '^  (level|temperature|AC powered|USB powered|Wireless powered|current now|Charge counter|voltage):';"
             "echo ==; dumpsys thermalservice | sed -n '/^Current temperatures from HAL/,/^[A-Z]/p' | grep -E 'mName=(AP|SKIN|BAT),' | head -3; echo ==;"
             "dumpsys power | grep -E '^ *lastUserActivityTime=|mWakefulness=|^  mLastWakeTime=|^  mLastWakeUpReason:'; echo ==;"
             "dumpsys window | grep -m1 mTopFocusedDisplayId; dumpsys window | grep -m1 mCurrentFocus;"
             "dumpsys telephony.registry 2>/dev/null | grep -m1 mCallState=; dumpsys audio 2>/dev/null | grep -m1 mAudioModeOwner;"
             "echo ==; dumpsys input | sed '/at time of last ANR/q' | grep -E 'Viewport VIRTUAL|Filter verifier on display|Down times by device';"
             "echo agent_panel_hold=$(ps -A -o ARGS | grep -c '[P]anelPower hold')", timeout=60)
    t = {}
    for k, key in (("level", "level"), ("temperature", "batt_dC"), ("current now", "current_uA"), ("Charge counter", "charge_uAh"), ("voltage", "voltage_mV")):
        m = re.search(rf"^  {k}: (-?\d+)", out, re.M)
        t[key] = int(m[1]) if m else None
    t["plugged"] = bool(re.search(r"(AC|USB|Wireless) powered: true", out))
    for name in ("AP", "SKIN", "BAT"):
        m = re.search(rf"mValue=([\d.]+), mType=\d+, mName={name},", out)
        t[name] = float(m[1]) if m else None
    t.update(owner_activity(out))
    t["awake"] = "mWakefulness=Awake" in out
    m = re.search(r"mTopFocusedDisplayId=(\d+)", out)
    t["focus_display"] = int(m[1]) if m else None
    m = re.search(r"mCurrentFocus=(.*)", out)
    t["focus"] = m[1].strip() if m else None
    m = re.search(r"mCallState=(\d)", out)
    t["call_state"] = int(m[1]) if m else None  # 0 idle, 1 ringing, 2 offhook
    m = re.search(r"mAudioModeOwner:.*?mMode=(\d+)", out)
    t["audio_mode"] = int(m[1]) if m else None  # 1 ringtone, 2 in call, 3 in communication (VoIP)
    t["at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return t


def wait_for_gates(max_wait_s=int(os.environ.get("ZLV_MAX_WAIT_S", "1800")), log=print):
    start = time.time()
    while True:
        t = telemetry()
        why = []
        if not t["plugged"] and (t["level"] or 0) < MIN_BATT:
            why.append(f"battery {t['level']}% < {MIN_BATT}% and not charging")
        if t["AP"] and t["AP"] > MAX_AP_C:
            why.append(f"AP {t['AP']}C > {MAX_AP_C}")
        if t["batt_dC"] and t["batt_dC"] / 10 > MAX_BAT_C:
            why.append(f"battery {t['batt_dC']/10}C > {MAX_BAT_C}")
        if OWNER_IDLE_S and (t["owner_idle_s"] is None or t["owner_idle_s"] < OWNER_IDLE_S):
            why.append(f"owner active {t['owner_idle_s']}s ago")
        if not why:
            return t
        if time.time() - start > max_wait_s:
            raise SystemExit("GATE timeout: " + "; ".join(why))
        log("waiting: " + "; ".join(why))
        time.sleep(60 if any("%" in w for w in why) else 30)


# ---------------------------------------------------------------- assets
def sig(path):
    p = pathlib.Path(path)
    files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
    h = hashlib.sha256()
    for f in files:
        st = f.stat()
        h.update(f"{f.relative_to(p.parent)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16], sum(f.stat().st_size for f in files)


def binary(cfg):
    v = cfg.get("variant", "base")
    p = VARIANTS / v / "audiocpp_cli"
    if not p.exists():
        raise SystemExit(f"variant {v!r} missing: {p}")
    return p


def model_paths(cfg):
    paths = [cfg["model"].split("/")[0]] + list(cfg.get("assets", []))
    if cfg.get("lane") == "llm":
        return []  # lives in LLM_DIR, staged once (2026-11-02)
    if cfg.get("lane") in ("s2s", *INPUT_LANES):
        paths.append("ref")
    if cfg.get("spec_override"):
        paths.append("specs")
    return sorted(set(paths))


def kill_phone_jobs():
    sh("for p in $(ps -A -o PID,ARGS | grep -E 'block\\.s[h]|z0live-ar/bi[n]|z0live-ar/llm/bi[n]' | awk '{print $1}'); do kill $p; done", timeout=30)


def sync(cfgs):
    sh(f"mkdir -p {PD}/bin {PD}/m {PD}/out", check=True)
    have = dict(l.split(" ", 1) for l in sh(f"cat {PD}/manifest 2>/dev/null").splitlines() if " " in l)
    want = {}
    for c in cfgs:
        want[f"bin/{c.get('variant','base')}"] = binary(c)
        for m in model_paths(c):
            want[f"m/{m}"] = MODELS / m
    need = sum(sig(p)[1] for k, p in want.items() if have.get(k) != sig(p)[0])
    free_kb = int(sh("df -k /data | tail -1").split()[3])
    if need and free_kb * 1024 < need + 1.0e9:  # evict anything not wanted by this block
        for k in list(have):
            if k not in want:
                sh(f"rm -rf {PD}/{k}")
                have.pop(k)
    for k, p in want.items():
        s, size = sig(p)
        if have.get(k) == s:
            continue
        dest = f"{PD}/{k}"
        sh(f"rm -rf {dest}; mkdir -p {dest if p.is_dir() else os.path.dirname(dest)}")
        if p.is_dir():
            r = adb("push", "-q", f"{p}/.", dest)
        else:
            r = adb("push", "-q", str(p), f"{dest}/audiocpp_cli")
            sh(f"chmod 755 {dest}/audiocpp_cli")
        if r.returncode:
            raise RuntimeError(f"push {k} failed: {r.stderr[-400:]}")
        have[k] = s
        sh(f"printf '%s\\n' {' '.join(shlex.quote(f'{a} {b}') for a, b in have.items())} > {PD}/manifest")


# ---------------------------------------------------------------- phone script
def cmdline(cfg, item, out_wav):
    b = f"{PD}/bin/{cfg.get('variant','base')}/audiocpp_cli"
    a = [b, "--family", cfg["family"], "--model", f"{PD}/m/{cfg['model']}", "--backend", cfg.get("backend", "cpu"),
         "--threads", str(cfg.get("threads", 4)), "--mode", cfg.get("mode", "streaming"), "--out", out_wav, "--log"]
    if cfg["lane"] == "tts":
        a[1:1] = ["--task", "tts"]
        a += ["--text", item["text"]]
    elif cfg["lane"] == "s2s":
        a[1:1] = ["--task", "s2s"]
        a += ["--audio", f"{PD}/m/{item['audio']}", "--text-out", out_wav[:-4] + ".txt"]
        if cfg.get("spec_override"):
            a += ["--model-spec-override", f"{PD}/m/{cfg['spec_override']}"]
        if is_ttfa(cfg):  # ttfa tracks only: wall tracks keep their exact epoch-4 command
            a += ["--load-progress", "1"]
    elif cfg["lane"] in INPUT_LANES:
        a = [b, "--task", cfg["lane"]] + a[1:]
        i = a.index("--out"); del a[i:i + 2]
        a += ["--audio", f"{PD}/m/{item['audio']}", "--load-progress", "1"]
        a += {"asr": ["--text-out", out_wav], "vad": ["--segments-out", out_wav], "turn": []}[cfg["lane"]]
    elif cfg["lane"] == "llm":
        dev = cfg.get("device")  # None = CPU, "GPUOpenCL", "HTP0"
        a = [f"{LLM_DIR}/bin/llama-bench", "-m", f"{LLM_DIR}/m/{cfg['model']}", "-p", str(item["n_prompt"]), "-n", str(item["n_gen"]),
             "-r", str(item["reps"]), "-t", str(cfg.get("threads", 4)), "-o", "jsonl"] + (["-dev", dev, "-ngl", "99"] if dev else ["-dev", "none", "-ngl", "0"])
        a += [str(x) for x in cfg.get("extra_args", [])]
        env = {"LD_LIBRARY_PATH": f"{LLM_DIR}/lib", "ADSP_LIBRARY_PATH": f"{LLM_DIR}/lib", **cfg.get("env", {})}
        pre = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in env.items())
        mask = cfg.get("cpu_mask")
        return (f"{pre} timeout {int(cfg.get('timeout_s', 300))} " + (f"taskset {mask} " if mask else "")
                + " ".join(shlex.quote(x) for x in a) + f" > {out_wav} 2>/dev/null; cat {out_wav}")
    else:
        raise SystemExit(f"lane {cfg['lane']} not wired in bench epoch {BENCH_EPOCH}")
    a += [x.replace("{M}", f"{PD}/m") for x in cfg.get("extra_args", [])]
    pre = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in cfg.get("env", {}).items())
    mask = cfg.get("cpu_mask")
    # per-invocation wall cap: a hung backend (e.g. OpenCL init) must fail fast, not stall the queue for 30 min
    cap = f"timeout {int(cfg.get('timeout_s', 120))} "
    return (pre + " " if pre else "") + cap + (f"taskset {mask} " if mask else "") + " ".join(shlex.quote(x) for x in a)


def is_ttfa(cfg):
    """s2s time-to-first-audio track: LOADED = `runtime.load.phase loaded` (--load-progress 1), FIRST = the first
    `stream_audio` line (variants/patches/streammark.patch). Its own track, since objectives never change inside one."""
    return cfg.get("lane") == "s2s" and cfg.get("objective", "").startswith("ttfa")


def block_jobs(c, items, r):
    """One block = every eval item once. `warm: true` runs a discarded first item so flash pages and CPU clocks are
    warm, the way a resident phone app would be; its tag matches no eval item, so no record reads it."""
    jobs = []
    if c.get("warm"):
        wav = f"{PD}/out/{c['_slot']}-warm-r{r}.wav"
        jobs.append((f"{c['_slot']}-warm-r{r}", cmdline(c, items[0], wav), wav))
    for it in items:
        tag = f"{c['_slot']}-{it['id']}-r{r}"
        wav = f"{PD}/out/{tag}.wav"
        jobs.append((tag, cmdline(c, it, wav), wav))
    return jobs


def phone_block(jobs):
    """jobs: list of (tag, cmd, wav). Emits raw ns timestamps; arithmetic happens on groot (mksh is 32-bit)."""
    lines = ["N() { date +%s%N; }"]
    for tag, cmd, wav in jobs:
        lines.append(f"echo \"EV {tag} START $(N)\"")
        lines.append(f"{cmd} 2>&1 | while IFS= read -r l; do case \"$l\" in "
                     f"*text.prepare_ms*|*prompt_ms*|*voice.prepare_ms*|*\"runtime.load.phase loaded\"*) echo \"EV {tag} LOADED $(N)\";; "
                     f"audio_output_id=*|stream_audio\\ *) echo \"EV {tag} FIRST $(N)\";; "
                     f"*session.wall_ms*) echo \"EV {tag} WALL ${{l##* }}\";; "
                     f"*avg_ts*) echo \"EV {tag} JSON $l\";; "
                     f"*rror*|*failed*|*Abort*) echo \"EV {tag} ERR $l\";; esac; done")
        lines.append(f"echo \"EV {tag} END $(N) $(wc -c < {wav} 2>/dev/null || echo 0)\"")
    return "\n".join(lines) + "\n"


def parse_events(text):
    runs = {}
    for l in text.splitlines():
        p = l.split(" ", 3)
        if len(p) < 4 or p[0] != "EV":
            continue
        r = runs.setdefault(p[1], {"errs": []})
        if p[2] in ("START", "LOADED", "FIRST") and p[2].lower() not in r:
            r[p[2].lower()] = int(p[3])
        elif p[2] == "LOADED":
            r["loaded"] = int(p[3])  # last load-phase marker wins
        elif p[2] == "WALL":
            r["wall_ms"] = float(p[3])
        elif p[2] == "END":
            a, b = p[3].split()
            r["end"], r["wav_bytes"] = int(a), int(b)
        elif p[2] == "JSON":
            r.setdefault("json", []).append(p[3])
        elif p[2] == "ERR":
            r["errs"].append(p[3][:200])
    return runs


def wav_seconds(path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return None


# ---------------------------------------------------------------- measuring
def measure(cfgs, rounds, run_dir, log=print):
    """Interleaves configs ABBA per round; returns per-config list of run records."""
    items = EVALSET[cfgs[0]["lane"]]
    recs = {c["_slot"]: [] for c in cfgs}
    tele = []
    kill_phone_jobs()
    sync(cfgs)
    sh(f"rm -rf {PD}/out && mkdir -p {PD}/out")  # stale WAVs from other tracks would be pulled into this run
    for r in range(rounds):
        order = cfgs if r % 2 == 0 else list(reversed(cfgs))
        for c in order:
            t0 = wait_for_gates(log=log)
            jobs = block_jobs(c, items, r)
            sh(f"rm -f {PD}/out/{c['_slot']}-*-r{r}.*")
            script = phone_block(jobs)
            (run_dir / f"{c['_slot']}-r{r}.sh").write_text(script)
            adb("push", "-q", str(run_dir / f"{c['_slot']}-r{r}.sh"), f"{PD}/block.sh")
            try:
                out = sh(f"cd {PD} && sh block.sh", timeout=1800)
            except subprocess.TimeoutExpired:
                kill_phone_jobs()
                out = ""
            (run_dir / f"{c['_slot']}-r{r}.events").write_text(out)
            t1 = telemetry()
            tele.append({"slot": c["_slot"], "round": r, "before": t0, "after": t1})
            if (t0["focus_display"], t0["focus"]) != (t1["focus_display"], t1["focus"]) and t1["owner_idle_s"] and t1["owner_idle_s"] > 5:
                raise SystemExit(f"ABORT focus changed without owner input: {t0['focus']} -> {t1['focus']}")
            contaminated = t1["owner_idle_s"] is not None and t1["owner_idle_s"] < (time.time() - time.mktime(time.strptime(t0["at"], "%Y-%m-%dT%H:%M:%S")))
            ev = parse_events(out)
            adb("pull", "-q", f"{PD}/out/.", str(run_dir / "wav"))
            for it in items:
                tag = f"{c['_slot']}-{it['id']}-r{r}"
                e = ev.get(tag, {"errs": ["no events"]})
                lane = c["lane"]
                secs = it.get("duration_s") if lane in INPUT_LANES else (1.0 if lane == "llm" else wav_seconds(run_dir / "wav" / f"{tag}.wav"))
                rec = {"tag": tag, "item": it["id"], "round": r, "errs": e["errs"], "audio_s": secs, "contaminated": contaminated}
                if "first" in e:
                    rec["start_to_first_ms"] = (e["first"] - e["start"]) / 1e6
                    if "loaded" in e:
                        rec["load_to_first_ms"] = (e["first"] - e["loaded"]) / 1e6
                if "end" in e:
                    rec["proc_ms"] = (e["end"] - e["start"]) / 1e6
                if "wall_ms" in e and secs and lane not in INPUT_LANES + ("llm",):
                    rec["rtf"] = e["wall_ms"] / 1000 / secs
                if lane in INPUT_LANES and "proc_ms" in rec:
                    rec["rtf"] = rec["proc_ms"] / 1000 / secs  # process RTF incl. model load (one process per clip)
                if lane == "llm":
                    rows = [json.loads(x) for x in e.get("json", []) if x.startswith("{")]
                    tg = [x for x in rows if x.get("n_gen")]; pp = [x for x in rows if x.get("n_prompt") and not x.get("n_gen")]
                    if tg:
                        rec["ms_per_tok"] = 1000 / tg[0]["avg_ts"]; rec["tg_tps"] = tg[0]["avg_ts"]
                    if pp:
                        rec["pp_tps"] = pp[0]["avg_ts"]; rec["pp_ms_per_tok"] = 1000 / pp[0]["avg_ts"]
                    if not tg:
                        rec["errs"] = rec["errs"] + ["no llama-bench tg row"]
                recs[c["_slot"]].append(rec)
            ch = (t0["charge_uAh"] or 0) - (t1["charge_uAh"] or 0)
            log(f"  {c['_slot']} r{r}: {sum(1 for x in recs[c['_slot']] if x['round']==r and not x['errs'])}/{len(items)} ok, "
                f"AP {t0['AP']}->{t1['AP']}C, charge -{ch} uAh{' CONTAMINATED' if contaminated else ''}")
    return recs, tele


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


def summarize(rs, objective):
    ok = [r for r in rs if not r["errs"] and r.get("audio_s") and not (OWNER_IDLE_S and r["contaminated"])]
    s_busy = sum(1 for r in rs if r["contaminated"])
    s = {"n": len(rs), "ok": len(ok), "owner_active_runs": s_busy}
    for k in ("load_to_first_ms", "start_to_first_ms", "rtf", "proc_ms", "ms_per_tok", "tg_tps", "pp_tps", "pp_ms_per_tok"):
        v = [r[k] for r in ok if r.get(k) is not None]
        if v:
            s[k] = {"p50": round(statistics.median(v), 2), "p90": round(q(v, .9), 2), "n": len(v)}
    s["objective"] = (s.get(OBJ_KEY[objective]) or {}).get("p50")
    return s


OBJ_KEY = {"ttfa_warm": "load_to_first_ms", "ttfa_cold": "start_to_first_ms", "rtf": "rtf", "wall": "proc_ms",
           "ms_per_tok": "ms_per_tok", "prefill_ms_per_tok": "pp_ms_per_tok"}


def paired_ratio_ci(inc, cand, key, n=4000, seed=7):
    """Bootstrap CI of median(cand)/median(inc), resampling within each eval item (paired design)."""
    by = {}
    for side, rs in (("i", inc), ("c", cand)):
        for r in rs:
            if r.get(key) is not None and not r["errs"] and not (OWNER_IDLE_S and r["contaminated"]):
                by.setdefault(r["item"], {"i": [], "c": []})[side].append(r[key])
    by = {k: v for k, v in by.items() if v["i"] and v["c"]}
    if not by:
        return None
    rnd = random.Random(seed)
    ratios = []
    for _ in range(n):
        I, C = [], []
        for v in by.values():
            I += [rnd.choice(v["i"]) for _ in v["i"]]
            C += [rnd.choice(v["c"]) for _ in v["c"]]
        ratios.append(statistics.median(C) / statistics.median(I))
    ratios.sort()
    return {"lo": round(ratios[int(.025 * n)], 4), "mid": round(statistics.median(ratios), 4), "hi": round(ratios[int(.975 * n)], 4)}


def wer(ref, hyp):
    r, h = re.findall(r"[a-z0-9]+", ref.lower().replace("'", "")), re.findall(r"[a-z0-9]+", hyp.lower().replace("'", ""))
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def quality(run_dir, slots, lane):
    if lane == "asr":  # text-out files pulled into wav/; WER vs the frozen transcripts
        ref = {i["id"]: i["text"] for i in EVALSET["asr"]}
        out = {"slots": {}}
        for s in slots:
            ws = [wer(ref[f.name.split("-")[1]], f.read_text(errors="ignore")) for f in sorted((run_dir / "wav").glob(f"{s}-asr*-r*.wav"))]
            out["slots"][s] = {"wer": round(statistics.mean(ws), 4), "n": len(ws)} if ws else {"wer": None, "n": 0}
        return out
    if lane in ("vad", "turn", "llm"):
        return {"status": "latency-only lane (no quality scorer wired yet)"}
    qpy = ROOT / "quality.py"
    if not qpy.exists():
        return {"status": "unavailable"}
    py = os.environ.get("ZLV_QUALITY_PY", str(ZLV / ".venv" / "bin" / "python"))
    out = run_dir / "quality.json"
    cmd = ["env", "-u", "PYTHONPATH", "-u", "PYTHONHOME", py, str(qpy), "--wav-dir", str(run_dir / "wav"),
           "--evalset", str(ROOT / "evalset" / "v1.json"), "--lane", lane, "--slots", ",".join(slots), "--out", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if p.returncode or not out.exists():
        return {"status": "error", "stderr": p.stderr[-1500:]}
    return json.loads(out.read_text())


# ---------------------------------------------------------------- state
def load(name, default):
    p = STATE / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, value):
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(value, indent=2) + "\n")


def ledger(entry):
    STATE.mkdir(exist_ok=True)
    entry = {"epoch": BENCH_EPOCH, "at": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
    with open(STATE / "ledger.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    s = entry.get("summary", {})
    with open(STATE / "results.tsv", "a") as f:
        f.write("\t".join(str(x) for x in (entry["at"], entry["cmd"], entry.get("lane"), entry.get("id"), entry.get("decision"),
                                             (s.get("cand") or s.get("only") or {}).get("objective"), (s.get("inc") or {}).get("objective"),
                                             (entry.get("ci") or {}).get("mid"), entry.get("hypothesis", "")[:120])) + "\n")


def cfg_load(path):
    c = json.loads(pathlib.Path(path).read_text())
    for k in ("id", "lane", "family", "model"):
        if k not in c:
            raise SystemExit(f"candidate missing {k}")
    c.setdefault("objective", "ttfa_warm" if c["lane"] == "tts" else "wall")
    c.setdefault("track", f"{c['lane']}/{c['family']}")
    return c


def new_run_dir(tag):
    d = STATE / "runs" / f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{tag}"
    (d / "wav").mkdir(parents=True)
    return d


def gates(summary, qual, lane, inc_qual=None, slot="cand"):
    fails = []
    if summary["ok"] < 0.9 * summary["n"]:
        fails.append(f"only {summary['ok']}/{summary['n']} runs ok")
    if lane == "tts" and (summary.get("rtf") or {}).get("p90", 9) > 0.85:
        fails.append(f"rtf p90 {summary.get('rtf',{}).get('p90')} > 0.85 (not real time with margin)")
    qs = (qual or {}).get("slots", {}).get(slot)
    if qs and inc_qual:
        if qs.get("wer") is not None and inc_qual.get("wer") is not None and qs["wer"] > inc_qual["wer"] + 0.02:
            fails.append(f"WER {qs['wer']:.3f} > incumbent {inc_qual['wer']:.3f} + 0.02")
        if qs.get("mos") is not None and inc_qual.get("mos") is not None and qs["mos"] < inc_qual["mos"] - 0.10:
            fails.append(f"MOS {qs['mos']:.2f} < incumbent {inc_qual['mos']:.2f} - 0.10")
    if lane == "s2s" and qs and qs.get("task_acc") is not None and qs["task_acc"] < 1.0:
        fails.append(f"s2s task accuracy {qs['task_acc']}")
    return fails


# ---------------------------------------------------------------- commands
def cmd_baseline(a):
    c = cfg_load(a.candidate); c["_slot"] = "only"
    d = new_run_dir("base-" + c["id"])
    recs, tele = measure([c], a.rounds, d)
    s = summarize(recs["only"], c["objective"])
    qual = quality(d, ["only"], c["lane"])
    inc = load("incumbents.json", {})
    fails = gates(s, qual, c["lane"], slot="only")
    decision = "BASELINE_FAIL" if fails else ("BASELINE_SET" if c["track"] not in inc or a.force else "BASELINE_MEASURED")
    if decision == "BASELINE_SET":
        inc[c["track"]] = {"cfg": {k: v for k, v in c.items() if k != "_slot"}, "summary": s,
                          "quality": (qual.get("slots") or {}).get("only"), "run": str(d)}
        save("incumbents.json", inc)
    ledger({"cmd": "baseline", "lane": c["track"], "id": c["id"], "decision": decision, "fails": fails, "hypothesis": c.get("hypothesis", ""),
            "cfg": {k: v for k, v in c.items() if k != "_slot"}, "summary": {"only": s}, "quality": qual, "run": str(d), "telemetry": tele})
    print(json.dumps({"decision": decision, "fails": fails, "summary": s, "quality": qual.get("slots", qual)}, indent=1))
    print(f"METRIC {c['track']}.{c['objective']} {s['objective']}")


def cmd_run(a):
    c = cfg_load(a.candidate)
    inc = load("incumbents.json", {})
    if c["track"] not in inc:
        raise SystemExit(f"no incumbent for track {c['track']}: run baseline first")
    i = dict(inc[c["track"]]["cfg"])
    if i.get("objective") != c["objective"]:
        raise SystemExit("objective differs from incumbent: change objectives only via a new epoch")
    i["_slot"], c["_slot"] = "inc", "cand"
    d = new_run_dir(c["id"])
    recs, tele = measure([i, c], a.rounds, d)
    key = OBJ_KEY[c["objective"]]
    si, sc = summarize(recs["inc"], c["objective"]), summarize(recs["cand"], c["objective"])
    ci = paired_ratio_ci(recs["inc"], recs["cand"], key)
    qual = quality(d, ["inc", "cand"], c["lane"])
    iq = (qual.get("slots") or {}).get("inc") or inc[c["track"]].get("quality")
    fails = gates(sc, qual, c["lane"], inc_qual=iq, slot="cand")
    if a.tier == "screen":
        bad = [f for f in fails if not f.startswith("rtf")]
        decision = "PROMISING" if ci and not bad and ci["mid"] <= 0.85 else "SCREEN_NO"
        if decision == "PROMISING":
            with open(STATE / "confirm_queue.txt", "a") as f:
                f.write(c["id"] + "\n")
        ledger({"cmd": "screen", "lane": c["track"], "id": c["id"], "decision": decision, "fails": fails, "ci": ci,
                "hypothesis": c.get("hypothesis", ""), "cfg": {k: v for k, v in c.items() if k != "_slot"}, "incumbent_id": i["id"],
                "summary": {"inc": si, "cand": sc}, "quality": qual, "run": str(d), "telemetry": tele})
        print(json.dumps({"decision": decision, "fails": fails, "ci_ratio": ci, "inc": si, "cand": sc}, indent=1))
        print(f"METRIC {c['track']}.{c['objective']} screen inc={si['objective']} cand={sc['objective']} ratio={ci and ci['mid']}")
        return
    noise = load("noise.json", {}).get(c["track"], {}).get("ratio_halfwidth", 0.05)
    need = max(0.03, noise)
    if ci is None:
        decision = "REVERT_NO_DATA"
    elif fails:
        decision = "REVERT_GATE"
    elif ci["hi"] < 1 - need:
        decision = "KEEP"
    elif ci["lo"] > 1 + need:
        decision = "REVERT_SLOWER"
    else:
        decision = "REVERT_NOISE"
    if qual.get("status") in ("unavailable", "error") and decision == "KEEP":
        decision = "KEEP_PROVISIONAL_NO_QUALITY"
    if decision.startswith("KEEP"):
        inc[c["track"]] = {"cfg": {k: v for k, v in c.items() if k != "_slot"}, "summary": sc,
                          "quality": (qual.get("slots") or {}).get("cand"), "run": str(d), "prev": inc[c["track"]]["cfg"]["id"]}
        save("incumbents.json", inc)
    st = load("stall.json", {})
    lane = st.setdefault(c["track"], {"since_keep": 0, "stalls": 0, "experiments": 0})
    lane["experiments"] += 1
    lane["since_keep"] = 0 if decision.startswith("KEEP") else lane["since_keep"] + 1
    if decision.startswith("KEEP"):
        st["_stall_streak"] = 0
    save("stall.json", st)
    ledger({"cmd": "run", "lane": c["track"], "id": c["id"], "decision": decision, "fails": fails, "need": need, "ci": ci,
            "hypothesis": c.get("hypothesis", ""), "cfg": {k: v for k, v in c.items() if k != "_slot"},
            "incumbent_id": i["id"], "summary": {"inc": si, "cand": sc}, "quality": qual, "run": str(d), "telemetry": tele})
    print(json.dumps({"decision": decision, "fails": fails, "ci_ratio": ci, "need": need, "inc": si, "cand": sc,
                      "quality": qual.get("slots", qual), "since_keep": lane["since_keep"]}, indent=1))
    print(f"METRIC {c['track']}.{c['objective']} inc={si['objective']} cand={sc['objective']} ratio={ci and ci['mid']}")


def cmd_aa(a):
    inc = load("incumbents.json", {})
    if a.track not in inc:
        raise SystemExit("no incumbent")
    x = dict(inc[a.track]["cfg"]); y = dict(x)
    x["_slot"], y["_slot"] = "inc", "cand"
    d = new_run_dir("aa-" + a.track.replace("/", "_"))
    recs, tele = measure([x, y], a.rounds, d)
    ci = paired_ratio_ci(recs["inc"], recs["cand"], OBJ_KEY[x["objective"]])
    hw = round(max(1 - ci["lo"], ci["hi"] - 1), 4) if ci else None
    nz = load("noise.json", {}); nz[a.track] = {"ratio_ci": ci, "ratio_halfwidth": hw, "run": str(d)}
    save("noise.json", nz)
    ledger({"cmd": "aa", "lane": a.track, "id": x["id"], "decision": "NOISE", "ci": ci, "run": str(d), "telemetry": tele,
            "summary": {"inc": summarize(recs["inc"], x["objective"]), "cand": summarize(recs["cand"], x["objective"])}})
    print(json.dumps(nz[a.track], indent=1))


def cmd_status(a):
    print(json.dumps({"epoch": BENCH_EPOCH, "incumbents": {k: {"id": v["cfg"]["id"], "objective": v["summary"]["objective"], "quality": v.get("quality")}
                                                            for k, v in load("incumbents.json", {}).items()},
                      "noise": load("noise.json", {}), "stall": load("stall.json", {})}, indent=1))


def main():
    global SER
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("baseline", "run"):
        p = sp.add_parser(n); p.add_argument("--candidate", required=True); p.add_argument("--rounds", type=int, default=0)
        p.add_argument("--tier", choices=["confirm", "screen"], default="confirm")
        p.add_argument("--force", action="store_true")
    p = sp.add_parser("aa"); p.add_argument("--track", required=True); p.add_argument("--rounds", type=int, default=2)
    sp.add_parser("status")
    a = ap.parse_args()
    global OWNER_IDLE_S
    if getattr(a, "tier", "confirm") == "screen":
        OWNER_IDLE_S = 0
    if getattr(a, "rounds", 1) == 0:
        a.rounds = 4 if getattr(a, "tier", "") == "screen" else 2
    if a.cmd == "status":
        return cmd_status(a)
    SER = serial()
    lock = open("/tmp/s25-bench.lock", "w")
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        {"baseline": cmd_baseline, "run": cmd_run, "aa": cmd_aa}[a.cmd](a)
    finally:
        kill_phone_jobs()


if __name__ == "__main__":
    main()
