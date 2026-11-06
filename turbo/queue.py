"""Dependency-checked spec queue and append-only, process-locked ledger."""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .spec import DEVICE_CLASSES, Spec, SpecError


class LedgerError(ValueError):
    """Ledger evidence is malformed or violates a scheduling safety gate."""


def _validate_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    required = {"epoch", "id", "device", "cmd", "decision", "ci", "run_dir"}
    if not isinstance(entry, Mapping) or not all(isinstance(k, str) for k in entry):
        raise LedgerError("ledger entry must be a table with string keys")
    if required - set(entry):
        raise LedgerError(f"missing ledger fields: {', '.join(sorted(required - set(entry)))}")
    row = dict(entry)
    if isinstance(row["epoch"], bool) or not isinstance(row["epoch"], int) or row["epoch"] < 1:
        raise LedgerError("epoch must be a positive integer")
    for field in ("id", "device", "decision", "run_dir"):
        if not isinstance(row[field], str) or not row[field].strip():
            raise LedgerError(f"{field} must be a nonempty string")
    if row["device"] not in DEVICE_CLASSES:
        raise LedgerError("unknown device")
    if row["decision"] not in {"KEEP", "REVERT", "NOISE", "FAIL"}:
        raise LedgerError("unknown decision")
    cmd = row["cmd"]
    if isinstance(cmd, str):
        if not cmd.strip() or "\0" in cmd:
            raise LedgerError("cmd must be nonempty")
    elif isinstance(cmd, (list, tuple)) and cmd:
        if any(not isinstance(value, str) or not value or "\0" in value for value in cmd):
            raise LedgerError("cmd arguments must be nonempty strings")
        row["cmd"] = list(cmd)
    else:
        raise LedgerError("cmd must be a nonempty string or argument array")
    ci = row["ci"]
    if ci is not None:
        if not isinstance(ci, Mapping) or set(ci) != {"lo", "mid", "hi"}:
            raise LedgerError("ci must contain exactly lo, mid and hi")
        values = []
        for key in ("lo", "mid", "hi"):
            value = ci[key]
            try:
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value > 0)
            except OverflowError:
                valid = False
            if not valid:
                raise LedgerError("ci values must be positive finite numbers")
            values.append(value)
        if not values[0] <= values[1] <= values[2]:
            raise LedgerError("ci must satisfy lo <= mid <= hi")
        row["ci"] = dict(ci)
    if "at" in row:
        at = row["at"]
        if isinstance(at, str):
            try:
                datetime.fromisoformat(at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise LedgerError("at must be an ISO8601 timestamp or UNIX seconds") from exc
        else:
            try:
                valid_at = (not isinstance(at, bool) and isinstance(at, (int, float))
                            and math.isfinite(at) and at >= 0)
            except OverflowError:
                valid_at = False
            if not valid_at:
                raise LedgerError("at must be an ISO8601 timestamp or UNIX seconds")
    try:
        json.dumps(row, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LedgerError("ledger entry must contain finite JSON values") from exc
    return row


def _read_rows(stream: Any) -> list[dict[str, Any]]:
    stream.seek(0)
    rows = []
    for line_number, line in enumerate(stream, 1):
        if not line.endswith("\n"):
            raise LedgerError(f"incomplete ledger record at line {line_number}")
        try:
            entry = json.loads(line)
            rows.append(_validate_row(entry))
        except (ValueError, TypeError) as exc:
            raise LedgerError(f"invalid ledger record at line {line_number}: {exc}") from exc
    return rows


class Queue:
    def __init__(self, spec_dir: str | Path, state_dir: str | Path):
        self.spec_dir = Path(spec_dir)
        self.state_dir = Path(state_dir)
        self.ledger_path = self.state_dir / "ledger.jsonl"
        self.specs: dict[str, Spec] = {}
        self.reload()

    def reload(self) -> None:
        """Replace the schedulable set only after checking the entire graph."""
        self.specs = {}
        if not self.spec_dir.is_dir():
            raise SpecError(f"spec directory does not exist: {self.spec_dir}")
        candidates: dict[str, Spec] = {}
        paths = set(self.spec_dir.glob("*.toml")) | set(self.spec_dir.rglob("kernel.toml"))
        for path in sorted(paths):
            spec = Spec.load(path)
            if spec.id in candidates:
                raise SpecError(f"duplicate spec id: {spec.id}")
            candidates[spec.id] = spec
        indegree = {identifier: 0 for identifier in candidates}
        children: dict[str, list[str]] = defaultdict(list)
        for spec in candidates.values():
            if spec.parent is None:
                continue
            if spec.parent not in candidates:
                raise SpecError(f"{spec.id}: unknown parent {spec.parent}")
            indegree[spec.id] += 1
            children[spec.parent].append(spec.id)
        pending = deque(identifier for identifier, degree in indegree.items() if not degree)
        visited = 0
        while pending:
            identifier = pending.popleft()
            visited += 1
            for child in children[identifier]:
                indegree[child] -= 1
                if not indegree[child]:
                    pending.append(child)
        if visited != len(candidates):
            raise SpecError("spec parent graph contains a cycle")
        self.specs = candidates

    def rows(self) -> list[dict[str, Any]]:
        try:
            stream = self.ledger_path.open("r", encoding="utf-8")
        except FileNotFoundError:
            return []
        with stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                return _read_rows(stream)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def ranked(self, exclude: Iterable[str] = ()) -> list[Spec]:
        """Amdahl-adjusted gain per cost first; least-run order breaks ties."""
        rows = self.rows()
        counts = Counter(row["id"] for row in rows)
        last: dict[str, int] = {}
        for row in rows:
            last[row["id"]] = max(last.get(row["id"], 0), row["epoch"])
        excluded = set(exclude)
        return sorted(
            (spec for spec in self.specs.values() if spec.id not in excluded),
            key=lambda spec: (-spec.priority, counts[spec.id], last.get(spec.id, 0), spec.id),
        )

    def append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Append one durable receipt, rejecting impossible KEEP decisions.

        A pending fixture or a device reported as in use cannot produce a KEEP.
        Unknown fields are retained to preserve referee and device evidence.
        """
        row = _validate_row(entry)
        if row["id"] not in self.specs:
            raise LedgerError(f"unknown spec id: {row['id']}")
        spec = self.specs[row["id"]]
        if DEVICE_CLASSES[row["device"]] != spec.device_class:
            raise LedgerError("ledger device does not match spec device_class")
        if spec.devices and row["device"] not in spec.devices:
            raise LedgerError("ledger device is outside the spec allowlist")
        if row["decision"] == "KEEP":
            if spec.oracle_status == "pending-fixture":
                raise LedgerError("pending-fixture cannot KEEP")
            if spec.device_class == "phone" and row.get("owner_state") != "owner_idle":
                raise LedgerError("phone KEEP requires owner_idle evidence")
            if row.get("oracle_passed") is not True:
                raise LedgerError("KEEP requires oracle_passed true")
            ci = row["ci"]
            if ci is None or ci["lo"] <= 0:
                raise LedgerError("KEEP requires a positive confidence interval")
            if ((spec.direction == "lower" and ci["hi"] >= 1)
                    or (spec.direction == "higher" and ci["lo"] <= 1)):
                raise LedgerError("KEEP requires a confidence interval proving improvement")
        row.setdefault("at", time.time())
        encoded = json.dumps(row, allow_nan=False, sort_keys=True,
                             ensure_ascii=False, separators=(",", ":")) + "\n"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                _read_rows(stream)
                stream.seek(0, os.SEEK_END)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return row

    def council_flags(self) -> list[dict[str, Any]]:
        """Return device/spec pairs with at least three consecutive non-KEEPs."""
        streaks: dict[tuple[str, str], int] = defaultdict(int)
        for row in self.rows():
            pair = (row["device"], row["id"])
            streaks[pair] = 0 if row["decision"] == "KEEP" else streaks[pair] + 1
        return [
            {"device": device, "id": identifier, "since_keep": streak,
             "level": 1}
            for (device, identifier), streak in sorted(streaks.items()) if streak >= 3
        ]
