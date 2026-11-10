"""Mobile adapter regressions. All device readings and processes are faked."""
from dataclasses import replace
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from turbo import mobile
from turbo.devices import Device, Reading


@pytest.fixture(autouse=True)
def no_external_processes(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("mobile tests must not execute device or host commands")

    monkeypatch.setattr(mobile.subprocess, "run", forbidden)
    monkeypatch.setattr(mobile.subprocess, "Popen", forbidden)
    monkeypatch.setattr(mobile, "LOCK_ROOT", tmp_path)


@pytest.fixture
def bench():
    # Load the real fixed referee and real eval sets, but never run its adb CLI.
    return mobile.load_bench(Path(__file__).resolve().parents[2])


def reading(**changes):
    values = dict(measured_at=time.monotonic(), battery_pct=60, charging=False,
                  ap_c=35.0, battery_c=32.0, owner_idle_s=300.0, awake=True,
                  call_state=0, audio_mode=0, focus="opaque-focus-fixture")
    values.update(changes)
    return Reading(**values)


class FakeProbe:
    def __init__(self, *readings):
        self.readings = list(readings) or [reading()]
        self.calls = 0

    def read(self, device):
        value = self.readings[min(self.calls, len(self.readings) - 1)]
        self.calls += 1
        return replace(value, measured_at=time.monotonic())


def transport(tmp_path, probe=None):
    return mobile.PhoneTransport(SimpleNamespace(), Device("s22", "phone", 1, "TEST-SERIAL"),
                                 tmp_path, probe or FakeProbe(), poll_seconds=0)


@pytest.mark.parametrize("track", tuple(mobile.TRACKS))
def test_all_ten_real_tracks_select_baseline_then_aa_then_candidates(bench, track):
    state = mobile._empty_state()
    phase, base = mobile.select_candidate(bench, track, state)
    assert phase == "baseline"
    assert base["track"] == track
    state["incumbents"][track] = {"cfg": base}
    assert mobile.select_candidate(bench, track, state)[0] == "aa"
    state["noise"][track] = {"ratio_ci": {"lo": .97, "mid": 1, "hi": 1.03},
                             "ratio_halfwidth": .03}
    phase, candidate = mobile.select_candidate(bench, track, state, lambda cfg: True)
    assert phase == "run"
    assert candidate["id"] != base["id"]
    assert candidate["track"] == track


def test_candidate_traversal_remembers_seen_and_stops_at_real_bound(bench):
    track = "asr/moonshine_asr"
    _, base = mobile.select_candidate(bench, track, mobile._empty_state())
    state = mobile._empty_state()
    state["incumbents"][track] = {"cfg": base}
    state["noise"][track] = {"ratio_ci": {"lo": .98, "mid": 1, "hi": 1.02},
                             "ratio_halfwidth": .02}
    ids = []
    for _ in range(10):
        phase, cfg = mobile.select_candidate(bench, track, state, lambda cfg: True)
        if phase == "exhausted":
            break
        assert phase == "run"
        assert cfg["id"] not in ids
        ids.append(cfg["id"])
        state["seen"].setdefault(track, []).append(cfg["id"])
    assert phase == "exhausted"
    assert len(ids) == 3


@pytest.mark.parametrize("field,value", [
    ("variant", "base; input keyevent 3"),
    ("model", "../escape/model.gguf"),
    ("model", "/data/local/tmp/model.gguf"),
    ("assets", ["models/../../other"]),
    ("spec_override", "specs/unsafe name.json"),
    ("cpu_mask", "ff; reboot"),
    ("env", {"BAD;true": "1"}),
    ("id", "id/escape"),
])
def test_shell_interpolated_candidate_fields_are_validated(bench, field, value):
    cfg = bench.cfg_load(bench.ROOT / "candidates" / "phonon-base.json")
    cfg[field] = value
    with pytest.raises(ValueError):
        mobile.validate_config(cfg, "tts/phonon")


@pytest.mark.parametrize("changes", [
    {"call_state": None}, {"audio_mode": None},
    {"focus": None}, {"owner_idle_s": None}, {"awake": None},
    {"call_state": 1}, {"audio_mode": 3},
])
def test_phone_gate_fails_closed_on_missing_or_unsafe_readings(tmp_path, changes):
    phone = transport(tmp_path, FakeProbe(reading(**changes)))
    with pytest.raises(mobile.GateClosed):
        phone.gate()


def test_owner_activity_is_sticky_even_if_phone_returns_idle(tmp_path):
    phone = transport(tmp_path, FakeProbe(reading(owner_idle_s=1), reading()))
    phone.gate()
    phone.gate()
    assert phone.owner_in_use


def test_focus_change_labels_owner_in_use_without_stopping(tmp_path):
    phone = transport(tmp_path, FakeProbe(reading(), reading(focus="different-opaque-token")))
    phone.gate()
    phone.gate()  # keeps running; the label alone blocks a KEEP
    assert phone.owner_in_use


def test_long_running_block_rechecks_call_state_and_stops_host_observer(tmp_path, monkeypatch):
    probe = FakeProbe(reading(), reading(call_state=2))
    phone = transport(tmp_path, probe)

    class Process:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = Process()
    monkeypatch.setattr(mobile.subprocess, "Popen", lambda *a, **k: process)
    with pytest.raises(mobile.GateClosed, match="call state"):
        phone.execute(["fake-observer"], timeout=60)
    assert probe.calls == 2
    assert process.terminated


def test_legacy_broad_kill_and_unreviewed_shell_are_unreachable(bench, tmp_path):
    phone = mobile.PhoneTransport(bench, Device("s25", "phone", 1, "TEST-SERIAL"), tmp_path, FakeProbe())
    original = bench.kill_phone_jobs
    phone.install_adapter()
    assert bench.kill_phone_jobs != original
    assert bench.kill_phone_jobs == phone.cleanup
    for command in ("input keyevent 3", "am start anything", "rm -rf /data/local/tmp/z0live-ar"):
        with pytest.raises(ValueError):
            bench.sh(command)
    assert phone.script.endswith(".sh")
    assert not phone.script.endswith("/block.sh")


def test_adapter_preserves_cli_exit_codes_and_rejects_unreviewed_scripts(bench, tmp_path):
    phone = mobile.PhoneTransport(bench, Device("s25", "phone", 1, "TEST-SERIAL"), tmp_path, FakeProbe())
    phone.install_adapter()
    cfg = bench.cfg_load(bench.ROOT / "candidates" / "phonon-base.json")
    tag = "only-ack-r0"
    wav = phone.remote + "/out/" + tag + ".wav"
    script = bench.phone_block([(tag, bench.cmdline(cfg, bench.EVALSET["tts"][0], wav), wav)])
    assert "rc=$?" in script
    assert tag + ".exit" in script
    assert script in phone.generated_scripts
    path = tmp_path / "unreviewed.sh"
    path.write_text("input keyevent 3\n")
    with pytest.raises(ValueError):
        bench.adb("push", "-q", str(path), phone.remote + "/block.sh")


def test_owned_pid_cleanup_never_targets_shared_basename(tmp_path):
    phone = transport(tmp_path)
    binary = phone.remote + "/bin/base/audiocpp_cli"
    assert phone.owns_argv([binary, "--task", "asr"])
    assert phone.owns_argv(["timeout", "120", binary])
    assert phone.owns_argv(["sh", phone.script])
    assert not phone.owns_argv(["sh", "/data/local/tmp/z0live-ar/block.sh"])
    assert not phone.owns_argv(["/data/local/tmp/other/bin/base/audiocpp_cli"])
    assert not phone.owns_argv(["sh", "-c", "echo " + binary])
    assert not phone.owns_argv(["grep", binary])


def test_cleanup_signals_only_verified_positive_pid(tmp_path, monkeypatch):
    phone = transport(tmp_path)
    phone.has_launched = True
    sequences = iter([[(12345, "987654")], []])
    commands = []
    monkeypatch.setattr(phone, "owned_processes", lambda: next(sequences))
    monkeypatch.setattr(phone, "_raw", lambda command: commands.append(command) or "")
    monkeypatch.setattr(mobile.time, "sleep", lambda delay: None)
    phone.cleanup()
    assert phone.cleanup_verified
    assert len(commands) == 1
    assert "/proc/12345/stat" in commands[0]
    assert "987654" in commands[0]
    assert "kill -TERM 12345" in commands[0]
    assert "kill -TERM -" not in commands[0]


@pytest.mark.parametrize("data", ["", "\nHC_PID\n", "malformed stat\nHC_PID\nsh\0"])
def test_unreadable_matched_pid_requires_proof_of_disappearance(tmp_path, monkeypatch, data):
    phone = transport(tmp_path)
    phone.has_launched = True
    outputs = iter(["PID ARGS\n12345 sh " + phone.script, data, "PRESENT"])
    monkeypatch.setattr(phone, "_raw", lambda command: next(outputs))
    with pytest.raises(RuntimeError, match="unreadable"):
        phone.cleanup()
    assert not phone.cleanup_verified


def test_exited_matched_pid_is_safe_only_after_explicit_disappearance(tmp_path, monkeypatch):
    phone = transport(tmp_path)
    phone.has_launched = True
    outputs = iter(["PID ARGS\n12345 sh " + phone.script, "\nHC_PID\n", "GONE"])
    monkeypatch.setattr(phone, "_raw", lambda command: next(outputs))
    phone.cleanup()
    assert phone.cleanup_verified


def test_teardown_removes_only_unique_tree_including_aborted_staging(tmp_path, monkeypatch):
    phone = transport(tmp_path)
    phone.remote_created = True
    commands = []
    monkeypatch.setattr(phone, "_raw", lambda command: commands.append(command) or "")
    phone.teardown()
    assert commands == [f"rm -rf {phone.remote} && [ ! -e {phone.remote} ]"]
    assert not phone.remote_created
    assert phone.cleanup_verified


def test_teardown_does_not_delete_assets_when_process_ownership_is_uncertain(tmp_path, monkeypatch):
    phone = transport(tmp_path)
    phone.remote_created = phone.has_launched = True

    def unknown():
        raise RuntimeError("ownership unavailable")

    monkeypatch.setattr(phone, "owned_processes", unknown)
    with pytest.raises(RuntimeError):
        phone.teardown()
    assert phone.remote_created
    assert not phone.cleanup_verified


def test_phone_locks_are_nonblocking_and_separate(tmp_path):
    with mobile.phone_lock(tmp_path / "state", "s25"):
        with pytest.raises(BlockingIOError):
            with mobile.phone_lock(tmp_path / "state", "s25"):
                raise AssertionError("duplicate worker acquired the phone")
        with mobile.phone_lock(tmp_path / "state", "s22"):
            pass


def test_incomplete_or_failed_output_cannot_pass_measurement_gate(tmp_path):
    b = SimpleNamespace(EVALSET={"asr": [{"id": "asr01"}]}, OBJ_KEY={"wall": "proc_ms"})
    cfg = {"_slot": "only", "lane": "asr", "objective": "wall"}
    record = {"tag": "only-asr01-r0", "errs": [], "contaminated": False,
              "proc_ms": 100, "audio_s": 1}
    wav = tmp_path / "wav"
    wav.mkdir()
    (wav / "only-asr01-r0.wav").write_text("recognized words\n")
    exit_path = wav / "only-asr01-r0.exit"
    assert not mobile.complete_records(b, [cfg], {"only": [record]}, tmp_path, 1)
    exit_path.write_text("1\n")
    assert not mobile.complete_records(b, [cfg], {"only": [record]}, tmp_path, 1)
    exit_path.write_text("0\n")
    assert mobile.complete_records(b, [cfg], {"only": [record]}, tmp_path, 1)
    assert not mobile.complete_records(b, [cfg], {"only": [record, record]}, tmp_path, 1)
    # Owner rule: owner activity labels the run owner_in_use (KEEP is refused
    # there) but never discards complete adb-only measurements.
    record["contaminated"] = True
    assert mobile.complete_records(b, [cfg], {"only": [record]}, tmp_path, 1)


def test_quality_requires_complete_finite_scores_and_vad_has_no_keep_oracle():
    qual = {"slots": {"inc": {"wer": .1, "mos": 3.5, "n": 12},
                      "cand": {"wer": .1, "mos": 3.5, "n": 12}}}
    assert mobile.quality_complete(qual, "tts", ["inc", "cand"], 12)
    assert not mobile.quality_complete(qual, "vad", ["inc", "cand"], 12)
    assert not mobile.quality_complete(qual, "s2s", ["inc", "cand"], 12)
    qual["slots"]["cand"]["n"] = 11
    assert not mobile.quality_complete(qual, "tts", ["inc", "cand"], 12)
    qual["slots"]["cand"].update(n=12, mos=float("nan"))
    assert not mobile.quality_complete(qual, "tts", ["inc", "cand"], 12)


def proposal_files(tmp_path, bench, phase="run", worker_decision="KEEP", owner_state="owner_idle"):
    track = "asr/moonshine_asr"
    base = bench.cfg_load(bench.ROOT / "candidates" / mobile.TRACKS[track])
    candidate = dict(base, id=base["id"] + "-turbo-t2", threads=2)
    before = mobile._empty_state()
    before["noise"][track] = {"ratio_ci": {"lo": .98, "mid": 1, "hi": 1.02},
                              "ratio_halfwidth": .02}
    if phase != "baseline":
        before["incumbents"][track] = {"cfg": base}
    after = copy.deepcopy(before)
    after["incumbents"][track] = {"cfg": candidate}
    after["seen"][track] = [candidate["id"]]
    run = tmp_path / "run"
    state = tmp_path / "state"
    mobile._write(state / "s25" / "state.json", before)
    mobile._write(run / "mobile-proposal.json", {
        "device": "s25", "safe": True, "phase": phase, "track": track,
        "previous_digest": mobile._digest(before), "state": after, "candidate": candidate,
    })
    mobile._write(run / "result.json", {
        "decision": worker_decision, "owner_state": owner_state,
        "cleanup_verified": True, "oracle_passed": True,
        "ci": {"lo": .7, "mid": .75, "hi": .8},
    })
    return run, state, track, before, candidate


def test_parent_downgrade_keeps_incumbent_but_records_safe_traversal(tmp_path, bench):
    run, state, track, before, candidate = proposal_files(tmp_path, bench)
    mobile.finalize(run, state, "s25", "NOISE")
    saved = json.loads((state / "s25" / "state.json").read_text())
    assert saved["incumbents"] == before["incumbents"]
    assert saved["seen"][track] == [candidate["id"]]
    assert saved["stall"][track]["since_keep"] == 1
    assert not (state / "s22" / "state.json").exists()


def test_parent_keep_commits_once_and_stale_proposal_is_rejected(tmp_path, bench):
    run, state, track, before, candidate = proposal_files(tmp_path, bench)
    mobile.finalize(run, state, "s25", "KEEP")
    saved = json.loads((state / "s25" / "state.json").read_text())
    assert saved["incumbents"][track]["cfg"] == candidate
    mobile.finalize(run, state, "s25", "KEEP")
    assert json.loads((state / "s25" / "state.json").read_text()) == saved
    saved["generation"] += 1
    saved["last_run"] = "different-completed-run"
    mobile._write(state / "s25" / "state.json", saved)
    with pytest.raises(ValueError, match="different state"):
        mobile.finalize(run, state, "s25", "KEEP")
    assert json.loads((state / "s25" / "state.json").read_text()) == saved


@pytest.mark.parametrize("owner_state,parent_decision", [
    ("owner_in_use", "KEEP"), ("owner_idle", "FAIL"),
])
def test_unsafe_or_failed_finalization_never_commits(tmp_path, bench, owner_state, parent_decision):
    run, state, track, before, candidate = proposal_files(tmp_path, bench, owner_state=owner_state)
    mobile.finalize(run, state, "s25", parent_decision)
    assert json.loads((state / "s25" / "state.json").read_text()) == before


def test_safe_baseline_initializes_with_noise_not_keep(tmp_path, bench):
    run, state, track, before, candidate = proposal_files(tmp_path, bench, phase="baseline",
                                                         worker_decision="NOISE")
    mobile.finalize(run, state, "s25", "NOISE")
    saved = json.loads((state / "s25" / "state.json").read_text())
    assert saved["incumbents"][track]["cfg"] == candidate
    assert saved["generation"] == 1


@pytest.mark.parametrize("quality_ready", [True, False])
@pytest.mark.parametrize("owner_idle_s", [300, 1])
def test_real_referee_keep_is_only_a_proposal_until_parent_finalization(
        tmp_path, monkeypatch, bench, quality_ready, owner_idle_s):
    track = "asr/moonshine_asr"
    base = bench.cfg_load(bench.ROOT / "candidates" / mobile.TRACKS[track])
    before = mobile._empty_state()
    before["incumbents"][track] = {"cfg": base, "quality": {"wer": .1, "n": 24}}
    before["noise"][track] = {"ratio_ci": {"lo": .98, "mid": 1, "hi": 1.02},
                              "ratio_halfwidth": .02}
    state = tmp_path / "persistent"
    mobile._write(state / "s25" / "state.json", before)

    def fake_measure(configs, rounds, directory, log=print):
        records = {}
        for cfg in configs:
            slot = cfg["_slot"]
            records[slot] = []
            for round_id in range(rounds):
                for item in bench.EVALSET[cfg["lane"]]:
                    tag = f"{slot}-{item['id']}-r{round_id}"
                    (directory / "wav" / (tag + ".wav")).write_text(item["text"])
                    (directory / "wav" / (tag + ".exit")).write_text("0\n")
                    records[slot].append(dict(tag=tag, item=item["id"], round=round_id,
                                              errs=[], contaminated=False, audio_s=item["duration_s"],
                                              proc_ms=100 if slot == "inc" else 50))
        return records, []

    def fake_quality(directory, slots, lane):
        if not quality_ready:
            return {"status": "error"}
        return {"slots": {slot: {"wer": .1, "n": 24} for slot in slots}}

    monkeypatch.setattr(bench, "measure", fake_measure)
    monkeypatch.setattr(bench, "quality", fake_quality)
    monkeypatch.setattr(mobile.PhoneTransport, "available", lambda self, cfg: True)
    args = SimpleNamespace(track=track, device="s25", serial="TEST-SERIAL",
                           run_dir=str(tmp_path / "run"), state_dir=str(state))
    receipt = mobile.run_worker(args, bench=bench, probe=FakeProbe(reading(owner_idle_s=owner_idle_s)))
    assert receipt["decision"] == ("KEEP" if quality_ready and owner_idle_s >= 90 else "NOISE")
    assert receipt["oracle_passed"] is quality_ready
    assert receipt["owner_state"] == ("owner_idle" if owner_idle_s >= 90 else "owner_in_use")
    assert json.loads((state / "s25" / "state.json").read_text()) == before
    # Simulate the final dispatcher gate withdrawing its KEEP permission.
    mobile.finalize(Path(args.run_dir), state, "s25", "NOISE")
    after = json.loads((state / "s25" / "state.json").read_text())
    assert after["incumbents"] == before["incumbents"]


def test_bench_lock_holder_reports_a_held_legacy_lock(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(mobile, "LOCK_ROOT", tmp_path)
    assert mobile.bench_lock_holder("s25") is None
    with (tmp_path / "s25-bench.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert "s25-bench.lock" in mobile.bench_lock_holder("s25")
    assert mobile.bench_lock_holder("s25") is None


def test_display0_focus_change_labels_but_never_aborts_the_cli_bench(tmp_path):
    # bench_s25.measure raises "ABORT focus changed" when telemetry focus differs
    # across a round; owner rule says CLI runs are only labelled owner_in_use.
    t = transport(tmp_path, FakeProbe(reading(focus="lockscreen"), reading(focus="launcher")))
    before, after = t.telemetry(), t.telemetry()
    assert before["focus"] == after["focus"]
    assert t.owner_in_use


def test_one_slow_probe_under_load_does_not_kill_the_run(tmp_path):
    t = transport(tmp_path, FakeProbe(reading(), Reading(time.monotonic(), error="TimeoutExpired"), reading()))
    first = t.gate()
    assert t.gate() is first  # transient probe timeout reuses the last good reading
    t.gate()


def test_truncated_telephony_dump_under_load_is_a_probe_miss_but_a_real_call_still_closes(tmp_path):
    t = transport(tmp_path, FakeProbe(reading(), reading(call_state=None), reading(call_state=1)))
    first = t.gate()
    assert t.gate() is first
    with pytest.raises(mobile.GateClosed):
        t.gate()


def test_a_long_probe_outage_still_closes_the_gate(tmp_path):
    t = transport(tmp_path, FakeProbe(reading(), Reading(time.monotonic(), error="TimeoutExpired")))
    t.gate()
    t.last_good_at -= mobile.PROBE_GRACE_SECONDS + 1
    with pytest.raises(mobile.GateClosed):
        t.gate()


def test_streaming_tts_prepare_markers_after_first_audio_do_not_make_ttfa_negative(bench):
    events = bench.parse_events("\n".join([
        "EV x START 1000", "EV x LOADED 2000", "EV x LOADED 3000", "EV x FIRST 5000",
        "EV x LOADED 9000", "EV x FIRST 9500", "EV x END 12000 10"]))["x"]
    assert (events["loaded"], events["first"]) == (3000, 5000)


def test_tab_runs_only_the_armv82_build_because_i8mm_builds_sigill_there():
    assert mobile.fit_device({"variant": "base", "id": "x"}, "tab")["variant"] == "armv82"
    assert mobile.fit_device({"id": "x"}, "tab")["variant"] == "armv82"
    assert mobile.fit_device({"variant": "armv86", "id": "x"}, "tab") is None
    assert mobile.fit_device({"variant": "armv86", "id": "x"}, "s25")["variant"] == "armv86"
