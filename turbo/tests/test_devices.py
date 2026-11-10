"""Device admission regression coverage; every process reading is faked."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from turbo.devices import (
    GIB, MIB, Admission, Device, Probe, Reading, adb_command, admit,
    cancel_command, default_devices, fleet_job_id, fleet_terminal_exit, launch_command,
    parse_phone, retryable_admission_exit, scope_name, validate_phone_shell,
)


def spec(kind="cpu", mem=GIB, cpus=1, gpu_mem=0, devices=()):
    return SimpleNamespace(device_class=kind, devices=devices,
                           resources=SimpleNamespace(mem=mem, cpus=cpus, gpu_mem=gpu_mem),
                           max_runtime=60)


def phone_reading(**changes):
    good = Reading(measured_at=100.0, battery_pct=70, charging=False, ap_c=40.0,
                   battery_c=35.0, owner_idle_s=120.0, awake=True,
                   call_state=0, audio_mode=0, focus="opaque-focus-token")
    return replace(good, **changes)


def test_groot_memory_floor_and_shared_reservations():
    dev = Device("groot-cpu", "cpu", 1)
    assert not admit(dev, spec(), Reading(100, mem_available=6 * GIB - 1, cpus=4), now=100).admitted
    assert admit(dev, spec(), Reading(100, mem_available=6 * GIB, cpus=4), now=100).admitted
    gpu_reservation = spec("gpu", mem=2 * GIB, gpu_mem=GIB).resources
    assert not admit(dev, spec(), Reading(100, mem_available=7 * GIB, cpus=4),
                     [gpu_reservation], now=100).admitted


def test_gpu_reservations_and_cpu_capacity_are_enforced():
    dev = Device("groot-gpu", "gpu", 2)
    job = spec("gpu", gpu_mem=4 * GIB)
    live = Reading(100, mem_available=16 * GIB, cpus=4, gpu_free=7 * GIB)
    assert admit(dev, job, live, now=100).admitted
    assert not admit(dev, job, live, [job.resources], now=100).admitted
    assert not admit(dev, job, replace(live, cpus=1), [spec().resources], now=100).admitted


@pytest.mark.parametrize("changes", [
    {"call_state": 1}, {"call_state": 2}, {"audio_mode": 3},
    {"call_state": None}, {"audio_mode": None}, {"focus": None},
    {"owner_idle_s": None}, {"awake": None}, {"measured_at": 0},
])
def test_phone_rejects_unsafe_or_unknown_reading(changes):
    result = admit(Device("s25", "phone", 1, "fake-s25"), spec("phone"),
                   phone_reading(**changes), now=100)
    assert not result.admitted and result.reasons


@pytest.mark.parametrize("changes", [
    {"battery_pct": 5, "charging": False}, {"ap_c": 55.0}, {"battery_c": 45.0},
    {"ap_c": None}, {"battery_c": None}, {"battery_pct": None, "charging": None},
])
def test_owner_rule_no_manual_phone_resource_limits(changes):
    result = admit(Device("s25", "phone", 1, "fake-s25"), spec("phone"),
                   phone_reading(**changes), now=100)
    assert result.admitted, result.reasons


def test_charging_phone_and_owner_in_use_are_admitted_but_labelled():
    dev = Device("s22", "phone", 1, "fake-s22")
    result = admit(dev, spec("phone"), phone_reading(battery_pct=10, charging=True,
                   owner_idle_s=0), now=100)
    assert result.admitted and result.owner_state == "owner_in_use"
    idle = admit(dev, spec("phone"), phone_reading(), now=100)
    assert idle.admitted and idle.owner_state == "owner_idle"


def test_focus_change_holds_and_missing_serial_holds():
    dev = Device("s25", "phone", 1, "fake-s25")
    result = admit(dev, spec("phone"), phone_reading(), previous_focus="other-focus", now=100)
    assert result.admitted and result.owner_state == "owner_in_use"  # labelled, not paused
    assert not admit(replace(dev, serial=None), spec("phone"), phone_reading(), now=100).admitted


def test_mbp_requires_live_resources_and_allowed_placement():
    dev = Device("mbp", "cpu", 3)
    assert not admit(dev, spec(), Reading(100), now=100).admitted
    assert admit(dev, spec(), Reading(100, mem_available=2 * GIB, cpus=4), now=100).admitted
    assert not admit(dev, spec(devices=("groot-cpu",)), Reading(100, mem_available=8 * GIB, cpus=8), now=100).admitted


def test_probe_remeasures_before_each_admission():
    memory = iter([8 * 1024 * 1024, 5 * 1024 * 1024])
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        out = f"MemAvailable: {next(memory)} kB\n" if argv == ["cat", "/proc/meminfo"] else "4\n"
        return SimpleNamespace(returncode=0, stdout=out)

    probe = Probe(runner, clock=lambda: 100.0)
    dev = Device("groot-cpu", "cpu", 1)
    assert admit(dev, spec(), probe.read(dev), now=100).admitted
    assert not admit(dev, spec(), probe.read(dev), now=100).admitted
    assert calls.count(["cat", "/proc/meminfo"]) == 2


def test_mbp_probe_uses_fresh_single_node_probe_not_health_cache():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "mem_available_mb": 8192, "ncpu": 8, "maintenance": False, "paused": False,
        }))

    probe = Probe(runner, clock=lambda: 100.0)
    live = probe.read(Device("mbp", "cpu", 3))
    assert live.mem_available == 8192 * MIB and live.cpus == 8
    assert len(calls) == 1 and calls[0][:2] == ["python3", "-c"]
    assert "fleet['probe'](node)" in calls[0][2]
    assert "fleet['registry']()[1]['mbp']" in calls[0][2]
    assert "health/state.json" not in calls[0][2]


@pytest.mark.parametrize("changes", [{"error": "offline"}, {"maintenance": True},
                                     {"paused": True}, {"mem_available_mb": None},
                                     {"ncpu": True}])
def test_mbp_probe_refuses_failed_or_missing_telemetry(changes):
    value = {"mem_available_mb": 8192, "ncpu": 8, "maintenance": False, "paused": False, **changes}
    runner = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(value))
    live = Probe(runner, clock=lambda: 100).read(Device("mbp", "cpu", 3))
    assert live.error and not admit(Device("mbp", "cpu", 3), spec(), live, now=100).admitted


def phone_parts():
    return {
        "battery": "  level: 70\n  temperature: 350\n  USB powered: true\n",
        "thermalservice": "Cached temperatures:\nTemperature{mValue=20.0, mType=0, mName=AP, mStatus=0}\nCurrent temperatures from HAL:\n  Temperature{mValue=40.0, mType=0, mName=AP, mStatus=0}\nCurrent cooling devices from HAL:\n",
        "power": "mWakefulness=Awake\n lastUserActivityTime=123 (120000 ms ago)\n",
        "window": "mTopFocusedDisplayId=0\n mCurrentFocus=Window{fixture-token}\n",
        "telephony.registry": "mCallState=0\nmCallState=0\n",
        "audio": "mAudioModeOwner: mPid=0 mMode=0\n",
    }


def test_phone_telemetry_commands_are_shell_only_and_focus_is_opaque():
    parts = phone_parts()
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert argv[:5] == ["adb", "-s", "fake-s25", "shell", "dumpsys"]
        assert "input" not in " ".join(argv) and "am start" not in " ".join(argv)
        return SimpleNamespace(returncode=0, stdout=parts[argv[5]])

    live = Probe(runner, clock=lambda: 100).read(Device("s25", "phone", 1, "fake-s25"))
    assert live.ap_c == 40.0 and live.battery_c == 35.0 and len(calls) == 6
    assert len(live.focus) == 64 and "fixture-token" not in live.focus
    assert admit(Device("s25", "phone", 1, "fake-s25"), spec("phone"), live, now=100).admitted


def test_samsung_multi_display_focus_is_read_not_unavailable():
    parts = phone_parts()
    parts["window"] = ("mTopFocusedDisplayId=0\n  mCurrentFocus=null\n  mFocusedApp=null\n"
                       "  mCurrentFocus=Window{b0c u0 launcher}\n")
    live = parse_phone(parts, 100)
    assert len(live.focus) == 64 and "launcher" not in live.focus
    assert admit(Device("s22", "phone", 1, "fake"), spec("phone"), live, now=100).admitted
    parts["window"] = parts["window"].replace("launcher", "other-app")
    assert parse_phone(parts, 100).focus != live.focus  # a focus change still holds the phone
    parts["window"] = "mTopFocusedDisplayId=0\n  mCurrentFocus=null\n  mCurrentFocus=null\n"
    assert parse_phone(parts, 100).focus  # sleeping screen is a reading, not missing telemetry


def test_agent_virtual_display_focus_changes_do_not_hold_the_phone():
    parts = phone_parts()
    base = ("  Display: mDisplayId=145 (organized)\n  mCurrentFocus={agent}\n"
            "  Display: mDisplayId=0 (organized)\n  mCurrentFocus={owner}\n"
            "WINDOW MANAGER WINDOWS\n  mTopFocusedDisplayId=0\n")
    parts["window"] = base.format(agent="null", owner="Window{1 u0 launcher}")
    first = parse_phone(parts, 100).focus
    parts["window"] = base.format(agent="Window{2 u0 chatgpt}", owner="Window{1 u0 launcher}")
    assert parse_phone(parts, 100).focus == first  # agent display churn is ignored
    parts["window"] = base.format(agent="null", owner="Window{9 u0 launcher}")
    assert parse_phone(parts, 100).focus == first  # recreated window, same app
    parts["window"] = base.format(agent="null", owner="Window{3 u0 owner-app}")
    assert parse_phone(parts, 100).focus != first  # owner display change still holds


def test_wifi_tablet_without_ap_sensor_or_telephony_is_readable():
    parts = phone_parts()
    parts["thermalservice"] = ("Current temperatures from HAL:\n"
                               "\tTemperature{mValue=30.0, mType=0, mName=CPU, mStatus=0}\n"
                               "\tTemperature{mValue=24.7, mType=3, mName=SKIN, mStatus=0}\n"
                               "Current cooling devices from HAL:\n")
    parts["telephony.registry"] = "last known state:\n  mDefaultPhoneId=-1\n  mDefaultSubId=-1\n"
    live = parse_phone(parts, 100)
    assert live.ap_c == 30.0 and live.call_state == 0
    assert admit(Device("tab", "phone", 1, "fake-tab"), spec("phone"), live, now=100).admitted
    parts["telephony.registry"] = "last known state:\n  mDefaultPhoneId=0\n"
    assert parse_phone(parts, 100).call_state is None  # a phone with unreadable call state stays held


def test_phone_host_worker_needs_less_groot_headroom_than_groot_jobs():
    from turbo.devices import PHONE_HOST_HEADROOM, GROOT_HEADROOM
    tight = Reading(measured_at=100, mem_available=5 * GIB, cpus=8)
    host = Device("groot-cpu", "cpu", 1)
    small = spec("cpu")
    small.resources.mem = GIB // 2
    assert not admit(host, small, tight, now=100).admitted
    assert admit(host, small, tight, now=100, headroom=PHONE_HOST_HEADROOM).admitted
    starved = Reading(measured_at=100, mem_available=GIB, cpus=8)
    assert not admit(host, small, starved, now=100, headroom=PHONE_HOST_HEADROOM).admitted
    assert PHONE_HOST_HEADROOM < GROOT_HEADROOM


def test_cached_temperatures_and_second_sim_call_fail_closed():
    parts = phone_parts()
    parts["thermalservice"] = "Cached temperatures:\nTemperature{mValue=20.0, mType=0, mName=AP, mStatus=0}\n"
    parts["telephony.registry"] = "mCallState=0\nmCallState=2\n"
    live = parse_phone(parts, 100)
    assert live.ap_c is None and live.call_state == 2
    assert not admit(Device("s25", "phone", 1, "fake-s25"), spec("phone"), live, now=100).admitted


@pytest.mark.parametrize("command", [
    "input tap 1 2", "/system/bin/input keyevent 3", "am start x", "cmd activity start x",
    "pm install x.apk", "wm size 100x100", "sendevent /dev/input/event0 1 2 3",
    "sh benchmark.sh --display 0", "eval 'input tap 1 2'",
])
def test_phone_ui_activity_install_and_display_commands_are_rejected(command):
    with pytest.raises(ValueError):
        adb_command("fake-s25", "shell", command)


def test_phone_transfers_are_allowed_by_owner_clarification():
    assert adb_command("fake-s25", "push", "model.bin", "/data/local/tmp/hc/model.bin")[3] == "push"
    assert adb_command("fake-s25", "pull", "/data/local/tmp/hc/result.json", "result.json")[3] == "pull"
    with pytest.raises(ValueError):
        adb_command("fake-s25", "install", "app.apk")
    with pytest.raises(ValueError):
        adb_command("", "shell", "dumpsys battery")


def test_launch_commands_enforce_scope_and_mbp_checkpoint_route(tmp_path):
    cpu = launch_command(Device("groot-cpu", "cpu", 1), spec(), ["python3", "worker.py"], tmp_path, "abc123")
    assert cpu[:4] == ["systemd-run", "--user", "--scope", "--unit"]
    assert cpu[4] == "hc-turbo-abc123.scope"
    assert "MemoryMax=1073741824" in cpu and "CPUQuota=100%" in cpu
    gpu = launch_command(Device("groot-gpu", "gpu", 2), spec("gpu", gpu_mem=GIB), ["python3", "worker.py"], tmp_path, "abc124")
    assert "CUDA_VISIBLE_DEVICES=0" in gpu
    mbp = launch_command(Device("mbp", "cpu", 3), spec(), ["python3", "-m", "turbo.worker"], tmp_path, "abc125")
    assert mbp[:7] == ["fleet-build", "--node", "mbp", "--class", "bench", "--checkpoint-safe", "--mem"]
    assert mbp[mbp.index("--src") + 1] == str(tmp_path / "work")
    assert mbp[mbp.index("--fetch") + 1] == "output"
    assert mbp[mbp.index("--out") + 1] == str(tmp_path / "remote")
    assert mbp[mbp.index("--wait") + 1] == "0"


@pytest.mark.parametrize("name", ["s25", "s22"])
def test_phone_host_worker_is_scoped_and_cannot_select_cuda(tmp_path, name):
    command = ["python3", "-m", "turbo.mobile", "--device", name,
               "--serial", "fake-" + name, "--track", "tts/phonon"]
    argv = launch_command(Device(name, "phone", 1, "fake-" + name),
                          spec("phone", mem=8 * GIB, cpus=1), command,
                          tmp_path, "owned-phone-" + name)
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--unit",
                        "hc-turbo-owned-phone-" + name + ".scope"]
    assert argv[5:9] == ["-p", "MemoryMax=8589934592", "-p", "CPUQuota=100%"]
    assert argv[9:] == ["--", "env", "CUDA_VISIBLE_DEVICES=", *command]
    # The cap covers the entire worker and its scorer without rewriting the
    # benchmark command or allowing a direct transport to bypass the adapter.
    assert command[0] == "python3"
    with pytest.raises(ValueError, match="reviewed mobile worker"):
        launch_command(Device(name, "phone", 1, "fake-" + name), spec("phone"),
                       ["/usr/bin/adb", "shell", "true"], tmp_path, "direct-adb")


def test_fleet_cancellation_requires_exact_owned_submission_id():
    jid = "fleet-mbp-0123456789abcdef"
    log = f"fleet: job {jid} -> mbp (Kueue bench-lanes); log /tmp/log; stage /tmp/stage\n"
    assert fleet_job_id(log) == jid
    assert cancel_command(jid) == ["fleet", "kill", "mbp", jid]
    assert fleet_job_id("worker mentioned " + jid) is None
    with pytest.raises(ValueError):
        cancel_command("--all")
    with pytest.raises(ValueError):
        scope_name("unsafe;token")


def test_exact_device_slot_caps():
    assert {d.name: d.capacity for d in default_devices("fake-s25", "fake-s22")} == {
        "groot-gpu": 2, "groot-cpu": 1, "mbp": 3, "s25": 1, "s22": 1, "tab": 1,
    }


def test_same_adb_serial_cannot_occupy_both_phone_slots(monkeypatch):
    with pytest.raises(ValueError, match="distinct"):
        default_devices("same-device", "same-device")
    monkeypatch.setenv("S25_SERIAL", "same-device")
    monkeypatch.setenv("S22_SERIAL", "same-device")
    with pytest.raises(ValueError, match="distinct"):
        default_devices()


def test_only_confirmed_unallocated_mbp_admission_hold_is_retryable():
    marker = ("fleet-build: no worker has room now (exit 75). Retry later or lower --mem; "
              "do not queue it on groot.\n")
    assert retryable_admission_exit(75, marker)
    assert not retryable_admission_exit(75, "unknown observer failure\n")
    assert not retryable_admission_exit(1, marker)
    assert not retryable_admission_exit(75, "worker says " + marker)
    allocated = "fleet: job fleet-mbp-0123456789abcdef -> mbp (Kueue bench-lanes); log x; stage x\n"
    assert not retryable_admission_exit(75, allocated + marker)
    assert not retryable_admission_exit(75, "fleet: job unfamiliar-id -> mbp\n" + marker)


def test_terminal_mbp_failure_does_not_require_recancelling_finished_job():
    job = "fleet-mbp-0123456789abcdef"
    submitted = f"fleet: job {job} -> mbp (Kueue bench-lanes); log x; stage x\n"
    assert fleet_terminal_exit(1, submitted + f"fleet: job {job} on mbp exited 1\n", job)
    # Cleanup after a successful benchmark can make the observer fail without
    # making the completed benchmark run again or consume a live slot.
    assert fleet_terminal_exit(1, submitted + f"fleet: job {job} on mbp exited 0\n", job)
    assert not fleet_terminal_exit(1, submitted, job)


def test_terminal_mbp_evidence_must_follow_exact_owned_submission():
    job = "fleet-mbp-0123456789abcdef"
    other = "fleet-mbp-fedcba9876543210"
    submitted = f"fleet: job {job} -> mbp (Kueue bench-lanes); log x; stage x\n"
    terminal = f"fleet: job {job} on mbp exited 1\n"
    assert not fleet_terminal_exit(1, terminal, job)
    assert not fleet_terminal_exit(1, terminal + submitted, job)
    assert not fleet_terminal_exit(1, submitted + f"fleet: job {other} on mbp exited 1\n", job)
    assert not fleet_terminal_exit(1, submitted + "worker says " + terminal, job)
    assert not fleet_terminal_exit(None, submitted + terminal, job)
