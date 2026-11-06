"""Evidence and checkpoint regressions; no host referee is executed."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from turbo.host import atomic_json, checked_ci, execute, local_artifact, read_receipt, score, stage


def spec(oracle=None, oracle_status="ready", direction="lower", **kwargs):
    return SimpleNamespace(id="hc-test", oracle=oracle or {
        "kind": "receipt", "check": "fixed referee equivalence", "checks": ["equivalent"]},
        oracle_status=oracle_status, direction=direction, **kwargs)


def keep(**kwargs):
    return {"decision": "KEEP", "ci": {"lo": .7, "mid": .8, "hi": .9},
            "oracle_passed": True, "checks": {"equivalent": True}, **kwargs}


def test_pending_fixture_cannot_keep_even_with_successful_referee(tmp_path):
    pending = spec({"kind": "byte-equal", "fixture": "fixtures/hc2-redaction.jsonl", "sha256": ""},
                   oracle_status="pending-fixture")
    result = score(pending, keep(), tmp_path)
    assert result["decision"] == "NOISE" and not result["oracle_passed"]
    assert result["reason"] == "oracle_status=pending-fixture"


def test_exit_zero_and_unstructured_stdout_are_not_oracle_evidence(tmp_path):
    out = tmp_path / "stdout.log"
    out.write_text("benchmark completed successfully\n")
    with pytest.raises(ValueError, match="no structured"):
        read_receipt(tmp_path, out)
    assert score(spec(), keep(oracle_passed=False), tmp_path)["decision"] == "FAIL"
    assert score(spec(), keep(checks={}), tmp_path)["decision"] == "FAIL"


def test_receipt_parser_accepts_existing_pretty_json_before_metric_line(tmp_path):
    out = tmp_path / "stdout.log"
    out.write_text("progress\n" + json.dumps(keep(), indent=2) + "\nMETRIC latency 1\n")
    assert read_receipt(tmp_path, out) == keep()
    atomic_json(tmp_path / "result.json", keep(decision="REVERT"))
    assert read_receipt(tmp_path, out)["decision"] == "REVERT"


@pytest.mark.parametrize("ci", [None, {}, {"lo": .5, "mid": .6},
    {"lo": True, "mid": .6, "hi": .7}, {"lo": .8, "mid": .6, "hi": .7},
    {"lo": .5, "mid": .6, "hi": float("nan")}, {"lo": 0, "mid": .6, "hi": .7}])
def test_malformed_confidence_interval_cannot_keep(ci, tmp_path):
    assert score(spec(), keep(ci=ci), tmp_path)["decision"] == "FAIL"


def test_confidence_direction_and_nonzero_exit_are_enforced(tmp_path):
    assert score(spec(), keep(), tmp_path)["decision"] == "KEEP"
    assert score(spec(), keep(ci={"lo": .8, "mid": 1, "hi": 1.1}), tmp_path)["decision"] == "NOISE"
    assert score(spec(direction="higher"), keep(), tmp_path)["decision"] == "NOISE"
    assert score(spec(direction="higher"), keep(ci={"lo": 1.1, "mid": 1.2, "hi": 1.3}), tmp_path)["decision"] == "KEEP"
    assert score(spec(), keep(), tmp_path, returncode=1)["decision"] == "FAIL"


def test_byte_equal_oracle_pins_fixture_and_compares_complete_outputs(tmp_path):
    # Synthetic adversarial unit-test data; not the host's pending HC-2 corpus.
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(json.dumps({"text": "key-long key", "secrets": ["key", "key-long"]}) + "\n")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    (tmp_path / "a.jsonl").write_bytes(b'"[REDACTED] [REDACTED]"\n')
    (tmp_path / "b.jsonl").write_bytes((tmp_path / "a.jsonl").read_bytes())
    oracle = {"kind": "byte-equal", "fixture": "fixture.jsonl", "sha256": digest}
    receipt = keep(fixture_sha256=digest, artifacts={"baseline": "a.jsonl", "candidate": "b.jsonl"},
                   checks={"complete_fixture": True, "overlapping_secrets": True})
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "KEEP"
    same = dict(receipt, artifacts={"baseline": "a.jsonl", "candidate": "a.jsonl"})
    assert score(spec(oracle), same, tmp_path)["decision"] == "FAIL"
    (tmp_path / "b.jsonl").write_bytes(b'"[REDACTED]-long [REDACTED]"\n')
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "FAIL"
    (tmp_path / "b.jsonl").write_bytes((tmp_path / "a.jsonl").read_bytes())
    fixture.write_text("changed corpus\n")
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "FAIL"


def test_oracle_rejects_empty_outputs_and_path_escape(tmp_path):
    (tmp_path / "fixture").write_bytes(b"fixture")
    (tmp_path / "a").write_bytes(b"")
    (tmp_path / "b").write_bytes(b"")
    digest = hashlib.sha256(b"fixture").hexdigest()
    oracle = {"kind": "byte-equal", "fixture": "fixture", "sha256": digest}
    receipt = keep(fixture_sha256=digest, artifacts={"baseline": "a", "candidate": "b"},
                   checks={"complete_fixture": True, "overlapping_secrets": True})
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "FAIL"
    with pytest.raises(ValueError):
        local_artifact(tmp_path, "../outside")
    with pytest.raises(ValueError):
        local_artifact(tmp_path, "/etc/passwd")


def test_completed_checkpoint_is_returned_without_replaying_referee(tmp_path):
    out = tmp_path / "output"
    atomic_json(out / "result.json", keep())

    def forbidden(*args, **kwargs):
        raise AssertionError("completed referee must not be replayed")

    assert execute(spec(), tmp_path, out, popen=forbidden) == keep()


def test_physarum_oracle_rejects_aliased_outputs_and_empty_d(tmp_path):
    import numpy as np

    oracle = {"kind": "physarum-npz", "baseline": "a.npz", "candidate": "b.npz",
              "tube_key": "tubes", "backbone_key": "backbone", "d_key": "D",
              "rtol": 1e-5, "atol": 1e-8}
    arrays = {"tubes": np.array([[1, 2], [2, 3]]), "backbone": np.array([[1, 2]]), "D": np.array([1.0])}
    np.savez(tmp_path / "a.npz", **arrays)
    np.savez(tmp_path / "b.npz", **arrays)
    receipt = keep(checks={"aa_passed": True})
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "KEEP"
    assert score(spec(dict(oracle, candidate="a.npz")), receipt, tmp_path)["decision"] == "FAIL"
    arrays["D"] = np.array([])
    np.savez(tmp_path / "a.npz", **arrays)
    np.savez(tmp_path / "b.npz", **arrays)
    assert score(spec(oracle), receipt, tmp_path)["decision"] == "FAIL"


def test_external_referee_is_snapshotted_and_stale_outputs_are_excluded(tmp_path):
    repo = tmp_path / "repo"
    (repo / "turbo").mkdir(parents=True)
    (repo / "turbo/__init__.py").write_text("")
    source = tmp_path / "referee"
    source.mkdir()
    (source / "run_hc1.sh").write_text("echo measured\n")
    (source / "result.json").write_text(json.dumps(keep()))
    (source / "out").mkdir()
    (source / "out/stale.npz").write_bytes(b"stale")
    kernel = tmp_path / "kernel.toml"
    kernel.write_text('id = "hc-test"\n')
    config = spec(source=kernel, working_directory=str(source), referee=("bash", str(source / "run_hc1.sh")))
    run = tmp_path / "run"
    command = stage(config, repo, run)
    assert command[:3] == ["python3", "-m", "turbo.host"]
    assert json.loads((run / "work/launch.json").read_text())["command"] == ["bash", "./run_hc1.sh"]
    assert (run / "work/source/run_hc1.sh").read_text() == (source / "run_hc1.sh").read_text()
    assert not (run / "work/source/result.json").exists()
    assert not (run / "work/source/out").exists()


@pytest.mark.parametrize("state_relative", ["tmpstate", "turbo/tmpstate"])
def test_custom_state_inside_repo_is_excluded_from_both_snapshots(tmp_path, state_relative):
    repo = tmp_path / "repo"
    (repo / "turbo").mkdir(parents=True)
    (repo / "turbo/__init__.py").write_text("")
    (repo / "referee.py").write_text("print('measurement')\n")
    kernel = repo / "kernel.toml"
    kernel.write_text('id = "hc-test"\n')
    state = repo / state_relative
    state.mkdir(parents=True)
    (state / "old-run.json").write_text('{"decision":"KEEP"}\n')
    run = state / "runs" / "owned-token"
    config = spec(source=kernel, working_directory=None,
                  referee=("python3", "{repo}/referee.py"))
    stage(config, repo, run)
    assert (run / "work/source/referee.py").read_text() == (repo / "referee.py").read_text()
    assert not (run / "work/source" / state_relative).exists()
    assert not (run / "work/turbo/tmpstate").exists()
    assert json.loads((run / "work/launch.json").read_text())["command"] == ["python3", "./referee.py"]
