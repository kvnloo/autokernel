#!/usr/bin/env python3
"""Run one host referee in an isolated, restartable directory.

The referee remains the authority for its measurement. Exit zero alone is never
evidence of a KEEP: a structured receipt, a valid CI and the configured oracle
are all required. This module does not invent outputs for external referees.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from turbo.spec import Spec


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_receipt(directory, stdout):
    """Prefer the referee's file, then its last complete JSON stdout object."""
    path = Path(directory) / "result.json"
    if path.is_file():
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("referee result.json must be an object")
        return value
    text = Path(stdout).read_text(errors="replace")
    decoder = json.JSONDecoder()
    found = None
    # Existing mobile-style output can be pretty-printed, followed by METRIC.
    for index, char in enumerate(text):
        if char != "{" or (index and text[index - 1] != "\n"):
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "decision" in value:
            found = value
    if found is None:
        raise ValueError("referee produced no structured decision receipt")
    return found


def checked_ci(value):
    if not isinstance(value, dict) or set(value) != {"lo", "mid", "hi"}:
        raise ValueError("CI must contain exactly lo, mid and hi")
    numbers = [value[k] for k in ("lo", "mid", "hi")]
    try:
        valid = all(not isinstance(x, bool) and isinstance(x, (int, float))
                    and math.isfinite(x) and x > 0 for x in numbers)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("CI ratios must be positive finite numbers")
    if not numbers[0] <= numbers[1] <= numbers[2]:
        raise ValueError("CI bounds are unordered")
    return dict(zip(("lo", "mid", "hi"), numbers))


