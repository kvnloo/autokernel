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

BENCH_EPOCH = 1
ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "state"
ZLV = pathlib.Path(os.environ.get("ZLV_ROOT", "/mnt/zer0models/models/zlv-tts"))
MODELS = ZLV / "acpp-models"
VARIANTS = ZLV / "phone-bundle" / "variants"
PD = "/data/local/tmp/z0live-ar"
OWNER_IDLE_S = int(os.environ.get("ZLV_OWNER_IDLE_S", "90"))
MAX_AP_C, MAX_BAT_C, MIN_BATT = 42.0, 38.0, 30
EVALSET = json.loads((ROOT / "evalset" / "v1.json").read_text())


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


def telemetry():
    out = sh("dumpsys battery | grep -E '^  (level|temperature|AC powered|USB powered|Wireless powered|current now|Charge counter|voltage):';"
             "echo ==; dumpsys thermalservice | grep -E 'mName=(AP|SKIN|BAT),' | head -3; echo ==;"
             "dumpsys power | grep -E '^ *lastUserActivityTime=|mWakefulness='; echo ==;"
             "dumpsys window | grep -m1 mTopFocusedDisplayId; dumpsys window | grep -m1 mCurrentFocus", timeout=60)
    t = {}
    for k, key in (("level", "level"), ("temperature", "batt_dC"), ("current now", "current_uA"), ("Charge counter", "charge_uAh"), ("voltage", "voltage_mV")):
        m = re.search(rf"^  {k}: (-?\d+)", out, re.M)
        t[key] = int(m[1]) if m else None
    t["plugged"] = bool(re.search(r"(AC|USB|Wireless) powered: true", out))
    for name in ("AP", "SKIN", "BAT"):
        m = re.search(rf"mValue=([\d.]+), mType=\d+, mName={name},", out)
        t[name] = float(m[1]) if m else None
    m = re.search(r"lastUserActivityTime=\d+ \((\d+) ms ago\)", out)
    t["owner_idle_s"] = int(m[1]) / 1000 if m else None
    t["awake"] = "mWakefulness=Awake" in out
    m = re.search(r"mTopFocusedDisplayId=(\d+)", out)
    t["focus_display"] = int(m[1]) if m else None
    m = re.search(r"mCurrentFocus=(.*)", out)
    t["focus"] = m[1].strip() if m else None
    t["at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return t


def wait_for_gates(max_wait_s=1800, log=print):
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
        if t["owner_idle_s"] is None or t["owner_idle_s"] < OWNER_IDLE_S:
            why.append(f"owner active {t['owner_idle_s']}s ago")
        if not why:
            return t
        if any("battery" in w and "%" in w for w in why):
            raise SystemExit("GATE battery: " + "; ".join(why))
        if time.time() - start > max_wait_s:
            raise SystemExit("GATE timeout: " + "; ".join(why))
        log("waiting: " + "; ".join(why))
        time.sleep(30)


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
    if cfg.get("lane") == "s2s":
        paths.append("ref")
    if cfg.get("spec_override"):
        paths.append("specs")
    return sorted(set(paths))


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
    else:
        raise SystemExit(f"lane {cfg['lane']} not wired in bench epoch {BENCH_EPOCH}")
    a += [x.replace("{M}", f"{PD}/m") for x in cfg.get("extra_args", [])]
    pre = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in cfg.get("env", {}).items())
    mask = cfg.get("cpu_mask")
    return (pre + " " if pre else "") + (f"taskset {mask} " if mask else "") + " ".join(shlex.quote(x) for x in a)


