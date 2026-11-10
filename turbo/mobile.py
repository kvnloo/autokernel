#!/usr/bin/env python3
"""Isolated, continuously gated adapter for the unchanged mobile referee.

The worker writes a proposal. Only the dispatcher's final device admission may
commit it, through finalize(). Importing this module does not load the referee.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import datetime
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

if __package__ in (None, ""):
    # Do not leave turbo/ on sys.path: its queue.py shadows stdlib queue.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from turbo.devices import (Device, Probe, OWNER_IDLE_SECONDS, admit,
                           adb_command, validate_phone_shell)


BASE_COMMIT = "b5c0e58a13eb5d476f28ca9a4534bb28cc077c7d"
TRACKS = {
    "tts/phonon": "phonon-base.json",
    "tts/pocket": "pocket-base.json",
    "s2s/lfm3": "lfm3-base.json",
    "asr/kroko_asr": "asr-kroko-base.json",
    "asr/moonshine_asr": "asr-moonshine-base.json",
    "asr/niagara_asr": "asr-niagara-base.json",
    "asr/parakeet_tdt": "asr-parakeet-base.json",
    "asr/qwen3_asr": "asr-qwen3asr-base.json",
    "vad/pulsevad": "vad-pulsevad-base.json",
    "vad/silero_vad": "vad-silero-base.json",
}
LOCK_ROOT = Path("/tmp")
ROUNDS = 2
PHONE_SPEC = SimpleNamespace(device_class="phone", devices=("s25", "s22", "tab"),
                             resources=SimpleNamespace(mem=1, cpus=1, gpu_mem=0))
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class GateClosed(RuntimeError):
    pass


def _finite(value, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def valid_ci(ci):
    return (isinstance(ci, dict) and all(_finite(ci.get(k), True) for k in ("lo", "mid", "hi"))
            and ci["lo"] <= ci["mid"] <= ci["hi"])


def _relative(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise ValueError("candidate path must be relative")
    if any(p in ("", ".", "..") or not _NAME.fullmatch(p) for p in value.split("/")):
        raise ValueError("unsafe candidate path")
    return value


def validate_config(cfg, track):
    """Check every field used as unquoted shell syntax by the fixed referee."""
    if not isinstance(cfg, dict) or cfg.get("track") != track or track not in TRACKS:
        raise ValueError("candidate does not belong to the requested track")
    for key in ("id", "family", "variant", "backend", "mode"):
        value = cfg.get(key, {"variant": "base", "backend": "cpu", "mode": "streaming"}.get(key))
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ValueError("unsafe candidate identifier")
    if cfg.get("lane") != track.split("/", 1)[0]:
        raise ValueError("candidate lane mismatch")
    _relative(cfg.get("model"))
    if "spec_override" in cfg:
        _relative(cfg["spec_override"])
    assets = cfg.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("candidate assets must be a list")
    for value in assets:
        _relative(value)
    if not isinstance(cfg.get("threads"), int) or isinstance(cfg["threads"], bool) or not 1 <= cfg["threads"] <= 16:
        raise ValueError("invalid candidate thread count")
    timeout = cfg.get("timeout_s", 120)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 600:
        raise ValueError("invalid candidate timeout")
    if "cpu_mask" in cfg and not re.fullmatch(r"(?:0x)?[0-9A-Fa-f]+", str(cfg["cpu_mask"])):
        raise ValueError("invalid CPU affinity mask")
    env = cfg.get("env", {})
    if not isinstance(env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                        or not isinstance(v, (str, int, float)) or "\0" in str(v)
                                        for k, v in env.items()):
        raise ValueError("invalid candidate environment")
    extra = cfg.get("extra_args", [])
    if not isinstance(extra, list) or any(not isinstance(v, str) or "\0" in v for v in extra):
        raise ValueError("invalid candidate arguments")
    if cfg.get("objective") not in ("ttfa_warm", "ttfa_cold", "rtf", "wall"):
        raise ValueError("unsupported candidate objective")
    return cfg


def _json(path, default):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _empty_state():
    return {"incumbents": {}, "noise": {}, "stall": {}, "seen": {}, "generation": 0}


def _digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, allow_nan=False).encode()).hexdigest()


@contextmanager
def phone_lock(state_root, device_name, before_release=None):
    if device_name not in ("s25", "s22", "tab"):
        raise ValueError("unsupported phone")
    directory = Path(state_root) / device_name
    directory.mkdir(parents=True, exist_ok=True)
    legacy = LOCK_ROOT / ("s25-bench.lock" if device_name == "s25" else f"hc-turbo-{device_name}-bench.lock")
    with legacy.open("a") as old_lock, (directory / "worker.lock").open("a") as lock:
        fcntl.flock(old_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield directory
        finally:
            if before_release is not None:
                before_release()


def finalize(run_dir: Path, state_root: Path, device_name: str, decision: str) -> None:
    """Commit a complete proposal only after the parent's last live gate.