def local_artifact(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("oracle evidence must name a relative artifact")
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError("oracle evidence is missing or outside the run directory")
    return path


def verify_oracle(spec, receipt, root):
    """Check evidence; missing fixture/metadata fails closed, including exit 0."""
    if spec.oracle_status != "ready":
        return False, "oracle_status=" + spec.oracle_status
    oracle = spec.oracle
    kind = oracle["kind"]
    if kind == "receipt":
        checks = receipt.get("checks", {})
        if receipt.get("oracle_passed") is not True:
            return False, "referee did not attest the configured equivalence check"
        if not isinstance(checks, dict) or any(checks.get(k) is not True for k in oracle.get("checks", ())):
            return False, "required referee equivalence checks are absent or false"
        return True, "referee equivalence checks passed"
    if kind == "byte-equal":
        fixture = local_artifact(root, oracle["fixture"])
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        if digest != oracle["sha256"]:
            return False, "fixture SHA-256 mismatch"
        evidence = receipt.get("artifacts", {})
        if not isinstance(evidence, dict):
            raise ValueError("byte-equal artifacts must be an object")
        baseline = local_artifact(root, oracle.get("baseline") or evidence.get("baseline"))
        candidate = local_artifact(root, oracle.get("candidate") or evidence.get("candidate"))
        if baseline.samefile(candidate):
            return False, "baseline and candidate must be distinct artifacts"
        # The independent referee binds these complete corpus outputs to the
        # pinned input; equality of two empty/truncated files is not sufficient.
        checks = receipt.get("checks", {})
        valid = (receipt.get("oracle_passed") is True
                 and isinstance(checks, dict)
                 and checks.get("complete_fixture") is True
                 and checks.get("overlapping_secrets") is True
                 and receipt.get("fixture_sha256") == digest
                 and baseline.stat().st_size > 0
                 and baseline.read_bytes() == candidate.read_bytes())
        return valid, "byte-equal fixture outputs" if valid else "redaction equivalence evidence failed"
    if kind == "physarum-npz":
        import numpy as np
        left = sorted(Path(root).glob(oracle["baseline"]))
        right = sorted(Path(root).glob(oracle["candidate"]))
        if not left or len(left) != len(right):
            return False, "missing or unpaired Physarum NPZ outputs"
        if any(a.samefile(b) for a in left for b in right):
            return False, "Physarum baseline and candidate artifacts overlap"
        for before, after in zip(left, right):
            before = local_artifact(root, str(before.relative_to(root)))
            after = local_artifact(root, str(after.relative_to(root)))
            with np.load(before, allow_pickle=False) as a, np.load(after, allow_pickle=False) as b:
                for key in (oracle["tube_key"], oracle["backbone_key"]):
                    # The referee serializes sets as rows (or scalar members).
                    av, bv = np.asarray(a[key]), np.asarray(b[key])
                    if av.ndim != bv.ndim or av.shape[1:] != bv.shape[1:]:
                        return False, "Physarum set shape mismatch"
                    aa = {tuple(np.asarray(x).reshape(-1).tolist()) for x in np.atleast_1d(av)}
                    bb = {tuple(np.asarray(x).reshape(-1).tolist()) for x in np.atleast_1d(bv)}
                    if aa != bb:
                        return False, "Physarum tube/backbone sets differ"
                av, bv = np.asarray(a[oracle["d_key"]]), np.asarray(b[oracle["d_key"]])
                if (av.size == 0 or av.shape != bv.shape or not np.isfinite(av).all() or not np.isfinite(bv).all()
                        or not np.allclose(av, bv, rtol=oracle["rtol"], atol=oracle["atol"], equal_nan=False)):
                    return False, "Physarum D exceeds configured CG tolerance"
        if receipt.get("checks", {}).get("aa_passed") is not True:
            return False, "Physarum A/A control was not attested"
        return True, "Physarum sets and D match"
    return False, "unsupported host oracle"


def score(spec, receipt, root, returncode=0):
    result = {"decision": "FAIL", "ci": None, "oracle_passed": False}
    try:
        if returncode:
            raise ValueError(f"referee exited {returncode}")
        decision = receipt.get("decision")
        if decision not in ("KEEP", "REVERT", "NOISE", "FAIL"):
            raise ValueError("referee decision is absent or unsupported")
        if receipt.get("ci") is not None:
            result["ci"] = checked_ci(receipt["ci"])
        ok, reason = verify_oracle(spec, receipt, root)
        result.update(oracle_passed=ok, reason=reason)
        if decision == "KEEP":
            if not ok:
                result["decision"] = "NOISE" if spec.oracle_status != "ready" else "FAIL"
            elif result["ci"] is None:
                result["reason"] = "KEEP requires a confidence interval"
            elif (spec.direction == "lower" and result["ci"]["hi"] >= 1
                  or spec.direction == "higher" and result["ci"]["lo"] <= 1):
                result.update(decision="NOISE", reason="CI does not establish improvement")
            else:
                result["decision"] = "KEEP"
        else:
            result["decision"] = decision
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        result["reason"] = str(exc)
    return result


def source_ignore(_directory, names):
    return [n for n in names if n in {".git", ".venv", "__pycache__", ".pytest_cache", "state", "output", "out", "result.json"}]


def stage(spec, repo, run_dir):
    """A source snapshot per run; external referees are copied, never edited."""
    repo, run_dir = Path(repo).resolve(), Path(run_dir).resolve()
    work = run_dir / "work"
    work.mkdir(parents=True)
    state_root = run_dir.parent.parent if run_dir.parent.name == "runs" else run_dir

    def ignore(directory, names):
        omitted = set(source_ignore(directory, names))
        omitted.update(name for name in names
                       if (Path(directory) / name).resolve() in (state_root, run_dir))
        return omitted

    shutil.copytree(repo / "turbo", work / "turbo", ignore=ignore)
    source = Path(spec.working_directory) if spec.working_directory else repo
    source = source.resolve()
    if not source.is_dir() or run_dir.is_relative_to(source) and source != repo:
        raise ValueError("referee working directory unavailable or recursive")
    # Reject external symlinks instead of silently exporting host files to MBP.
    for path in source.rglob("*"):
        if path.is_relative_to(state_root):
            continue
        if any(part in {".git", ".venv", "__pycache__", "state", "output", "out"} for part in path.relative_to(source).parts):
            continue
        if path.is_symlink() and not path.resolve().is_relative_to(source):
            raise ValueError("referee snapshot contains an external symlink")
    shutil.copytree(source, work / "source", ignore=ignore, symlinks=False)
    shutil.copy2(spec.source, work / "kernel.toml")
    command = []
    for item in spec.referee:
        value = item.replace("{repo}", str(repo)).replace("{python}", "python3")
        if "{" in value or "}" in value:
            raise ValueError("host referee arguments cannot depend on dispatcher state")
        candidate = Path(value)
        if candidate.is_absolute() and candidate.is_relative_to(source):
            value = str(candidate.relative_to(source))
            # Absolute source paths become paths relative to the isolated cwd.
            if "/" not in value:
                value = "./" + value
        command.append(value)
    atomic_json(work / "launch.json", {"command": command})
    return ["python3", "-m", "turbo.host", "--spec", "kernel.toml", "--run-dir", "output"]


def execute(spec, work, output, popen=subprocess.Popen):
    """One completed referee invocation is the atomic checkpoint unit.

    Kueue may restart a partially completed unit. Its mutable cwd is recreated
    from source; a completed invocation is never replayed on readmission.
    """
    work, output = Path(work).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    checkpoint = output / "referee-complete.json"
    cwd = output / "referee"
    command = json.loads((work / "launch.json").read_text())["command"]
    if checkpoint.exists():
        returncode = json.loads(checkpoint.read_text())["returncode"]
    else:
        if cwd.exists():
            shutil.rmtree(cwd)
        shutil.copytree(work / "source", cwd)
        child = None
        old_handlers = {}

        def halt(signum, _frame):
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                except ProcessLookupError:
                    pass
            raise SystemExit(128 + signum)

        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, halt)
            with (output / "stdout.log").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
                child = popen(command, cwd=cwd, stdout=stdout, stderr=stderr,
                              start_new_session=True, env={**os.environ,
                              "HC_TURBO_RUN_DIR": str(cwd), "HC_TURBO_SPEC": str(spec.source.resolve())})
                try:
                    returncode = child.wait(timeout=spec.max_runtime)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                    returncode = 124
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
        atomic_json(checkpoint, {"returncode": returncode})
    try:
        receipt = read_receipt(cwd, output / "stdout.log") if not returncode else {}
        result = score(spec, receipt, cwd, returncode)
    except (OSError, ValueError) as exc:
        result = {"decision": "FAIL", "ci": None, "oracle_passed": False, "reason": str(exc)}
    result.update(id=spec.id, oracle_status=spec.oracle_status)
    atomic_json(result_path, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    spec = Spec.load(args.spec)
    result = execute(spec, Path.cwd(), args.run_dir)
    print(json.dumps(result, allow_nan=False))
    return 0 if result["decision"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
