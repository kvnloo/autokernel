"""Fresh admission readings and bounded launch commands for HC-TURBO.

No probe reads a cached fleet/phone report. The dispatcher calls ``Probe.read``
before each admission and passes reservations from *both* groot slot groups.
Phone serials are explicit: probing does not discover or change attached devices.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Iterable


GIB = 1024 ** 3
MIB = 1024 ** 2
GROOT_HEADROOM = 6 * GIB
# A phone run's host process is a tiny adb/referee supervisor (31 MB peak measured
# 2026-11-09; 12-15 MB RSS live 2026-11-10) whose scope is hard-capped at the spec's
# MemoryMax (128 MiB). The phone does the work, so a busy groot must not idle a phone:
# keep only enough margin that the capped scope cannot push groot into OOM.
PHONE_HOST_HEADROOM = 512 * MIB
OWNER_IDLE_SECONDS = 90.0  # mobile/bench_s25.py's confirmation window
MAX_READING_AGE = 30.0

# fleet-kueue uses this same runpy API to obtain the configured MBP SSH route.
# Importing the installed fleet script does not run its CLI. Only registry()
# and its read-only single-node probe are invoked; no health report is reused.
_MBP_PROBE = """import json, pathlib, runpy, shutil, sys
path = shutil.which('fleet')
if not path:
    raise SystemExit('fleet is not installed')
path = pathlib.Path(path).resolve()
sys.path.insert(0, str(path.parent))
fleet = runpy.run_path(str(path))
node = fleet['registry']()[1]['mbp']
print(json.dumps(fleet['probe'](node)))
"""


@dataclass(frozen=True)
class Device:
    name: str
    device_class: str
    capacity: int
    serial: str | None = None


@dataclass(frozen=True)
class Reading:
    measured_at: float
    mem_available: int | None = None
    cpus: int | None = None
    gpu_free: int | None = None
    battery_pct: int | None = None
    charging: bool | None = None
    ap_c: float | None = None
    battery_c: float | None = None
    owner_idle_s: float | None = None
    awake: bool | None = None
    call_state: int | None = None
    audio_mode: int | None = None
    focus: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Admission:
    admitted: bool
    reasons: tuple[str, ...]
    owner_state: str | None = None
    focus: str | None = None


PHONES = ("s25", "s22", "tab")


def default_devices(s25_serial: str | None = None,
                    s22_serial: str | None = None,
                    tab_serial: str | None = None) -> tuple[Device, ...]:
    s25_serial = s25_serial or os.environ.get("S25_SERIAL")
    s22_serial = s22_serial or os.environ.get("S22_SERIAL")
    tab_serial = tab_serial or os.environ.get("TAB_SERIAL")
    serials = [s for s in (s25_serial, s22_serial, tab_serial) if s]
    if len(serials) != len(set(serials)):
        raise ValueError("S25, S22 and tab must have distinct explicit adb serials")
    return (
        Device("groot-gpu", "gpu", 2),
        Device("groot-cpu", "cpu", 1),
        Device("mbp", "cpu", 3),
        Device("s25", "phone", 1, s25_serial),
        Device("s22", "phone", 1, s22_serial),
        Device("tab", "phone", 1, tab_serial),
    )


def _number(value: Any, minimum: float = 0) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= minimum)


def _integer(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_phone_shell(script: str) -> None:
    """Reject UI, package-management, activity and display operations.

