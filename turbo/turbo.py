#!/usr/bin/env python3
"""Fill admitted fleet slots; STOP drains, SIGTERM cancels owned work."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from turbo import devices as hardware
from turbo.host import atomic_json, checked_ci, stage
from turbo.queue import Queue


ROOT = Path(__file__).resolve().parents[1]


def finalize_phone(directory, state, device, decision):
    from turbo.mobile import finalize
    finalize(directory, state, device, decision)


@dataclass
class Run:
    token: str
    spec: object
    device: object
    process: object
    directory: Path
    command: list
    log: object
    stderr: object
    started: float
    owner_state: str | None
    focus: str | None
    cancelled: str | None = None


class Dispatcher:
    def __init__(self, queue, devices, probe=None, repo=ROOT, *, popen=subprocess.Popen,
                 runner=subprocess.run, clock=time.monotonic, wall_clock=time.time,
                 prepare=stage, phone_finalize=finalize_phone, interval=5.0):
        self.queue = queue
        self.devices = tuple(devices)
        self.probe = probe or hardware.Probe()
        self.repo = Path(repo).resolve()
        self.state = Path(queue.state_dir).resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.popen, self.runner = popen, runner
        self.clock, self.wall_clock, self.prepare = clock, wall_clock, prepare
        self.phone_finalize = phone_finalize
        self.interval = interval
        self.active = {}
        self.seen = set()
        self.last = {}
        self.skips = {}
        holds_path = self.state / "holds.json"
        holds = json.loads(holds_path.read_text()) if holds_path.exists() else {}
        if (not isinstance(holds, dict) or set(holds) - {"mbp", "s25", "s22", "tab"}
                or any(not isinstance(v, str) or not v for v in holds.values())):
            raise ValueError("invalid persistent device holds")
        self.remote_hold = holds.get("mbp")
        self.phone_hold = {name: reason for name, reason in holds.items() if name in ("s25", "s22", "tab")}
        self.terminate = False
        self.draining = False

    def eligible(self, spec, device):
        return (spec.device_class == device.device_class
                and (not spec.devices or device.name in spec.devices))

    def reservations(self, device):
        if device.name.startswith("groot-"):
            # Phone quality scoring runs on this host in its own bounded scope.
            return [r.spec.resources for r in self.active.values()
                    if r.device.name.startswith("groot-") or r.device.device_class == "phone"]
        return [r.spec.resources for r in self.active.values() if r.device.name == device.name]

    def admission(self, spec, device, previous_focus=None):
        result = hardware.admit(device, spec, self.probe.read(device),
                                reserved_resources=self.reservations(device),
                                previous_focus=previous_focus, now=self.clock())
        if device.device_class != "phone" or not result.admitted:
            return result
        # The phone's host process includes the original quality scorer. Its
        # resource budget is reserved with Groot jobs, never silently on CUDA.
        host = hardware.Device("groot-cpu", "cpu", 1)
        host_spec = SimpleNamespace(device_class="cpu", devices=("groot-cpu",),
                                    resources=spec.resources)
        host_result = hardware.admit(host, host_spec, self.probe.read(host),
                                     reserved_resources=self.reservations(host), now=self.clock(),
                                     headroom=hardware.PHONE_HOST_HEADROOM)
        return hardware.Admission(host_result.admitted,
            tuple("phone host worker: " + reason for reason in host_result.reasons),
            result.owner_state, result.focus)

    def _phone_command(self, spec, device, directory):
        # No arbitrary command supplied by a phone spec may bypass this adapter.
        expected = ("python3", "{repo}/turbo/mobile.py", "--track", spec.oracle["track"])
        if tuple(spec.referee) != expected:
            raise ValueError("phone specs must use the controlled mobile adapter")
        return [sys.executable, str(self.repo / "turbo/mobile.py"), "--track", spec.oracle["track"],
                "--device", device.name, "--serial", device.serial,
                "--run-dir", str(directory), "--state-dir", str(self.state / "mobile")]

    def launch(self, spec, device, admission):
        token = uuid.uuid4().hex
        directory = self.state / "runs" / token
        directory.mkdir(parents=True)
        if device.device_class == "phone":
            command = self._phone_command(spec, device, directory)
            command = hardware.launch_command(device, spec, command, directory, token)
            cwd = self.repo
        else:
            command = self.prepare(spec, self.repo, directory)
            command = hardware.launch_command(device, spec, command, directory, token)
            cwd = directory / "work"
        # Copying source may take minutes: admission is measured again at the
        # actual launch boundary, and never reused from queue selection.
        admission = self.admission(spec, device, admission.focus)
        if not admission.admitted:
            raise ValueError("launch held: " + "; ".join(admission.reasons))
        if self.terminate or (self.state / "STOP").exists():
            self.draining = True
            raise ValueError("STOP requested during preparation")
        log = (directory / "process.log").open("w")
        stderr = (directory / "process.stderr.log").open("w")
        try:
            process = self.popen(command, cwd=cwd, stdout=log, stderr=stderr,
                                 start_new_session=True)
        except BaseException:
            log.close()
            stderr.close()
            raise
        run = Run(token, spec, device, process, directory, command, log, stderr, self.clock(),
                  admission.owner_state, admission.focus)
        self.active[token] = run
        self.seen.add(spec.id)
        # The process receipt is diagnostic; only this live parent owns PIDs.
        atomic_json(directory / "process.json", {"pid": process.pid, "id": spec.id,
                    "device": device.name, "token": token, "cmd": command})
        return run

    def _remote_cancel(self, run):
        # Remote workload logs are relayed on stdout. Only the fleet observer's
        # separate stderr can establish an owned allocation identity.
        text = (run.directory / "process.stderr.log").read_text(errors="replace")
        job = hardware.fleet_job_id(text)
        if job is None:
            return False
        try:
            outcome = self.runner(hardware.cancel_command(job), capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.remote_hold = "MBP owned-job cancellation could not be confirmed"
            return False
        if outcome.returncode:
            self.remote_hold = "MBP cancellation failed for owned job " + job
            return False
        atomic_json(run.directory / "cancel.json", {"job_id": job, "cancelled": True})
        return True

    def cancel(self, run, reason):
        """Never scan process names or recover a stale PID from a status file."""
        if run.cancelled:
            return
        run.cancelled = reason
        remote_cancelled = False
        if run.device.name == "mbp":
            try:
                remote_cancelled = self._remote_cancel(run)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.remote_hold = "MBP owned-job cancellation could not be confirmed"
        if run.process.poll() is None:
            try:
                os.killpg(run.process.pid, signal.SIGTERM)
                run.process.wait(timeout=60 if run.device.device_class == "phone" else 8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(run.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                run.process.wait(timeout=5)
            except ProcessLookupError:
                pass
        if run.device.name.startswith("groot-") or run.device.device_class == "phone":
            try:
                outcome = self.runner(["systemctl", "--user", "stop", hardware.scope_name(run.token)],
                                      capture_output=True, text=True, timeout=15)
                # An already-removed transient scope is harmless; the process
                # group above was qualified by our live Popen handle.
            except (OSError, subprocess.SubprocessError):
                pass
        if run.device.name == "mbp" and not remote_cancelled:
            try:
                remote_cancelled = self._remote_cancel(run)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            if not remote_cancelled:
                self.remote_hold = "MBP observer stopped; remote termination unverified; drain and inspect owned receipt"

    def _result_path(self, run):
        if run.device.device_class == "phone":
            return run.directory / "result.json"
        if run.device.name == "mbp":
            return run.directory / "remote" / "output" / "result.json"
        return run.directory / "work" / "output" / "result.json"

    def finish(self, run):
        run.log.close()
        run.stderr.close()
        result = {"decision": "FAIL", "ci": None, "oracle_passed": False}
        try:
            if run.cancelled:
                raise ValueError(run.cancelled)
            if run.device.name == "mbp":
                # Even rc75 may leave a queued job; a terminal failed benchmark
                # has already been fetched/deleted and must not be cancelled.
                text = (run.directory / "process.stderr.log").read_text(errors="replace")
                try:
                    job = hardware.fleet_job_id(text)
                except ValueError:
                    self.remote_hold = "MBP observer reported ambiguous job ownership"
                    raise
                if job and not hardware.fleet_terminal_exit(run.process.returncode, text, job):
                    if not self._remote_cancel(run):
                        self.remote_hold = "MBP nonzero exit left an unconfirmed owned job"
                    raise ValueError("MBP observer ended without terminal evidence")
                if job is None:
                    if not hardware.retryable_admission_exit(run.process.returncode, text):
                        self.remote_hold = "MBP observer failed without an owned job identity; inspect before restart"
                    raise ValueError("MBP admission did not produce an owned terminal job")
            if run.process.returncode:
                raise ValueError(f"process exited {run.process.returncode}")
            value = json.loads(self._result_path(run).read_text())
            if not isinstance(value, dict) or value.get("decision") not in ("KEEP", "REVERT", "NOISE", "FAIL"):
                raise ValueError("invalid result receipt")
            ci = checked_ci(value["ci"]) if value.get("ci") is not None else None
            result.update(decision=value["decision"], ci=ci,
                          oracle_passed=value.get("oracle_passed") is True,
                          reason=str(value.get("reason", "")))
            if run.device.device_class == "phone":
                if value.get("owner_state") != "owner_idle":
                    run.owner_state = "owner_in_use"
                final = hardware.admit(run.device, run.spec, self.probe.read(run.device),
                                       previous_focus=run.focus, now=self.clock())
                if not final.admitted:
                    result.update(decision="FAIL", reason="; ".join(final.reasons))
                if final.owner_state != "owner_idle":
                    run.owner_state = "owner_in_use"
            if result["decision"] == "KEEP":
                if run.spec.oracle_status != "ready":
                    result.update(decision="NOISE", reason="oracle_status=" + run.spec.oracle_status)
                elif run.device.device_class == "phone" and run.owner_state != "owner_idle":
                    result.update(decision="NOISE", reason="owner_in_use cannot KEEP")
                elif not result["oracle_passed"] or ci is None:
                    result.update(decision="FAIL", reason="KEEP lacks oracle/CI evidence")
                elif (run.spec.direction == "lower" and ci["hi"] >= 1
                      or run.spec.direction == "higher" and ci["lo"] <= 1):
                    result.update(decision="NOISE", reason="CI does not establish improvement")
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            result.update(decision="FAIL", reason=str(exc))
        if run.device.device_class == "phone":
            try:
                cleanup = json.loads(self._result_path(run).read_text()).get("cleanup_verified") is True
            except (OSError, ValueError, AttributeError):
                cleanup = False
            if not cleanup:
                self.phone_hold[run.device.name] = "owned phone cleanup unverified; inspect before clearing hold"
                result.update(decision="FAIL", reason=self.phone_hold[run.device.name])
            try:
                # Activity can begin after the worker exits. In that case even
                # baseline/noise calibration must not mutate durable state.
                final_decision = result["decision"] if run.owner_state == "owner_idle" else "FAIL"
                self.phone_finalize(run.directory, self.state / "mobile", run.device.name, final_decision)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                result.update(decision="FAIL", reason="phone state finalization: " + str(exc))
        entry = {"epoch": 1, "id": run.spec.id, "device": run.device.name,
                 "cmd": run.command, "decision": result["decision"], "ci": result["ci"],
                 "run_dir": str(run.directory), "run": str(run.directory),
                 "at": self.wall_clock(), "oracle_status": run.spec.oracle_status,
                 "oracle_passed": result["oracle_passed"], "reason": result.get("reason", ""),
                 "owner_state": run.owner_state, "cancelled": run.cancelled}
        self.queue.append(entry)
        self.last[run.device.name] = result["decision"]
        del self.active[run.token]

    def reap(self):
        for run in list(self.active.values()):
            if run.process.poll() is None:
                if self.terminate:
                    self.cancel(run, "SIGTERM: cancelled owned work")
                elif self.clock() - run.started >= run.spec.max_runtime:
                    self.cancel(run, "max_runtime exceeded")
                elif run.device.device_class == "phone":
                    try:
                        reading = self.probe.read(run.device)
                        admission = hardware.admit(run.device, run.spec, reading,
                                                   previous_focus=run.focus, now=self.clock())
                        if admission.owner_state != "owner_idle":
                            run.owner_state = "owner_in_use"
                        if not admission.admitted:
                            self.cancel(run, "; ".join(admission.reasons))
                    except (OSError, ValueError, subprocess.SubprocessError):
                        self.cancel(run, "live phone telemetry unavailable")
            if run.process.poll() is not None:
                self.finish(run)

    def fill(self):
        self.skips = {}
        launched_any = False
        active_ids = {run.spec.id for run in self.active.values()}
        if not (set(self.queue.specs) - self.seen - active_ids):
            self.seen.clear()
        for device in self.devices:
            reasons = []
            self.skips[device.name] = reasons
            if device.name == "mbp" and self.remote_hold:
                reasons.append(self.remote_hold)
                continue
            if device.name in self.phone_hold:
                reasons.append(self.phone_hold[device.name])
                continue
            count = sum(r.device.name == device.name for r in self.active.values())
            for _ in range(device.capacity - count):
                if self.terminate or (self.state / "STOP").exists():
                    self.draining = True
                    return
                candidates = [s for s in self.queue.ranked(exclude=active_ids)
                              if self.eligible(s, device)]
                # Unvisited specs go first. When they are blocked, reuse an
                # admitted visited spec instead of idling this free device
                # behind unrelated active work or an offline device.
                candidates.sort(key=lambda spec: spec.id in self.seen)
                launched = False
                for spec in candidates:
                    wait = self.queue.fail_cooldown(device.name, spec.id, self.wall_clock())
                    if wait > 0:
                        reasons.append(f"{spec.id}: cooling down {int(wait)} s after FAIL")
                        continue
                    try:
                        # An initial reading avoids needless staging on a busy
                        # device. launch() re-measures after that staging.
                        admission = self.admission(spec, device)
                        if not admission.admitted:
                            reasons.append(spec.id + ": " + "; ".join(admission.reasons))
                            continue
                        self.launch(spec, device, admission)
                        active_ids.add(spec.id)
                        launched = True
                        launched_any = True
                        break
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        reasons.append(spec.id + ": " + str(exc))
                if not launched:
                    if not candidates:
                        reasons.append("no eligible queued spec")
                    break
        if not launched_any and not self.active:
            # An offline device must not prevent the healthy devices from
            # beginning another round once all their own specs were visited.
            self.seen.clear()

    def status(self):
        now = self.wall_clock()
        rows = self.queue.rows()
        def timestamp(row):
            value = row.get("at", 0)
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if isinstance(value, str) else value

        devices = {}
        for device in self.devices:
            running = [r for r in self.active.values() if r.device.name == device.name]
            history = [r for r in rows if r.get("device") == device.name]
            recent = [r for r in history if 0 <= now - timestamp(r) <= 3600]
            devices[device.name] = {"capacity": device.capacity, "slots_used": len(running),
                "slots": [{"slot": index, "current_id": run.spec.id,
                           "run_dir": str(run.directory), "owner_state": run.owner_state}
                          for index, run in enumerate(running)],
                "current_ids": [r.spec.id for r in running],
                "last_decision": self.last.get(device.name, history[-1]["decision"] if history else None),
                "runs_per_hour": len(recent), "skip_reasons": self.skips.get(device.name, [])}
        report = {"at": now, "pid": os.getpid(), "draining": self.draining,
                  "devices": devices, "council": self.queue.council_flags()}
        holds = {**self.phone_hold, **({"mbp": self.remote_hold} if self.remote_hold else {})}
        atomic_json(self.state / "holds.json", holds)
        report["holds"] = holds
        atomic_json(self.state / "status.json", report)
        return report

    def tick(self, fill=True):
        self.draining = self.draining or self.terminate or (self.state / "STOP").exists()
        self.reap()
        if fill and not self.draining:
            previous = dict(self.queue.specs)
            try:
                self.queue.reload()
            except (OSError, ValueError):
                # Preserve the admitted contracts only for cancellation and
                # their final receipts; the exception prevents new launches.
                self.queue.specs = previous
                raise
            if any(self.queue.specs.get(run.spec.id) != run.spec for run in self.active.values()):
                self.queue.specs = previous
                raise ValueError("an active spec changed; draining is required before changing its contract")
            self.fill()
        return self.status()

    def run(self, once=False):
        first = True
        try:
            while True:
                self.tick(fill=not once or first)
                first = False
                if (once or self.draining) and not self.active:
                    return
                time.sleep(self.interval)
        finally:
            # Python exceptions receive the same owned cleanup as SIGTERM.
            for run in list(self.active.values()):
                self.cancel(run, "dispatcher exiting")
                self.finish(run)
            self.draining = True
            self.status()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status", "once"))
    parser.add_argument("--specs", type=Path, default=ROOT / "turbo/specs")
    parser.add_argument("--state", type=Path, default=ROOT / "turbo/state")
    parser.add_argument("--s25-serial", default=os.environ.get("S25_SERIAL"))
    parser.add_argument("--s22-serial", default=os.environ.get("S22_SERIAL"))
    parser.add_argument("--tab-serial", default=os.environ.get("TAB_SERIAL"))
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args(argv)
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    if args.command == "stop":
        (state / "STOP").touch()
        print("STOP set: draining owned runs")
        return 0
    if args.command == "status":
        path = state / "status.json"
        print(path.read_text() if path.exists() else json.dumps({"state": "not started"}))
        return 0
    if not 0 < args.interval <= 10:
        parser.error("--interval must be greater than zero and at most 10 seconds")
    if (state / "STOP").exists():
        parser.error("STOP exists; remove it explicitly before starting a new wave")
    global_path = Path("/tmp") / f"hc-turbo-{os.getuid()}.lock"
    with global_path.open("a") as global_lock, (state / "dispatcher.lock").open("a") as lock:
        try:
            fcntl.flock(global_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another dispatcher owns the fleet or this state directory")
        fleet = hardware.default_devices(args.s25_serial, args.s22_serial, args.tab_serial)
        dispatcher = Dispatcher(Queue(args.specs, state), fleet, interval=args.interval)

        def terminate(_signum, _frame):
            dispatcher.terminate = True

        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, terminate)
        dispatcher.run(once=args.command == "once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