def phone_block(jobs):
    """jobs: list of (tag, cmd, wav). Emits raw ns timestamps; arithmetic happens on groot (mksh is 32-bit)."""
    lines = ["N() { date +%s%N; }"]
    for tag, cmd, wav in jobs:
        lines.append(f"echo \"EV {tag} START $(N)\"")
        lines.append(f"{cmd} 2>&1 | while IFS= read -r l; do case \"$l\" in "
                     f"*text.prepare_ms*|*prompt_ms*|*voice.prepare_ms*) echo \"EV {tag} LOADED $(N)\";; "
                     f"audio_output_id=*) echo \"EV {tag} FIRST $(N)\";; "
                     f"*session.wall_ms*) echo \"EV {tag} WALL ${{l##* }}\";; "
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
    sync(cfgs)
    for r in range(rounds):
        order = cfgs if r % 2 == 0 else list(reversed(cfgs))
        for c in order:
            t0 = wait_for_gates(log=log)
            jobs = []
            for it in items:
                tag = f"{c['_slot']}-{it['id']}-r{r}"
                wav = f"{PD}/out/{tag}.wav"
                jobs.append((tag, cmdline(c, it, wav), wav))
            sh(f"rm -f {PD}/out/{c['_slot']}-*-r{r}.*")
            script = phone_block(jobs)
            (run_dir / f"{c['_slot']}-r{r}.sh").write_text(script)
            adb("push", "-q", str(run_dir / f"{c['_slot']}-r{r}.sh"), f"{PD}/block.sh")
            out = sh(f"cd {PD} && sh block.sh", timeout=1800)
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
                secs = wav_seconds(run_dir / "wav" / f"{tag}.wav")
                rec = {"tag": tag, "item": it["id"], "round": r, "errs": e["errs"], "audio_s": secs, "contaminated": contaminated}
                if "first" in e:
                    rec["start_to_first_ms"] = (e["first"] - e["start"]) / 1e6
                    if "loaded" in e:
                        rec["load_to_first_ms"] = (e["first"] - e["loaded"]) / 1e6
                if "end" in e:
                    rec["proc_ms"] = (e["end"] - e["start"]) / 1e6
                if "wall_ms" in e and secs:
                    rec["rtf"] = e["wall_ms"] / 1000 / secs
                recs[c["_slot"]].append(rec)
            ch = (t0["charge_uAh"] or 0) - (t1["charge_uAh"] or 0)
            log(f"  {c['_slot']} r{r}: {sum(1 for x in recs[c['_slot']] if x['round']==r and not x['errs'])}/{len(items)} ok, "
                f"AP {t0['AP']}->{t1['AP']}C, charge -{ch} uAh{' CONTAMINATED' if contaminated else ''}")
    return recs, tele


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None


def summarize(rs, objective):
    ok = [r for r in rs if not r["errs"] and r.get("audio_s") and not r["contaminated"]]
    s = {"n": len(rs), "ok": len(ok)}
    for k in ("load_to_first_ms", "start_to_first_ms", "rtf", "proc_ms"):
        v = [r[k] for r in ok if r.get(k) is not None]
        if v:
            s[k] = {"p50": round(statistics.median(v), 2), "p90": round(q(v, .9), 2), "n": len(v)}
    s["objective"] = (s.get(OBJ_KEY[objective]) or {}).get("p50")
    return s


OBJ_KEY = {"ttfa_warm": "load_to_first_ms", "ttfa_cold": "start_to_first_ms", "rtf": "rtf", "wall": "proc_ms"}


def paired_ratio_ci(inc, cand, key, n=4000, seed=7):
    """Bootstrap CI of median(cand)/median(inc), resampling within each eval item (paired design)."""
    by = {}
    for side, rs in (("i", inc), ("c", cand)):
        for r in rs:
            if r.get(key) is not None and not r["errs"] and not r["contaminated"]:
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


def quality(run_dir, slots, lane):
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
        p = sp.add_parser(n); p.add_argument("--candidate", required=True); p.add_argument("--rounds", type=int, default=2)
        p.add_argument("--force", action="store_true")
    p = sp.add_parser("aa"); p.add_argument("--track", required=True); p.add_argument("--rounds", type=int, default=2)
    sp.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "status":
        return cmd_status(a)
    SER = serial()
    lock = open("/tmp/s25-bench.lock", "w")
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX)
    {"baseline": cmd_baseline, "run": cmd_run, "aa": cmd_aa}[a.cmd](a)


if __name__ == "__main__":
    main()
