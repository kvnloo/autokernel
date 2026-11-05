"""Owner-yield signal: agent user-activity pokes must not look like Kevin (S25, 2026-11-05 04:38-04:53).

The fixture holds the supervisor's own telemetry rows from that window (owner_idle_s < 2 s, hold=true) and the
phone state behind them: agent_dark.py's `PanelPower hold` poking IPowerManager.userActivity every 2 s after an
`input keyevent WAKEUP`, and a shell-owned scrcpy virtual display. Real display-0 input must still yield.
"""
import datetime
import json
import pathlib
import sys

import pytest

MOBILE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOBILE))
import bench_s25 as b  # noqa: E402
import fulltime as f  # noqa: E402

FX = json.loads((MOBILE / "tests/fixtures/owner_yield_20261105.json").read_text())
P = FX["phone"]
WAKE_WALL = datetime.datetime.fromisoformat(P["wake_wall"])


def phone_out(at, activity_idle_s, *, hold=P["panel_hold_procs"], vd=True, down_times=P["down_times"],
              wake_reason=P["wake_reason"], wake_ms=P["wake_time_ms"], verifier=True):
    """What telemetry()'s adb shell returns at wall time `at` (monotonic clock anchored on the 04:37:45 wake)."""
    now_ms = P["wake_time_ms"] + int((datetime.datetime.fromisoformat(at) - WAKE_WALL).total_seconds() * 1000)
    idle_ms = int(activity_idle_s * 1000)
    lines = ["  level: 60", "  temperature: 371", "  AC powered: true", "==",
             "==",
             "  mWakefulness=Awake",
             f"  mLastWakeTime={wake_ms} ({now_ms - wake_ms} ms ago)",
             f"  mLastWakeUpReason: {wake_reason}",
             f"lastUserActivityTime={now_ms - idle_ms} ({idle_ms} ms ago)",
             "==",
             "  mTopFocusedDisplayId=0", "  mCurrentFocus=Window{d60a666 u0 ai.nousresearch.hermes.mobile}",
             "  mCallState=0",
             "=="]
    if vd:
        lines.append("        " + P["viewport_vd"])
    if verifier:
        lines += ["  Filter verifier on display 0:", f"    Down times by device: {down_times}"]
    lines.append(f"agent_panel_hold={hold}")
    return "\n".join(lines) + "\n"


def telemetry_at(monkeypatch, out):
    monkeypatch.setattr(b, "sh", lambda cmd, timeout=600, check=False: out)
    return b.telemetry()


@pytest.mark.parametrize("row", FX["rows"], ids=[r["at"][11:] for r in FX["rows"]])
def test_agent_pokes_0438_0453_do_not_yield(monkeypatch, row):
    assert row["hold"] and row["why"] == ["owner"] and row["owner_idle_s"] < 2  # the recorded false yield
    t = telemetry_at(monkeypatch, phone_out(row["at"], row["owner_idle_s"]))
    assert f.owner_busy(t) is None, t
    assert t["activity_idle_s"] == pytest.approx(row["owner_idle_s"], abs=0.01)  # raw signal still reported


def _at_ns(at, ago_s):
    now_ms = P["wake_time_ms"] + int((datetime.datetime.fromisoformat(at) - WAKE_WALL).total_seconds() * 1000)
    return (now_ms - int(ago_s * 1000)) * 1_000_000


def test_physical_display0_touch_still_yields(monkeypatch):
    at = "2026-11-05T04:45:24"
    out = phone_out(at, 1.1, down_times=f"{{DeviceId(-1): 195160410000000, DeviceId(6): {_at_ns(at, 5)}}}")
    t = telemetry_at(monkeypatch, out)
    assert t["owner_idle_s"] == pytest.approx(5, abs=0.01)
    assert f.owner_busy(t) == "foreground"


def test_injected_display0_input_is_not_owner(monkeypatch):
    at = "2026-11-05T04:45:24"
    out = phone_out(at, 1.1, down_times=f"{{DeviceId(-1): {_at_ns(at, 3)}, DeviceId(6): 208578346888000}}")
    assert f.owner_busy(telemetry_at(monkeypatch, out)) is None


def test_owner_power_button_wake_yields(monkeypatch):
    at = "2026-11-05T04:45:24"
    wake_ms = P["wake_time_ms"] + int((datetime.datetime.fromisoformat(at) - WAKE_WALL).total_seconds() * 1000) - 10_000
    t = telemetry_at(monkeypatch, phone_out(at, 1.1, wake_reason=1, wake_ms=wake_ms))
    assert f.owner_busy(t) == "foreground"


@pytest.mark.parametrize("idle, busy", [(1.3, "foreground"), (500, None)])
def test_no_agent_source_keeps_activity_signal(monkeypatch, idle, busy):
    t = telemetry_at(monkeypatch, phone_out("2026-11-05T04:45:24", idle, hold=0, vd=False))
    assert t["agent_sources"] == []
    assert f.owner_busy(t) == busy


def test_unreadable_touch_record_fails_safe(monkeypatch):
    """No input-verifier block (format change): fall back to the raw activity signal, i.e. yield."""
    t = telemetry_at(monkeypatch, phone_out("2026-11-05T04:45:24", 1.3, verifier=False))
    assert f.owner_busy(t) == "foreground"