Only reviewed generated benchmark scripts belong here; this is not a sandbox
for arbitrary shell programs. Validate script contents before pushing them too.
    """
    if not isinstance(script, str) or not script.strip() or "\0" in script:
        raise ValueError("invalid phone shell command")
    forbidden = r"(?<![A-Za-z0-9_])(?:input|sendevent|getevent|monkey|am|pm|cmd|service|settings|wm|uiautomator|scrcpy|install|display|eval)(?![A-Za-z0-9_])"
    if re.search(forbidden, script, re.IGNORECASE):
        raise ValueError("phone command violates the no-UI/no-install contract")
    if "`" in script:
        raise ValueError("backtick expansion is not allowed in phone scripts")


def adb_command(serial: str, operation: str, *arguments: str) -> list[str]:
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", serial):
        raise ValueError("an explicit adb serial is required")
    if operation not in ("shell", "push", "pull"):
        raise ValueError("only adb shell/push/pull are allowed")
    if not arguments or any(not isinstance(a, str) or "\0" in a for a in arguments):
        raise ValueError("invalid adb arguments")
    if operation == "shell":
        validate_phone_shell(" ".join(arguments))
    return ["adb", "-s", serial, operation, *arguments]


def parse_meminfo(text: str) -> int:
    values = re.findall(r"^MemAvailable:\s+(\d+)\s+kB\s*$", text, re.MULTILINE)
    if len(values) != 1:
        raise ValueError("MemAvailable is unavailable")
    return int(values[0]) * 1024


def parse_phone(parts: dict[str, str], measured_at: float) -> Reading:
    """Parse live HAL data; cached temperature entries never satisfy the gate."""
    battery = parts["battery"]

    def battery_int(name: str) -> int | None:
        values = re.findall(rf"^\s*{re.escape(name)}:\s*(-?\d+)\s*$", battery, re.MULTILINE)
        return int(values[0]) if len(values) == 1 else None

    powered = re.findall(r"^\s*(?:AC|USB|Wireless|Dock) powered:\s*(true|false)\s*$",
                         battery, re.MULTILINE)
    charging = any(p == "true" for p in powered) if powered else None
    thermal = parts["thermalservice"]
    marker = re.search(r"^\s*Current temperatures from HAL:\s*$", thermal, re.MULTILINE)
    ap_values = []
    if marker:
        current = thermal[marker.end():]
        # Subsequent headings end the current-temperature section.
        current = re.split(r"\n(?=[ \t]*[A-Za-z][^\n{}]*:)", current, maxsplit=1)[0]
        for record in re.findall(r"Temperature\{([^}]*)\}", current):
            fields = dict(re.findall(r"(m\w+)=([^,}]+)", record))
            if fields.get("mName", "").strip() == "AP":
                try:
                    ap_values.append(float(fields["mValue"]))
                except (KeyError, ValueError):
                    ap_values.append(float("nan"))
        if not ap_values:
            # Non-Samsung SoCs (Lenovo TB350FU) name the application processor
            # "CPU"; Android's TEMPERATURE_TYPE_CPU is mType=0 on both.
            for record in re.findall(r"Temperature\{([^}]*)\}", current):
                fields = dict(re.findall(r"(m\w+)=([^,}]+)", record))
                if fields.get("mType", "").strip() == "0":
                    try:
                        ap_values.append(float(fields["mValue"]))
                    except (KeyError, ValueError):
                        ap_values.append(float("nan"))
    ap = max(ap_values) if ap_values and all(_number(v) for v in ap_values) else None
    power = parts["power"]
    idle = re.search(r"lastUserActivityTime=\d+\s+\((\d+) ms ago\)", power)
    wake = re.search(r"\bmWakefulness=(Awake|Asleep|Dozing|Dreaming)\b", power)
    window = parts["window"]
    displays = re.findall(r"\bmTopFocusedDisplayId=(\d+)\b", window)
    focuses = re.findall(r"^\s*mCurrentFocus=(.+)$", window, re.MULTILINE)
    # Store an opaque equality token, never the foreground package/window text.
    focus = None
    # Samsung lists every display; secondary/virtual ones (and a sleeping screen)
    # report null. The token covers all displays so any focus change still holds
    # the phone, and a null-only reading is a real state, not a missing one.
    if displays:
        # Agents drive virtual displays (ChatGPT driver on S22 display 145);
        # only the owner-facing top-focused display decides a focus change.
        sections = re.split(r"^\s*Display: mDisplayId=(\d+)\b.*$", window, flags=re.MULTILINE)
        scoped = dict(zip(sections[1::2], sections[2::2]))
        if displays[0] in scoped:
            focuses = re.findall(r"^\s*mCurrentFocus=(.+)$", scoped[displays[0]], re.MULTILINE)[:1]
    if displays and focuses:
        # Drop the per-instance window token (Window{3d727c1 u0 ...}): a
        # launcher recreated under memory pressure is not an owner action.
        names = [re.sub(r"^Window\{[0-9a-f]+ ", "Window{", f.strip()) for f in focuses]
        focus = hashlib.sha256("\0".join([displays[0], *names]).encode()).hexdigest()
    registry = parts["telephony.registry"]
    calls = [int(x) for x in re.findall(r"\bmCallState=(\d+)\b", registry)]
    if not calls and re.search(r"^\s*mDefaultPhoneId=-1\s*$", registry, re.MULTILINE):
        calls = [0]  # Wi-Fi-only device: no phone, so no call can be active.
    mode_names = {"MODE_NORMAL": 0, "MODE_RINGTONE": 1, "MODE_IN_CALL": 2,
                  "MODE_IN_COMMUNICATION": 3, "MODE_CALL_SCREENING": 4,
                  "MODE_CALL_REDIRECT": 5, "MODE_COMMUNICATION_REDIRECT": 6}
    raw_modes = re.findall(r"mAudioModeOwner:[^\n]*?\bmMode=([A-Za-z_0-9]+)", parts["audio"])
    modes = [int(x) if x.isdigit() else mode_names.get(x) for x in raw_modes]
    audio = max(modes) if modes and all(x is not None for x in modes) else None
    temp = battery_int("temperature")
    return Reading(
        measured_at=measured_at,
        battery_pct=battery_int("level"), charging=charging, ap_c=ap,
        battery_c=temp / 10 if temp is not None else None,
        owner_idle_s=int(idle[1]) / 1000 if idle else None,
        awake=wake[1] in ("Awake", "Dreaming") if wake else None,
        call_state=max(calls) if calls else None, audio_mode=audio, focus=focus,
    )


class Probe:
    def __init__(self, runner: Callable = subprocess.run,
                 clock: Callable[[], float] = time.monotonic):
        self.runner = runner
        self.clock = clock

    def _read(self, argv: list[str]) -> str:
        result = self.runner(argv, capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise ValueError("probe command failed")
        return result.stdout

    def read(self, device: Device) -> Reading:
        started = self.clock()
        try:
            if device.device_class == "phone":
                if not device.serial:
                    raise ValueError("phone serial is not configured")
                parts = {service: self._read(adb_command(device.serial, "shell", "dumpsys", service))
                         for service in ("battery", "thermalservice", "power", "window", "telephony.registry", "audio")}
                return parse_phone(parts, started)
            if device.name in ("groot-cpu", "groot-gpu"):
                mem = parse_meminfo(self._read(["cat", "/proc/meminfo"]))
                cpus = int(self._read(["getconf", "_NPROCESSORS_ONLN"]).strip())
                gpu = None
                if device.device_class == "gpu":
                    raw = self._read(["nvidia-smi", "--id=0", "--query-gpu=memory.free",
                                      "--format=csv,noheader,nounits"]).strip()
                    if not re.fullmatch(r"\d+", raw):
                        raise ValueError("GPU 0 free memory is unavailable")
                    gpu = int(raw) * MIB
                return Reading(started, mem_available=mem, cpus=cpus, gpu_free=gpu)
            if device.name == "mbp":
                live = json.loads(self._read(["python3", "-c", _MBP_PROBE]))
                if (not isinstance(live, dict) or live.get("error")
                        or live.get("maintenance") is not False or live.get("paused") is not False):
                    raise ValueError("MBP live probe is unavailable or paused")
                mem, cpus = live.get("mem_available_mb"), live.get("ncpu")
                if not _integer(mem, 1) or not _integer(cpus, 1):
                    raise ValueError("MBP live resource reading is unavailable")
                return Reading(started, mem_available=mem * MIB, cpus=cpus)
            raise ValueError("unknown device")
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            # Do not persist arbitrary command output, which may contain credentials.
            return Reading(started, error=type(exc).__name__)


def admit(device: Device, spec: Any, reading: Reading,
          reserved_resources: Iterable[Any] = (), previous_focus: str | None = None,
          *, now: float | None = None, headroom: int = GROOT_HEADROOM) -> Admission:
    """Fail closed. Reservations must include shared-host CPU and GPU slots."""
    reasons = []
    owner_state = None
    now = time.monotonic() if now is None else now
    if reading.error:
        reasons.append("live probe unavailable")
    if (not _number(reading.measured_at) or not _number(now)
            or not 0 <= now - reading.measured_at <= MAX_READING_AGE):
        reasons.append("missing or stale live reading")
    if device.device_class != spec.device_class:
        reasons.append("device class does not match spec")
    allowed = getattr(spec, "devices", ())
    if allowed and device.name not in allowed:
        reasons.append("device is not permitted by spec")
    res = spec.resources
    reservations = tuple(reserved_resources)
    if any(not _integer(getattr(r, "mem", None), 1)
           or not _integer(getattr(r, "cpus", None), 1)
           or not _integer(getattr(r, "gpu_mem", None), 0)
           for r in (res, *reservations)):
        return Admission(False, tuple(reasons + ["invalid resource requirement"]))
    if device.device_class == "phone":
        if not device.serial:
            reasons.append("phone serial is not configured")
        # Owner rule: no manual phone resource limits (battery, thermal, RAM,
        # CPU, GPU); Android throttles and manages those itself. Only owner
        # presence (calls, audio, activity, focus) holds a phone.
        if not _integer(reading.call_state) or reading.call_state != 0:
            reasons.append("call state missing or active")
        if not _integer(reading.audio_mode) or reading.audio_mode != 0:
            reasons.append("audio call state missing or active")
        if not _number(reading.owner_idle_s) or type(reading.awake) is not bool:
            reasons.append("owner activity reading unavailable")
        else:
            owner_state = ("owner_in_use" if reading.awake and reading.owner_idle_s < OWNER_IDLE_SECONDS
                           else "owner_idle")
        if not isinstance(reading.focus, str) or not reading.focus:
            reasons.append("focus reading unavailable")
        elif previous_focus is not None and reading.focus != previous_focus:
            # Owner rule: hill climbs are adb CLI and never touch display 0, so
            # owner activity labels the run (no KEEP) but never pauses it.
            owner_state = "owner_in_use"
    elif device.name in ("groot-cpu", "groot-gpu", "mbp"):
        if not _integer(reading.mem_available, 1) or not _integer(reading.cpus, 1):
            reasons.append("host resource reading unavailable")
        else:
            mem = reading.mem_available - sum(r.mem for r in reservations)
            cpus = reading.cpus - sum(r.cpus for r in reservations)
            need = max(headroom, res.mem) if device.name.startswith("groot-") else res.mem
            if mem < need:
                reasons.append("insufficient MemAvailable after reservations")
            if cpus < res.cpus:
                reasons.append("insufficient CPUs after reservations")
        if device.device_class == "gpu":
            if not _integer(reading.gpu_free, 1):
                reasons.append("GPU 0 free memory unavailable")
            elif reading.gpu_free - sum(r.gpu_mem for r in reservations) < res.gpu_mem:
                reasons.append("insufficient GPU 0 memory after reservations")
    else:
        reasons.append("unknown device")
    return Admission(not reasons, tuple(reasons), owner_state, reading.focus)


def scope_name(unique_token: str) -> str:
    if not isinstance(unique_token, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", unique_token):
        raise ValueError("invalid owned scope token")
    return "hc-turbo-" + unique_token + ".scope"


def launch_command(device: Device, spec: Any, command: Iterable[str],
                   run_dir: str | Path, unique_token: str) -> list[str]:
    """Bound each host worker, including the phone referee's quality scorer.

    Phone resources are the HOST-worker memory/CPU budget; benchmark candidate
    thread counts on the phone are unchanged. The dispatcher reserves this host
    budget alongside Groot work before launch. Phone workers cannot use CUDA.
    """
    command = list(command)
    if not command or any(not isinstance(a, str) or "\0" in a for a in command):
        raise ValueError("invalid referee argv")
    if device.name in ("groot-cpu", "groot-gpu"):
        gpu = ["env", "CUDA_VISIBLE_DEVICES=0"] if device.device_class == "gpu" else []
        return ["systemd-run", "--user", "--scope", "--unit", scope_name(unique_token),
                "-p", f"MemoryMax={spec.resources.mem}",
                "-p", f"CPUQuota={spec.resources.cpus * 100}%", "--", *gpu, *command]
    if device.name == "mbp":
        run_dir = Path(run_dir).resolve()
        return ["fleet-build", "--node", "mbp", "--class", "bench", "--checkpoint-safe",
                "--mem", str(spec.resources.mem), "--cpus", str(spec.resources.cpus),
                "--timeout", str(int(math.ceil(spec.max_runtime))), "--wait", "0",
                "--src", str(run_dir / "work"), "--fetch", "output", "--out", str(run_dir / "remote"),
                "--", *command]
    if device.device_class == "phone" and device.name in PHONES:
        if Path(command[0]).name == "adb":
            raise ValueError("phone work must use the reviewed mobile worker")
        return ["systemd-run", "--user", "--scope", "--unit", scope_name(unique_token),
                "-p", f"MemoryMax={spec.resources.mem}",
                "-p", f"CPUQuota={spec.resources.cpus * 100}%", "--",
                "env", "CUDA_VISIBLE_DEVICES=", *command]
    raise ValueError("unknown device")


def fleet_job_id(log: str) -> str | None:
    matches = set(re.findall(r"^fleet: job (fleet-mbp-[0-9a-f]{16}) -> mbp \(Kueue bench-lanes\);",
                             log, re.MULTILINE))
    if len(matches) > 1:
        raise ValueError("multiple remote job identities in one owned launch")
    return next(iter(matches), None)


def cancel_command(job_id: str) -> list[str]:
    if not isinstance(job_id, str) or not re.fullmatch(r"fleet-mbp-[0-9a-f]{16}", job_id):
        raise ValueError("invalid owned MBP job id")
    return ["fleet", "kill", "mbp", job_id]


def retryable_admission_exit(returncode: int, log: str) -> bool:
    """Recognize fleet-build's explicit no-admission receipt, not arbitrary 75.

    Once any Job identity or allocation marker exists, an observer's exit must
    go through exact-job cancellation/recovery before the slot is reusable.
    """
    if returncode != 75 or not isinstance(log, str):
        return False
    if ("fleet-mbp-" in log
            or re.search(r"^fleet: (?:job |Kueue admitted |detached job |attached )", log, re.MULTILINE)):
        return False
    marker = ("fleet-build: no worker has room now (exit 75). Retry later or lower --mem; "
              "do not queue it on groot.")
    return marker in log.splitlines()


def fleet_terminal_exit(returncode: int, log: str, job_id: str) -> bool:
    """A trusted observer-stderr terminal receipt proves this Job stopped.

    The benchmark exit may differ from the observer exit if subsequent artifact
    cleanup fails. Either way, a terminal Job must not be cancelled again merely
    because the observer returned nonzero. Only pass the observer's stderr here;
    fleet forwards benchmark logs to stdout, which is not an identity authority.
    """
    if (not isinstance(returncode, int) or isinstance(returncode, bool)
            or not isinstance(log, str) or not isinstance(job_id, str)
            or not re.fullmatch(r"fleet-mbp-[0-9a-f]{16}", job_id)):
        return False
    submitted = re.search(rf"^fleet: job {re.escape(job_id)} -> mbp \(Kueue bench-lanes\);",
                          log, re.MULTILINE)
    terminals = list(re.finditer(rf"^fleet: job {re.escape(job_id)} on mbp exited (\d+)$",
                                 log, re.MULTILINE))
    # fleet-kueue prints "exited 75" after an expired admission wait too, but
    # leaves the Job queued; it later ran orphaned (two hc-1 copies at once).
    if re.search(rf"^fleet: admission wait expired for {re.escape(job_id)}$", log, re.MULTILINE):
        return False
    return (submitted is not None and len(terminals) == 1
            and terminals[0].start() > submitted.end()
            and 0 <= int(terminals[0][1]) <= 255)
