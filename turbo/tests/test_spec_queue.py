"""Spec and ledger regressions; all device evidence is synthetic."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from turbo.queue import LedgerError, Queue
from turbo.spec import MOBILE_TRACKS, Resources, Spec, SpecError


def specification(**updates):
    data = {
        "id": "a", "objective": "speed", "metric": "seconds",
        "referee": ["python3", "{repo}/referee.py"],
        "oracle": {"kind": "receipt", "check": "Exact output equality"},
        "device_class": "cpu", "resources": {"mem": 1024, "cpus": 1},
        "max_runtime": 60,
    }
    data.update(updates)
    return data


def phone_specification(**updates):
    data = specification(
        device_class="phone", devices=["s25", "s22"],
        referee=["python3", "{repo}/turbo/mobile.py", "--track", "tts/pocket"],
        oracle={"kind": "mobile", "check": "Fixed quality gates", "track": "tts/pocket"},
    )
    data.update(updates)
    return data


def write_spec(directory, identifier="a", filename=None, **updates):
    data = specification(id=identifier, **updates)
    path = directory / (filename or f"{identifier}.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {json.dumps(value)}" for key, value in data.items()
             if not isinstance(value, dict)]
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            lines.extend(f"{field} = {json.dumps(item)}" for field, item in value.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def receipt(identifier="a", device="groot-cpu", decision="NOISE", **updates):
    row = {
        "epoch": 1, "id": identifier, "device": device,
        "cmd": ["python3", "referee.py"], "decision": decision,
        "ci": None, "run_dir": "runs/1/a",
    }
    if decision == "KEEP":
        row.update(ci={"lo": 0.7, "mid": 0.8, "hi": 0.9}, oracle_passed=True)
    row.update(updates)
    return row


@pytest.fixture
def queue(tmp_path):
    write_spec(tmp_path / "specs")
    return Queue(tmp_path / "specs", tmp_path / "state")


def test_spec_defaults_and_amdahl_priority(tmp_path):
    path = write_spec(tmp_path, expected_gain=4, amdahl_share=0.5, cost=2)
    spec = Spec.load(path)
    assert spec.source == path
    assert spec.priority == 1
    assert spec.direction == "lower"
    assert spec.resources == Resources(mem=1024, cpus=1, gpu_mem=0)
    default = Spec.from_mapping(specification())
    assert default.cost == default.max_runtime == 60
    assert default.oracle_status == "ready"


@pytest.mark.parametrize("field,value", [
    ("unknown", 1), ("id", "../a"), ("id", ""),
    ("objective", "latency"), ("metric", ""), ("metric", True),
    ("device_class", "tablet"), ("direction", "sideways"),
    ("referee", "python3 referee.py"), ("referee", []),
    ("referee", ["python3", 1]), ("referee", ["python3", "bad\nargument"]),
    ("max_runtime", True), ("max_runtime", 0), ("max_runtime", float("inf")),
    ("max_runtime", float("nan")), ("max_runtime", "60"),
    ("amdahl_share", 0), ("amdahl_share", 1.1),
    ("expected_gain", -1), ("expected_gain", False),
    ("cost", 0), ("cost", float("inf")),
    ("oracle_status", "unknown"), ("parent", "a"),
    ("devices", ["s25"]), ("devices", ["groot-cpu", "groot-cpu"]),
    ("devices", "groot-cpu"), ("working_directory", "relative"),
    ("resources", {"mem": 1, "cpus": 1, "extra": 0}),
    ("resources", {"mem": 1}),
    ("oracle", {"kind": "receipt", "check": ""}),
    ("oracle", {"kind": "receipt", "check": "Exact", "extra": 1}),
    ("oracle", {"kind": "receipt", "check": "Exact", "checks": ["aa", "aa"]}),
])
def test_invalid_spec_fields_are_rejected(field, value):
    with pytest.raises(SpecError):
        Spec.from_mapping(specification(**{field: value}))


@pytest.mark.parametrize("resources", [
    {"mem": True, "cpus": 1}, {"mem": 0, "cpus": 1},
    {"mem": 1.0, "cpus": 1}, {"mem": 1, "cpus": 0},
    {"mem": 1, "cpus": 1, "gpu_mem": -1},
    {"mem": 1, "cpus": False},
])
def test_resource_units_require_positive_integer_counts(resources):
    with pytest.raises(SpecError):
        Resources(**resources)


@pytest.mark.parametrize("argument", [
    "{unknown}", "{repo.name}", "{repo[0]}", "{repo!r}", "{repo:>5}", "{repo", "{}",
    "{run_dir}", "{state_dir}", "{device}", "{serial}",
])
def test_referee_rejects_unsupported_placeholders(argument):
    with pytest.raises(SpecError):
        Spec.from_mapping(specification(referee=["python3", argument]))


def test_referee_preserves_literal_argv_and_supported_templates():
    argv = ["/usr/bin/python3", "/absolute/referee.py", "argument with spaces",
            "{repo}", "{python}"]
    spec = Spec.from_mapping(specification(referee=argv, working_directory="/srv/work"))
    assert spec.referee == tuple(argv)
    assert spec.working_directory == "/srv/work"


def test_gpu_memory_reservation_is_mandatory():
    with pytest.raises(SpecError, match="positive gpu_mem"):
        Spec.from_mapping(specification(device_class="gpu"))
    with pytest.raises(SpecError, match="only GPU"):
        Spec.from_mapping(specification(resources={"mem": 1, "cpus": 1, "gpu_mem": 1}))
    spec = Spec.from_mapping(specification(device_class="gpu", devices=["groot-gpu"],
                                          resources={"mem": 1, "cpus": 1, "gpu_mem": 1}))
    assert spec.resources.gpu_mem == 1


@pytest.mark.parametrize("track", sorted(MOBILE_TRACKS))
def test_phone_requires_fixed_wrapper_for_each_supported_track(track):
    data = phone_specification(
        referee=["python3", "{repo}/turbo/mobile.py", "--track", track],
        oracle={"kind": "mobile", "check": "Fixed quality gates", "track": track},
    )
    assert Spec.from_mapping(data).oracle["track"] == track
    data["referee"] = ["adb", "shell", "input", "tap", "0", "0"]
    with pytest.raises(SpecError, match="fixed"):
        Spec.from_mapping(data)


def test_phone_rejects_unknown_track_and_receipt_oracle():
    with pytest.raises(SpecError, match="supported"):
        Spec.from_mapping(phone_specification(
            oracle={"kind": "mobile", "check": "Gate", "track": "tts/unknown"}))
    with pytest.raises(SpecError, match="supported"):
        Spec.from_mapping(phone_specification(oracle={"kind": "receipt", "check": "Gate"}))
    with pytest.raises(SpecError, match="phone"):
        Spec.from_mapping(specification(
            oracle={"kind": "mobile", "check": "Gate", "track": "tts/pocket"}))


def test_pending_fixture_accepts_only_explicit_empty_digest():
    oracle = {"kind": "byte-equal", "check": "Byte equality", "fixture": "fixtures/data.jsonl",
              "sha256": ""}
    pending = Spec.from_mapping(specification(oracle=oracle, oracle_status="pending-fixture"))
    assert pending.oracle["sha256"] == ""
    with pytest.raises(SpecError, match="sha256"):
        Spec.from_mapping(specification(oracle=oracle))
    oracle["sha256"] = "A" * 64
    assert Spec.from_mapping(specification(oracle=oracle)).oracle["sha256"] == "a" * 64
    oracle["baseline"] = "out/reference.jsonl"
    with pytest.raises(SpecError, match="together"):
        Spec.from_mapping(specification(oracle=oracle))


@pytest.mark.parametrize("field", ["fixture", "baseline", "candidate"])
@pytest.mark.parametrize("path", ["/tmp/output", "../output", "out/../../output", "."])
def test_oracle_paths_cannot_escape_work_directory(field, path):
    oracle = {"kind": "byte-equal", "check": "Byte equality", "fixture": "input.jsonl",
              "sha256": "a" * 64, "baseline": "baseline.jsonl", "candidate": "candidate.jsonl"}
    oracle[field] = path
    with pytest.raises(SpecError, match="relative"):
        Spec.from_mapping(specification(oracle=oracle))


def test_physarum_requires_all_keys_and_finite_tolerances():
    oracle = {"kind": "physarum-npz", "check": "Topology and solver tolerance",
              "baseline": "baseline.npz", "candidate": "candidate.npz", "tube_key": "tubes",
              "backbone_key": "backbone", "d_key": "D", "rtol": 1e-6, "atol": 0}
    assert Spec.from_mapping(specification(oracle=oracle)).oracle["atol"] == 0
    for invalid in (True, -1, float("nan")):
        with pytest.raises(SpecError):
            Spec.from_mapping(specification(oracle=dict(oracle, rtol=invalid)))
    del oracle["d_key"]
    with pytest.raises(SpecError, match="missing"):
        Spec.from_mapping(specification(oracle=oracle))


def test_nested_and_flat_specs_discover_once(tmp_path):
    directory = tmp_path / "specs"
    write_spec(directory, "root", "kernel.toml")
    write_spec(directory, "child", "group/child/kernel.toml", parent="root")
    write_spec(directory, "flat")
    assert set(Queue(directory, tmp_path / "state").specs) == {"root", "child", "flat"}


@pytest.mark.parametrize("problem", ["duplicate", "unknown-parent", "cycle"])
def test_queue_rejects_invalid_dependency_graph(tmp_path, problem):
    directory = tmp_path / "specs"
    if problem == "duplicate":
        write_spec(directory, "a", "one.toml")
        write_spec(directory, "a", "two.toml")
    elif problem == "unknown-parent":
        write_spec(directory, "a", parent="missing")
    else:
        write_spec(directory, "a", parent="b")
        write_spec(directory, "b", parent="a")
    with pytest.raises(SpecError):
        Queue(directory, tmp_path / "state")


def test_reload_fails_closed_after_invalid_edit(queue):
    (queue.spec_dir / "a.toml").write_text("not valid TOML", encoding="utf-8")
    with pytest.raises(SpecError):
        queue.reload()
    assert queue.specs == {}


def test_queue_accepts_hundred_spec_chain(tmp_path):
    directory = tmp_path / "specs"
    for index in range(100):
        updates = {"parent": f"spec-{index - 1}"} if index else {}
        write_spec(directory, f"spec-{index}", f"spec-{index}/kernel.toml", **updates)
    queue = Queue(directory, tmp_path / "state")
    assert len(queue.ranked()) == 100


def test_ranking_uses_amdahl_gain_cost_and_fair_ties(queue):
    write_spec(queue.spec_dir, "b")
    write_spec(queue.spec_dir, "fast", expected_gain=8, amdahl_share=0.5, cost=1)
    queue.reload()
    assert [spec.id for spec in queue.ranked()] == ["fast", "a", "b"]
    queue.append(receipt())
    assert [spec.id for spec in queue.ranked(exclude={"fast"})] == ["b", "a"]


def test_ledger_round_trip_is_append_only_and_keeps_evidence(queue):
    first = queue.append(receipt(at=123.5, evidence={"aa_passed": True}))
    prefix = queue.ledger_path.read_bytes()
    second = queue.append(receipt(epoch=2, at="2026-11-06T00:00:00Z"))
    assert queue.ledger_path.read_bytes().startswith(prefix)
    assert queue.rows() == [first, second]
    assert queue.rows()[0]["evidence"] == {"aa_passed": True}
    assert isinstance(queue.append(receipt(epoch=3))["at"], (int, float))


@pytest.mark.parametrize("updates", [
    {"epoch": True}, {"epoch": 0}, {"device": "unknown"},
    {"id": "missing"}, {"decision": "PASS"}, {"cmd": []},
    {"ci": {"lo": 0.9, "mid": 0.8, "hi": 0.7}},
    {"ci": {"lo": True, "mid": 1, "hi": 2}},
    {"ci": {"lo": 1, "mid": 2, "hi": float("inf")}},
    {"ci": {"lo": 1, "hi": 2}},
    {"ci": {"lo": -1, "mid": 0.8, "hi": 0.9}},
    {"at": True}, {"at": -1}, {"at": float("nan")}, {"at": "yesterday"},
])
def test_malformed_receipt_does_not_write(queue, updates):
    with pytest.raises(LedgerError):
        queue.append(receipt(**updates))
    assert queue.rows() == []


@pytest.mark.parametrize("updates", [
    {"oracle_passed": False}, {"oracle_passed": 1}, {"oracle_passed": None},
    {"ci": None}, {"ci": {"lo": 0, "mid": 0.5, "hi": 0.9}},
    {"ci": {"lo": 0.8, "mid": 0.9, "hi": 1}},
])
def test_keep_requires_positive_improving_ci_and_true_oracle(queue, updates):
    with pytest.raises(LedgerError):
        queue.append(receipt(decision="KEEP", **updates))
    assert queue.rows() == []


def test_higher_direction_requires_ci_strictly_above_one(queue):
    write_spec(queue.spec_dir, direction="higher")
    queue.reload()
    with pytest.raises(LedgerError, match="improvement"):
        queue.append(receipt(decision="KEEP"))
    kept = queue.append(receipt(decision="KEEP", ci={"lo": 1.1, "mid": 1.2, "hi": 1.3}))
    assert kept["decision"] == "KEEP"


def test_pending_fixture_never_keeps_even_with_passing_receipt(queue):
    write_spec(queue.spec_dir, oracle_status="pending-fixture", oracle={
        "kind": "byte-equal", "check": "Byte equality", "fixture": "fixtures/data.jsonl",
        "sha256": "",
    })
    queue.reload()
    with pytest.raises(LedgerError, match="pending-fixture"):
        queue.append(receipt(decision="KEEP"))
    assert queue.append(receipt())["decision"] == "NOISE"


@pytest.mark.parametrize("owner_state", [None, "owner_in_use", "unknown", "in-use", "idle"])
def test_phone_keep_requires_exact_idle_marker(queue, owner_state):
    data = phone_specification()
    data.pop("id")
    write_spec(queue.spec_dir, **data)
    queue.reload()
    with pytest.raises(LedgerError, match="owner_idle"):
        queue.append(receipt(device="s25", decision="KEEP", owner_state=owner_state))
    assert queue.append(receipt(device="s25", decision="KEEP", owner_state="owner_idle"))["decision"] == "KEEP"


def test_ledger_respects_device_class_and_explicit_allowlist(queue):
    with pytest.raises(LedgerError, match="device_class"):
        queue.append(receipt(device="s25"))
    write_spec(queue.spec_dir, devices=["groot-cpu"])
    queue.reload()
    with pytest.raises(LedgerError, match="allowlist"):
        queue.append(receipt(device="mbp"))


def test_corrupt_ledger_blocks_reads_and_further_appends(queue):
    queue.append(receipt())
    with queue.ledger_path.open("a", encoding="utf-8") as stream:
        stream.write('{"epoch": 2')
    original = queue.ledger_path.read_bytes()
    with pytest.raises(LedgerError, match="incomplete"):
        queue.rows()
    with pytest.raises(LedgerError, match="incomplete"):
        queue.append(receipt(epoch=2))
    assert queue.ledger_path.read_bytes() == original


def test_concurrent_appenders_preserve_every_record(queue):
    def append(index):
        independent = Queue(queue.spec_dir, queue.state_dir)
        independent.append(receipt(epoch=index + 1))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(12)))
    rows = queue.rows()
    assert len(rows) == 12
    assert {row["epoch"] for row in rows} == set(range(1, 13))


def test_council_streak_is_per_device_and_resets_only_on_keep(queue):
    for epoch, decision in enumerate(("FAIL", "NOISE", "REVERT"), 1):
        queue.append(receipt(epoch=epoch, decision=decision))
    assert queue.council_flags() == [
        {"device": "groot-cpu", "id": "a", "since_keep": 3, "level": 1},
    ]
    queue.append(receipt(device="mbp", epoch=4, decision="KEEP"))
    assert queue.council_flags()[0]["since_keep"] == 3
    queue.append(receipt(epoch=5, decision="FAIL"))
    queue.append(receipt(epoch=6, decision="NOISE"))
    # Escalation needs a real intervening council; repeated failures alone do
    # not fabricate that event or declare a third-level stop.
    assert queue.council_flags()[0]["level"] == 1
    queue.append(receipt(epoch=7, decision="KEEP"))
    assert queue.council_flags() == []


def test_repository_seeds_are_loadable_and_hc2_is_pending(tmp_path):
    seed_dir = Path(__file__).resolve().parents[1] / "specs"
    queue = Queue(seed_dir, tmp_path / "state")
    assert len(queue.specs) >= 12
    assert queue.specs["hc-2-memory-scrub"].oracle_status == "pending-fixture"
    assert queue.specs["hc-2-memory-scrub"].oracle["sha256"] == ""
    assert {spec.oracle["track"] for spec in queue.specs.values()
            if spec.device_class == "phone"} == MOBILE_TRACKS
