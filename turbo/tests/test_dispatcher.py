"""Scheduler regressions: fake processes, fake clocks and fake device readings."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from turbo.devices import Device, GIB, Reading, default_devices
from turbo.host import atomic_json
from turbo.turbo import Dispatcher, main


class FakeQueue:
    def __init__(self, state, specs):
        self.state_dir = state
        self.specs = {s.id: s for s in specs}
        self.history = []

    def reload(self):
        pass

    def ranked(self, exclude=()):
        return [s for s in self.specs.values() if s.id not in exclude]

    def rows(self):
        return list(self.history)

    def append(self, entry):
        self.history.append(entry)

    def council_flags(self):
        return {}

    def fail_cooldown(self, device, identifier, now):
        return 0.0


class FakeProcess:
    next_pid = 8000

    def __init__(self, command, **kwargs):
        FakeProcess.next_pid += 1
        self.pid = FakeProcess.next_pid
        self.command = command
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class FakeProbe:
    def __init__(self):
        self.calls = []
        self.phone_changes = {}
        self.mem = 64 * GIB

    def read(self, device):
        self.calls.append(device.name)
        if device.device_class == "phone":
            base = Reading(100, battery_pct=70, charging=False, ap_c=39, battery_c=35,
                           owner_idle_s=120, awake=True, call_state=0, audio_mode=0, focus="opaque")
            return replace(base, **self.phone_changes)
        return Reading(100, mem_available=self.mem, cpus=32, gpu_free=32 * GIB)


def specification(identifier, kind="cpu", **changes):
    values = dict(id=identifier, device_class=kind, devices=(), source=Path("kernel.toml"),
                  resources=SimpleNamespace(mem=GIB, cpus=1, gpu_mem=GIB if kind == "gpu" else 0),
                  oracle_status="ready", direction="lower", max_runtime=300,
                  referee=("python3", "{repo}/turbo/mobile.py", "--track", "tts/pocket"),
                  oracle={"kind": "mobile", "track": "tts/pocket"})
    values.update(changes)
    return SimpleNamespace(**values)


def prepare(spec, repo, directory):
    (directory / "work").mkdir()
    return ["python3", "-m", "turbo.host", "--spec", "kernel.toml", "--run-dir", "output"]


def make_dispatcher(tmp_path, specs=None, devices=None, **kwargs):
    specs = specs or [specification(f"gpu-{i}", "gpu") for i in range(3)] + [
        specification(f"cpu-{i}") for i in range(5)] + [
        specification(f"phone-{i}", "phone") for i in range(3)]
    queue = FakeQueue(tmp_path, specs)
    return Dispatcher(queue, devices or default_devices("fake-s25", "fake-s22", "fake-tab"),
                      probe=kwargs.pop("probe", FakeProbe()), popen=FakeProcess,
                      clock=lambda: 100, wall_clock=lambda: 1000,
                      phone_finalize=kwargs.pop("phone_finalize", lambda *args: None),
                      prepare=kwargs.pop("prepare", prepare),
                      bench_busy=kwargs.pop("bench_busy", lambda name: None), **kwargs)


def complete(dispatcher, run, **changes):
    receipt = {"decision": "KEEP", "ci": {"lo": .7, "mid": .8, "hi": .9},
               "oracle_passed": True, "owner_state": "owner_idle", "cleanup_verified": True, **changes}
    atomic_json(dispatcher._result_path(run), receipt)
    if run.device.name == "mbp":
        job = "fleet-mbp-" + run.token[:16]
        run.stderr.write(f"fleet: job {job} -> mbp (Kueue bench-lanes); log x; stage x\n")
        run.stderr.write(f"fleet: job {job} on mbp exited 0\n")
        run.stderr.flush()
    run.process.returncode = 0


def test_fills_every_admitted_slot_without_exceeding_caps(tmp_path):
    dispatcher = make_dispatcher(tmp_path)
    report = dispatcher.tick()
    expected = {"groot-gpu": 2, "groot-cpu": 1, "mbp": 3, "s25": 1, "s22": 1, "tab": 1}
    assert Counter(r.device.name for r in dispatcher.active.values()) == expected
    assert {k: v["slots_used"] for k, v in report["devices"].items()} == expected
    # Selection + post-staging admission, including a host read for each phone.
    assert len(dispatcher.probe.calls) == 24
    dispatcher.tick()
    assert len(dispatcher.active) == 9
    for run in dispatcher.active.values():
        run.log.close()
        run.stderr.close()


def test_stop_file_drains_current_work_and_never_launches_replacements(tmp_path):
    dispatcher = make_dispatcher(tmp_path)
    dispatcher.tick()
    before = set(dispatcher.active)
    (tmp_path / "STOP").touch()
    dispatcher.tick()
    assert set(dispatcher.active) == before
    assert all(r.cancelled is None for r in dispatcher.active.values())
    for run in list(dispatcher.active.values()):
        complete(dispatcher, run, decision="NOISE", ci=None)
    report = dispatcher.tick()
    assert not dispatcher.active and len(dispatcher.queue.history) == 9
    assert report["draining"] is True
    assert all(v["runs_per_hour"] == v["capacity"] for v in report["devices"].values())


def test_final_admission_rechecks_resources_after_expensive_staging(tmp_path):
    probe = FakeProbe()

    def staging(spec, repo, directory):
        command = prepare(spec, repo, directory)
        probe.mem = 5 * GIB
        return command

    dispatcher = make_dispatcher(tmp_path, [specification("cpu")],
        [Device("groot-cpu", "cpu", 1)], probe=probe, prepare=staging)
    report = dispatcher.tick()
    assert not dispatcher.active and len(probe.calls) == 2
    assert "launch held" in report["devices"]["groot-cpu"]["skip_reasons"][0]


def test_stop_during_preparation_prevents_process_launch(tmp_path):
    def staging(spec, repo, directory):
        command = prepare(spec, repo, directory)
        (tmp_path / "STOP").touch()
        return command

    dispatcher = make_dispatcher(tmp_path, [specification("cpu")],
        [Device("groot-cpu", "cpu", 1)], prepare=staging)
    dispatcher.tick()
    assert not dispatcher.active and dispatcher.draining


def test_pending_fixture_keep_is_downgraded_in_dispatcher_ledger(tmp_path):
    pending = specification("hc-2", oracle_status="pending-fixture")
    dispatcher = make_dispatcher(tmp_path, [pending], [Device("groot-cpu", "cpu", 1)])
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    complete(dispatcher, run)
    dispatcher.tick(fill=False)
    assert dispatcher.queue.history[0]["decision"] == "NOISE"
    assert "pending-fixture" in dispatcher.queue.history[0]["reason"]


def test_owner_in_use_receipt_never_becomes_keep(tmp_path):
    finalized = []
    dispatcher = make_dispatcher(tmp_path, [specification("phone", "phone")],
                                  [Device("s25", "phone", 1, "fake-s25")],
                                  phone_finalize=lambda *args: finalized.append(args))
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    complete(dispatcher, run, owner_state="owner_in_use")
    dispatcher.tick(fill=False)
    row = dispatcher.queue.history[0]
    assert row["decision"] == "NOISE" and row["owner_state"] == "owner_in_use"
    assert finalized[0][-1] == "NOISE"  # kept and labelled, never KEEP


def test_final_owner_activity_labels_but_keeps_the_baseline(tmp_path):
    finalized = []
    dispatcher = make_dispatcher(tmp_path, [specification("phone", "phone")],
                                  [Device("s25", "phone", 1, "fake-s25")],
                                  phone_finalize=lambda *args: finalized.append(args))
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    complete(dispatcher, run, decision="NOISE", ci=None)
    dispatcher.probe.phone_changes = {"owner_idle_s": 0}
    dispatcher.tick(fill=False)
    assert dispatcher.queue.history[0]["owner_state"] == "owner_in_use"
    assert finalized[0][-1] == "NOISE"


def test_phone_host_resources_share_groot_reservations(tmp_path):
    phone = specification("phone", "phone", resources=SimpleNamespace(mem=8 * GIB, cpus=2, gpu_mem=0))
    cpu = specification("cpu", resources=SimpleNamespace(mem=4 * GIB, cpus=1, gpu_mem=0))
    probe = FakeProbe()
    probe.mem = 12 * GIB
    dispatcher = make_dispatcher(tmp_path, [phone, cpu],
        [Device("s25", "phone", 1, "fake-s25"), Device("groot-cpu", "cpu", 1)], probe=probe)
    report = dispatcher.tick()
    run, = dispatcher.active.values()
    assert run.spec.id == "phone"
    assert "insufficient MemAvailable" in report["devices"]["groot-cpu"]["skip_reasons"][0]
    assert "MemoryMax=8589934592" in run.command
    assert "CUDA_VISIBLE_DEVICES=" in run.command
    run.log.close()
    run.stderr.close()


def test_safe_phone_is_held_when_host_quality_budget_is_unavailable(tmp_path):
    phone = specification("phone", "phone", resources=SimpleNamespace(mem=8 * GIB, cpus=1, gpu_mem=0))
    probe = FakeProbe()
    probe.mem = 7 * GIB
    dispatcher = make_dispatcher(tmp_path, [phone], [Device("s25", "phone", 1, "fake-s25")], probe=probe)
    report = dispatcher.tick()
    assert not dispatcher.active
    assert "phone host worker: insufficient MemAvailable" in report["devices"]["s25"]["skip_reasons"][0]


def test_owner_focus_change_keeps_adb_cli_run_going(tmp_path, monkeypatch):
    # Owner rule 2026-11-10: CLI hill climbs never touch display 0.
    dispatcher = make_dispatcher(tmp_path, [specification("phone", "phone")],
                                  [Device("s22", "phone", 1, "fake-s22")],
                                  runner=lambda *args, **kwargs: SimpleNamespace(returncode=0))
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    initial = dispatcher.probe.read(run.device)
    dispatcher.probe.read = lambda device: replace(initial, focus="changed")
    killed = []
    monkeypatch.setattr("turbo.turbo.os.killpg", lambda pid, sig: killed.append((pid, sig)))
    dispatcher.tick(fill=False)
    assert killed == [] and run.cancelled is None
    assert "s22" not in dispatcher.phone_hold


def test_sigterm_cancels_exact_mbp_job_and_owned_observer(tmp_path, monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    dispatcher = make_dispatcher(tmp_path, [specification("cpu")], [Device("mbp", "cpu", 3)], runner=runner)
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    job = "fleet-mbp-0123456789abcdef"
    run.stderr.write(f"fleet: job {job} -> mbp (Kueue bench-lanes); log x; stage x\n")
    run.stderr.flush()
    killed = []
    monkeypatch.setattr("turbo.turbo.os.killpg", lambda pid, sig: killed.append(pid))
    dispatcher.terminate = True
    dispatcher.tick()
    assert calls == [["fleet", "kill", "mbp", job]]
    assert killed == [run.process.pid]
    assert not dispatcher.active and dispatcher.draining


def test_mbp_observer_failure_without_job_identity_holds_new_work(tmp_path):
    dispatcher = make_dispatcher(tmp_path, [specification("cpu")], [Device("mbp", "cpu", 3)])
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    run.process.returncode = 75
    report = dispatcher.tick()
    assert not dispatcher.active and dispatcher.remote_hold
    assert "without an owned job identity" in report["devices"]["mbp"]["skip_reasons"][0]


def test_unverified_phone_cleanup_holds_device_across_restart(tmp_path):
    config = specification("phone", "phone")
    device = Device("s25", "phone", 1, "fake-s25")
    dispatcher = make_dispatcher(tmp_path, [config], [device])
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    complete(dispatcher, run, cleanup_verified=False)
    dispatcher.tick()
    assert not dispatcher.active and dispatcher.queue.history[0]["decision"] == "FAIL"
    restarted = make_dispatcher(tmp_path, [config], [device])
    restarted.tick()
    assert not restarted.active and "s25" in restarted.phone_hold


def test_missing_receipt_cannot_keep(tmp_path):
    dispatcher = make_dispatcher(tmp_path, [specification("cpu")], [Device("groot-cpu", "cpu", 1)])
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    run.process.returncode = 0
    dispatcher.tick(fill=False)
    assert dispatcher.queue.history[0]["decision"] == "FAIL"


def test_busy_unseen_spec_does_not_idle_otherwise_available_gpu(tmp_path):
    gpu = specification("gpu", "gpu")
    blocked = specification("too-large", "gpu", resources=SimpleNamespace(mem=GIB, cpus=1, gpu_mem=64 * GIB))
    cpu = specification("long-cpu")
    dispatcher = make_dispatcher(tmp_path, [gpu, blocked, cpu],
                                  [Device("groot-gpu", "gpu", 2), Device("groot-cpu", "cpu", 1)])
    dispatcher.tick()
    first = next(r for r in dispatcher.active.values() if r.spec.id == "gpu")
    complete(dispatcher, first, decision="NOISE")
    dispatcher.tick()
    assert {r.spec.id for r in dispatcher.active.values()} == {"gpu", "long-cpu"}
    assert all(r.token != first.token for r in dispatcher.active.values())
    for run in dispatcher.active.values():
        run.log.close()
        run.stderr.close()


def test_terminal_failed_mbp_job_releases_slot_without_cancelling_deleted_job(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("a completed/fetched job must not be cancelled")

    dispatcher = make_dispatcher(tmp_path, [specification("cpu")], [Device("mbp", "cpu", 3)], runner=forbidden)
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    job = "fleet-mbp-" + run.token[:16]
    run.stderr.write(f"fleet: job {job} -> mbp (Kueue bench-lanes); log x; stage x\n")
    run.stderr.write(f"fleet: job {job} on mbp exited 1\n")
    run.stderr.flush()
    run.process.returncode = 1
    dispatcher.tick(fill=False)
    assert dispatcher.remote_hold is None
    assert dispatcher.queue.history[0]["decision"] == "FAIL"


def test_cli_stop_and_status_do_not_probe_or_launch(tmp_path, capsys):
    assert main(["stop", "--state", str(tmp_path)]) == 0
    assert (tmp_path / "STOP").exists()
    atomic_json(tmp_path / "status.json", {"draining": True})
    assert main(["status", "--state", str(tmp_path)]) == 0
    assert '"draining": true' in capsys.readouterr().out


def test_completion_appends_to_real_queue_with_timestamp_and_evidence(tmp_path):
    from turbo.queue import Queue

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "kernel.toml").write_text('''id = "hc-test"
objective = "speed"
metric = "runtime_seconds"
device_class = "cpu"
devices = ["groot-cpu"]
referee = ["python3", "referee.py"]
max_runtime = 300
[resources]
mem = 1073741824
cpus = 1
[oracle]
kind = "receipt"
check = "fixed referee equivalence"
checks = ["equivalent"]
''')
    queue = Queue(specs, tmp_path / "state")
    dispatcher = Dispatcher(queue, [Device("groot-cpu", "cpu", 1)], bench_busy=lambda name: None,
        probe=FakeProbe(), popen=FakeProcess, clock=lambda: 100,
        wall_clock=lambda: 1000, prepare=prepare)
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    complete(dispatcher, run)
    report = dispatcher.tick(fill=False)
    row, = queue.rows()
    assert row["decision"] == "KEEP" and row["at"] == 1000
    assert row["oracle_passed"] is True
    assert report["devices"]["groot-cpu"]["runs_per_hour"] == 1
    assert (queue.state_dir / "ledger.jsonl").read_text().endswith("\n")


def test_phone_busy_with_another_bench_loop_is_skipped_not_failed(tmp_path):
    dispatcher = make_dispatcher(tmp_path, bench_busy=lambda name: "lock held" if name == "s25" else None)
    dispatcher.fill()
    assert not any(run.device.name == "s25" for run in dispatcher.active.values())
    assert dispatcher.skips["s25"] == ["lock held"]
    assert any(run.device.name == "s22" for run in dispatcher.active.values())


def test_a_held_phone_clears_itself_once_owned_cleanup_verifies(tmp_path):
    calls = []
    def check(serial):
        calls.append(serial)
        if len(calls) == 1:
            raise RuntimeError("phone unreachable")
    (tmp_path / "state").mkdir(exist_ok=True)
    dispatcher = make_dispatcher(tmp_path, [specification("phone", "phone")],
                                 [Device("s25", "phone", 1, "fake-s25")], phone_hold_check=check)
    dispatcher.phone_hold["s25"] = "owned phone cleanup unverified; inspect before clearing hold"
    dispatcher.retry_phone_holds(every=0)
    assert "s25" in dispatcher.phone_hold
    dispatcher.retry_phone_holds(every=0)
    assert "s25" not in dispatcher.phone_hold and calls == ["fake-s25", "fake-s25"]


def test_clear_owned_phone_kills_only_dispatcher_paths_and_verifies():
    from turbo.turbo import clear_owned_phone
    seen, ps = [], ["  PID ARGS\n 4242 /data/local/tmp/hc-turbo/abc/bin/base/audiocpp_cli\n 77 /system/bin/surfaceflinger\n",
                    "  PID ARGS\n 77 /system/bin/surfaceflinger\n"]
    def runner(argv, **kw):
        script = argv[-1]; seen.append(script)
        out = ps[0] if script.startswith("ps") and len([x for x in seen if x.startswith("ps")]) == 1 else (ps[1] if script.startswith("ps") else "")
        return SimpleNamespace(returncode=0, stdout=out)
    clear_owned_phone("fake-s25", runner)
    assert "kill -TERM 4242 2>/dev/null || true" in seen
    assert not any(" 77" in s for s in seen if s.startswith("kill"))
    assert seen[-1].startswith("rm -rf /data/local/tmp/hc-turbo/*")


def test_editing_an_active_spec_keeps_its_contract_instead_of_exiting(tmp_path):
    dispatcher = make_dispatcher(tmp_path, [specification("phone", "phone")],
                                 [Device("s25", "phone", 1, "fake-s25")])
    dispatcher.tick()
    run = next(iter(dispatcher.active.values()))
    original = dispatcher.queue.reload
    def edited():
        original()
        dispatcher.queue.specs[run.spec.id] = SimpleNamespace(**{**vars(run.spec), "devices": ("s25", "tab")}) \
            if hasattr(run.spec, "__dict__") else run.spec
    dispatcher.queue.reload = edited
    dispatcher.tick()
    assert dispatcher.queue.specs[run.spec.id] == run.spec
    run.log.close(); run.stderr.close()