The snapshot generation is checked under the phone lock. A stale proposal cannot
replace a newer incumbent. Failed/cancelled runs and absent proposals are no-ops.
    """
    proposal_path = Path(run_dir) / "mobile-proposal.json"
    if decision == "FAIL" or not proposal_path.exists():
        return
    proposal = _json(proposal_path, {})
    receipt = _json(Path(run_dir) / "result.json", {})
    if (proposal.get("device") != device_name or proposal.get("safe") is not True
            or receipt.get("owner_state") != "owner_idle"
            or receipt.get("cleanup_verified") is not True):
        return
    if decision not in ("KEEP", "REVERT", "NOISE"):
        raise ValueError("invalid final phone decision")
    with phone_lock(state_root, device_name) as directory:
        current = _json(directory / "state.json", _empty_state())
        if current.get("last_run") == str(Path(run_dir).resolve()):
            return
        if _digest(current) != proposal.get("previous_digest"):
            raise ValueError("phone proposal was measured against a different state")
        proposed = proposal["state"]
        track = proposal["track"]
        validate_config(proposal["candidate"], track)
        updated = copy.deepcopy(current)
        # Calibration and traversal survive a safe non-KEEP. Both worker and
        # parent must accept KEEP before replacing a measured incumbent.
        for key in ("noise", "seen"):
            updated[key] = proposed[key]
        need = max(.03, current["noise"].get(track, {}).get("ratio_halfwidth", .05))
        promote = (decision == "KEEP" and receipt.get("decision") == "KEEP"
                   and receipt.get("oracle_passed") is True and valid_ci(receipt.get("ci"))
                   and receipt["ci"]["hi"] < 1 - need)
        initialize = (decision == "NOISE" and proposal.get("phase") == "baseline"
                      and receipt.get("decision") == "NOISE"
                      and track not in current["incumbents"])
        if promote or initialize:
            updated["incumbents"][track] = proposed["incumbents"][track]
        if proposal.get("phase") == "run":
            previous = current["stall"].get(track, {})
            updated["stall"][track] = {
                "experiments": previous.get("experiments", 0) + 1,
                "since_keep": 0 if promote else previous.get("since_keep", 0) + 1,
                "stalls": previous.get("stalls", 0),
            }
        updated["generation"] = current["generation"] + 1
        updated["last_run"] = str(Path(run_dir).resolve())
        _write(directory / "state.json", updated)


def load_bench(repo):
    spec = importlib.util.spec_from_file_location("hc_turbo_mobile_referee", Path(repo) / "mobile" / "bench_s25.py")
    if spec is None or spec.loader is None:
        raise ValueError("fixed mobile referee unavailable")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    return bench


def select_candidate(bench, track, state, available=None):
    """Baseline, A/A, then existing configurations and bounded thread changes."""
    if track not in TRACKS:
        raise ValueError("unsupported mobile track")
    base = validate_config(bench.cfg_load(bench.ROOT / "candidates" / TRACKS[track]), track)
    if track not in state["incumbents"]:
        return "baseline", base
    incumbent = validate_config(state["incumbents"][track]["cfg"], track)
    noise = state["noise"].get(track, {})
    if not valid_ci(noise.get("ratio_ci")) or not _finite(noise.get("ratio_halfwidth")):
        return "aa", incumbent
    seen = set(state["seen"].get(track, [])) | {incumbent["id"]}
    candidates = []
    for path in sorted((bench.ROOT / "candidates").glob("*.json")):
        cfg = bench.cfg_load(path)
        if cfg["track"] == track:
            candidates.append(validate_config(cfg, track))
    # Measured configurations of the real baseline, not invented binaries.
    for threads in (2, 6, 8):
        cfg = copy.deepcopy(base)
        cfg.update(id=base["id"] + f"-turbo-t{threads}", threads=threads,
                   hypothesis=f"Measure {threads} CPU threads against the current incumbent")
        candidates.append(validate_config(cfg, track))
    for cfg in candidates:
        if cfg["id"] not in seen and (available is None or available(cfg)):
            return "run", cfg
    return "exhausted", incumbent


class PhoneTransport:
    """Only generated CLI work and exact-owned PID cleanup cross this boundary."""
    def __init__(self, bench, device, run_dir, probe=None, poll_seconds=1.0):
        self.bench, self.device, self.run_dir = bench, device, Path(run_dir)
        self.token = uuid.uuid4().hex
        self.remote = "/data/local/tmp/hc-turbo/" + self.token
        self.script = self.remote + "/hc-run-" + self.token + ".sh"
        self.probe = probe or Probe(runner=self._probe_run)
        self.poll_seconds = poll_seconds
        self.focus = None
        self.owner_in_use = False
        self.cleanup_verified = True
        self.has_launched = False
        self.remote_created = False
        self.generated_scripts = set()

    @staticmethod
    def _probe_run(argv, **kwargs):
        # Six dumpsys calls must not hide a long, unmonitored gap.
        kwargs["timeout"] = min(kwargs.get("timeout", 2), 2)
        return subprocess.run(argv, **kwargs)

    def gate(self):
        reading = self.probe.read(self.device)
        status = admit(self.device, PHONE_SPEC, reading, previous_focus=self.focus)
        if (status.owner_state != "owner_idle" or not _finite(reading.owner_idle_s)
                or reading.owner_idle_s < OWNER_IDLE_SECONDS):
            self.owner_in_use = True
        if not status.admitted:
            if status.focus != self.focus and self.focus is not None:
                self.owner_in_use = True
            raise GateClosed("; ".join(status.reasons) or "owner_in_use")
        if self.focus is None:
            self.focus = reading.focus
        return reading

    def telemetry(self):
        r = self.gate()
        return {"level": r.battery_pct, "plugged": r.charging,
                "AP": r.ap_c, "BAT": r.battery_c, "SKIN": None,
                "batt_dC": r.battery_c * 10, "owner_idle_s": r.owner_idle_s,
                "awake": r.awake, "call_state": r.call_state, "audio_mode": r.audio_mode,
                "focus": r.focus, "focus_display": None,
                "charge_uAh": None, "current_uA": None, "voltage_mV": None,
                "at": datetime.datetime.now().isoformat(timespec="seconds")}

    def execute(self, argv, timeout=600, **unused):
        self.gate()
        start = time.monotonic()
        with tempfile.TemporaryFile(mode="w+") as out, tempfile.TemporaryFile(mode="w+") as err:
            process = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, text=True)
            try:
                while process.poll() is None:
                    self.gate()
                    if time.monotonic() - start > timeout:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(self.poll_seconds)
                self.gate()
                out.seek(0)
                err.seek(0)
                return subprocess.CompletedProcess(argv, process.returncode, out.read(), err.read())
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

    def shell(self, command, timeout=600, check=True):
        result = self.execute(adb_command(self.device.serial, "shell", command), timeout)
        if check and result.returncode:
            raise RuntimeError("owned phone shell command failed")
        return result.stdout

    def transfer(self, operation, source, destination):
        result = self.execute(adb_command(self.device.serial, operation, "-q", str(source), str(destination)), 1800)
        if result.returncode:
            raise RuntimeError("owned phone transfer failed")
        return result

    def install_adapter(self):
        b = self.bench
        b.SER, b.PD = self.device.serial, self.remote
        # Owner rule: Android manages battery/thermal; no manual limits.
        b.OWNER_IDLE_S, b.MIN_BATT = int(OWNER_IDLE_SECONDS), 0
        b.MAX_AP_C = b.MAX_BAT_C = float("inf")
        original_block = b.phone_block

        def block(jobs):
            checked = []
            for tag, cmd, wav in jobs:
                if not _NAME.fullmatch(tag) or wav != f"{self.remote}/out/{tag}.wav":
                    raise ValueError("invalid owned benchmark output")
                # The original pipeline loses CLI exit codes. Preserve them in
                # separate artifacts without altering the event parser.
                checked.append((tag, "{ " + cmd + "; rc=$?; printf '%s\\n' \"$rc\" > "
                                + f"{self.remote}/out/{tag}.exit; }}", wav))
            script = original_block(checked)
            validate_phone_shell(script)
            self.generated_scripts.add(script)
            return script

        b.phone_block = block
        b.sh, b.adb, b.sync = self.referee_shell, self.referee_adb, self.sync
        b.telemetry = self.telemetry
        b.wait_for_gates = lambda **kwargs: self.telemetry()
        b.kill_phone_jobs = self.cleanup
        # Keep monitoring while the original host-side quality scorer runs.
        b.subprocess = SimpleNamespace(run=self.execute, TimeoutExpired=subprocess.TimeoutExpired)

    def referee_shell(self, command, timeout=600, check=False):
        if command == f"cd {self.remote} && sh block.sh":
            self.has_launched = True
            self.cleanup_verified = False
            return self.shell("sh " + self.script, timeout)
        if command == f"rm -rf {self.remote}/out && mkdir -p {self.remote}/out":
            return self.shell(command, timeout)
        if re.fullmatch(re.escape(f"rm -f {self.remote}/out/") + r"(?:only|inc|cand)-\*-r\d+\.\*", command):
            return self.shell(command, timeout)
        raise ValueError("unreviewed referee shell operation")

    def referee_adb(self, *args, timeout=1800):
        if len(args) != 4 or args[1] != "-q":
            raise ValueError("unreviewed referee transfer")
        op, _, source, destination = args
        if op == "push" and destination == self.remote + "/block.sh":
            source = Path(source)
            if not source.resolve().is_relative_to(self.run_dir.resolve()) or source.read_text() not in self.generated_scripts:
                raise ValueError("only the reviewed generated script may be pushed")
            return self.transfer("push", source, self.script)
        if (op == "pull" and source == self.remote + "/out/."
                and Path(destination).resolve().is_relative_to(self.run_dir.resolve())):
            return self.transfer("pull", source, destination)
        raise ValueError("unreviewed referee transfer")

    def assets(self, cfg):
        validate_config(cfg, cfg["track"])
        binary = Path(self.bench.binary(cfg)).resolve()
        if not binary.is_file() or not binary.is_relative_to(self.bench.VARIANTS.resolve()):
            raise ValueError("candidate binary is outside the configured bundle")
        yield "bin/" + cfg.get("variant", "base") + "/audiocpp_cli", binary
        for name in self.bench.model_paths(cfg):
            _relative(name)
            path = (self.bench.MODELS / name).resolve()
            if not path.exists() or not path.is_relative_to(self.bench.MODELS.resolve()):
                raise ValueError("candidate model assets unavailable")
            yield "m/" + name, path

    def available(self, cfg):
        try:
            list(self.assets(cfg))
            return True
        except (OSError, ValueError, SystemExit):
            return False

    def sync(self, configs):
        assets = dict(item for cfg in configs for item in self.assets(cfg))
        # Mark before the first operation: a transfer/gate failure can leave a
        # partially created tree, which still belongs exclusively to this run.
        self.remote_created = True
        self.shell(f"mkdir -p {self.remote}/bin {self.remote}/m {self.remote}/out")
        free = self.shell("df -k /data | tail -1").split()
        need = sum(self.bench.sig(path)[1] for path in assets.values())
        if len(free) < 4 or not free[3].isdigit() or int(free[3]) * 1024 < need + 10**9:
            raise GateClosed("insufficient free phone storage for isolated assets")
        for relative, source in assets.items():
            destination = self.remote + "/" + relative
            self.shell("mkdir -p " + (destination if source.is_dir() else str(Path(destination).parent)))
            self.transfer("push", str(source) + ("/." if source.is_dir() else ""), destination)
            if relative.startswith("bin/"):
                self.shell("chmod 755 " + destination)

    def _raw(self, command):
        result = subprocess.run(adb_command(self.device.serial, "shell", command),
                                capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError("owned process cleanup unavailable")
        return result.stdout

    def owns_argv(self, argv):
        if not argv:
            return False
        executable = argv[0]
        binary = lambda value: (value.startswith(self.remote + "/bin/")
                                and value.endswith("/audiocpp_cli")
                                and ".." not in value.split("/"))
        if binary(executable):
            return True
        name = executable.rsplit("/", 1)[-1]
        if name == "sh":
            return argv[1:] == [self.script]
        return name in ("timeout", "taskset") and any(binary(value) for value in argv[1:])

    def owned_processes(self):
        found = []
        for line in self._raw("ps -A -o PID,ARGS").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit() or self.remote + "/" not in parts[1]:
                continue
            pid = int(parts[0])
            if pid <= 1:
                continue
            data = self._raw(f"cat /proc/{pid}/stat 2>/dev/null; printf '\\nHC_PID\\n'; cat /proc/{pid}/cmdline 2>/dev/null || true")
            sections = data.split("\nHC_PID\n", 1)
            if len(sections) != 2:
                self.require_disappearance(pid)
                continue
            stat, argv = sections
            fields = stat.strip().rsplit(") ", 1)
            if len(fields) != 2 or not argv.endswith("\0"):
                self.require_disappearance(pid)
                continue
            fields = fields[1].split()
            if len(fields) <= 19 or not fields[19].isdigit():
                self.require_disappearance(pid)
                continue
            if self.owns_argv(argv.rstrip("\0").split("\0")):
                found.append((pid, fields[19]))
        return found

    def require_disappearance(self, pid):
        status = self._raw(f"if [ -d /proc/{pid} ]; then printf 'PRESENT'; else printf 'GONE'; fi")
        if status != "GONE":
            raise RuntimeError("matched phone PID is unreadable; quarantine this device")

    def cleanup(self):
        if not self.has_launched:
            self.cleanup_verified = True
            return
        self.cleanup_verified = False
        # No shared-basename match, negative PID, or default device. Positive
        # PIDs must carry our unique path and unchanged process start time.
        for sig in ("TERM", "KILL"):
            owned = self.owned_processes()
            if not owned:
                self.cleanup_verified = True
                self.has_launched = False
                return
            for pid, started in reversed(owned):
                self._raw(f"if [ \"$(awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null)\" = {started} ]; then kill -{sig} {pid} 2>/dev/null || true; fi")
            time.sleep(0.2)
        self.cleanup_verified = not self.owned_processes()
        if not self.cleanup_verified:
            raise RuntimeError("owned phone processes remain; quarantine this device")
        self.has_launched = False

    def teardown(self):
        self.cleanup()
        if self.remote_created:
            self.cleanup_verified = False
            # The UUID directory is owned by this worker. Never delete the
            # shared benchmark directories, other runs, or a parent directory.
            self._raw(f"rm -rf {self.remote} && [ ! -e {self.remote} ]")
            self.remote_created = False
            self.cleanup_verified = True


def complete_records(bench, configs, records, directory, rounds):
    for cfg in configs:
        items = bench.EVALSET[cfg["lane"]]
        expected = {f"{cfg['_slot']}-{item['id']}-r{r}" for r in range(rounds) for item in items}
        actual = records.get(cfg["_slot"], [])
        if len(actual) != len(expected) or {r.get("tag") for r in actual} != expected:
            return False
        key = bench.OBJ_KEY[cfg["objective"]]
        for record in actual:
            tag = record["tag"]
            if (record.get("errs") or record.get("contaminated")
                    or not _finite(record.get(key), True) or not _finite(record.get("audio_s"), True)):
                return False
            wav = Path(directory) / "wav" / (tag + ".wav")
            exit_file = wav.with_suffix(".exit")
            if (not wav.is_file() or wav.stat().st_size == 0 or not exit_file.is_file()
                    or exit_file.read_text().strip() != "0"):
                return False
            if cfg["lane"] == "s2s" and (not wav.with_suffix(".txt").is_file()
                                         or not wav.with_suffix(".txt").read_text().strip()):
                return False
    return True


def quality_complete(quality, lane, slots, expected):
    # The original VAD referee is expressly latency-only; its faster candidates
    # cannot supply a correctness oracle and therefore never become KEEP.
    if lane == "vad" or quality.get("status") in ("error", "unavailable"):
        return False
    for slot in slots:
        values = quality.get("slots", {}).get(slot, {})
        if values.get("n") != expected or not _finite(values.get("wer")):
            return False
        if lane in ("tts", "s2s") and not _finite(values.get("mos"), True):
            return False
        if lane == "s2s" and values.get("task_acc") != 1:
            return False
    return True


def run_worker(args, bench=None, probe=None):
    run_dir, state_root = Path(args.run_dir).resolve(), Path(args.state_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"decision": "FAIL", "ci": None, "oracle_passed": False,
               "owner_state": "owner_in_use", "reason": "worker did not complete",
               "cleanup_verified": True, "base_commit": BASE_COMMIT}
    transport = None
    proposal = None
    try:
        adb_command(args.serial, "shell", "true")  # Validate, without running.
        with phone_lock(state_root, args.device,
                        before_release=lambda: transport.teardown() if transport is not None else None) as directory:
            previous = _json(directory / "state.json", _empty_state())
            b = bench or load_bench(Path(__file__).resolve().parent.parent)
            b.STATE = run_dir / "bench-state"
            b.STATE.mkdir()
            for key in ("incumbents", "noise", "stall"):
                _write(b.STATE / (key + ".json"), previous[key])
            transport = PhoneTransport(b, Device(args.device, "phone", 1, args.serial), run_dir, probe)
            transport.gate()
            transport.install_adapter()
            phase, cfg = select_candidate(b, args.track, previous, transport.available)
            receipt.update(phase=phase, candidate=cfg["id"])
            if phase == "exhausted":
                receipt.update(decision="NOISE", reason="all available bounded candidates have been measured")
            else:
                _write(run_dir / "candidate.json", cfg)
                rows = []
                original_measure = b.measure

                def measure(configs, rounds, measured_dir, log=print):
                    for config in configs:
                        validate_config(config, args.track)
                    records, telemetry = original_measure(configs, rounds, measured_dir, log)
                    if not complete_records(b, configs, records, measured_dir, rounds):
                        raise ValueError("incomplete, failed or contaminated phone measurements")
                    return records, telemetry

                b.measure = measure
                measured_dir = run_dir / "referee"
                (measured_dir / "wav").mkdir(parents=True)
                b.new_run_dir = lambda tag: measured_dir

                def ledger(row):
                    rows.append(row)
                    _write(run_dir / "referee-result.json", row)

                b.ledger = ledger
                call = SimpleNamespace(candidate=str(run_dir / "candidate.json"), track=args.track,
                                       rounds=ROUNDS, tier="confirm", force=False)
                {"baseline": b.cmd_baseline, "aa": b.cmd_aa, "run": b.cmd_run}[phase](call)
                transport.gate()
                if len(rows) != 1:
                    raise ValueError("referee did not produce one result")
                row = rows[0]
                ci = row.get("ci")
                if ci is not None and not valid_ci(ci):
                    raise ValueError("invalid referee confidence interval")
                slots = ["only"] if phase == "baseline" else ["inc", "cand"]
                oracle = quality_complete(row.get("quality", {}), cfg["lane"], slots,
                                          ROUNDS * len(b.EVALSET[cfg["lane"]]))
                raw = row["decision"]
                if phase == "baseline":
                    if raw != "BASELINE_SET" or row.get("fails"):
                        raise ValueError("baseline quality or measurement gate failed")
                    decision = "NOISE"
                elif phase == "aa":
                    if not valid_ci(ci):
                        raise ValueError("A/A calibration produced no valid interval")
                    decision = "NOISE"
                elif raw == "KEEP" and oracle and not row.get("fails") and valid_ci(ci):
                    need = max(0.03, previous["noise"][args.track]["ratio_halfwidth"])
                    decision = "KEEP" if ci["hi"] < 1 - need else "NOISE"
                elif raw in ("REVERT_GATE", "REVERT_SLOWER"):
                    decision = "REVERT"
                elif raw in ("KEEP", "KEEP_PROVISIONAL_NO_QUALITY", "REVERT_NOISE"):
                    decision = "NOISE"
                else:
                    raise ValueError("referee did not produce a usable comparison")
                if transport.owner_in_use and decision == "KEEP":
                    decision = "NOISE"
                receipt.update(decision=decision, ci=ci, oracle_passed=oracle,
                               reason=("owner_in_use; " + raw if transport.owner_in_use else
                                       raw if oracle or phase == "aa" else raw + "; correctness oracle unavailable"))
                proposed = copy.deepcopy(previous)
                for key in ("incumbents", "noise"):
                    proposed[key] = _json(b.STATE / (key + ".json"), previous[key])
                if phase == "run":
                    proposed["seen"].setdefault(args.track, []).append(cfg["id"])
                proposal = {"device": args.device, "phase": phase, "track": args.track,
                            "candidate": cfg, "state": proposed, "safe": True,
                            "previous_digest": _digest(previous)}
            transport.cleanup()
            transport.gate()
            receipt["owner_state"] = "owner_idle"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError, SystemExit) as exc:
        # Never persist arbitrary subprocess output, focus text or serials.
        receipt.update(decision="FAIL", reason="phone worker stopped: " + type(exc).__name__)
        proposal = None
    finally:
        if transport is not None:
            try:
                transport.teardown()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                receipt.update(decision="FAIL", reason="owned phone cleanup unverified")
                proposal = None
            receipt["cleanup_verified"] = transport.cleanup_verified
            if transport.owner_in_use:
                receipt["owner_state"] = "owner_in_use"
        if proposal is not None and receipt["decision"] != "FAIL":
            _write(run_dir / "mobile-proposal.json", proposal)
        _write(run_dir / "result.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True, choices=tuple(TRACKS))
    parser.add_argument("--device", required=True, choices=("s25", "s22", "tab"))
    parser.add_argument("--serial", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()

    def stop(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = run_worker(args)
    return 0 if result["decision"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
