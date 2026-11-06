"""Validated, declarative experiment specifications.

Commands are argument vectors.  Loading a spec never runs a command, expands a
template, reads a fixture, or contacts a device.
"""

from __future__ import annotations

import copy
import math
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


class SpecError(ValueError):
    """A spec cannot be scheduled safely."""


DEVICE_CLASSES = {
    "groot-gpu": "gpu",
    "groot-cpu": "cpu",
    "mbp": "cpu",
    "s25": "phone",
    "s22": "phone",
}
MOBILE_TRACKS = frozenset({
    "tts/pocket", "tts/phonon", "asr/moonshine_asr", "asr/parakeet_tdt",
    "asr/kroko_asr", "asr/niagara_asr", "asr/qwen3_asr", "s2s/lfm3",
    "vad/silero_vad", "vad/pulsevad",
})
_PLACEHOLDERS = {"repo", "python"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{field} must be a nonempty string")
    if any(char in value for char in ("\0", "\r", "\n")):
        raise SpecError(f"{field} contains a control character")
    return value


def _number(value: Any, field: str, *, minimum: float = 0,
            inclusive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise SpecError(f"{field} must be a finite number") from exc
    if not math.isfinite(number) or (
        number < minimum if inclusive else number <= minimum
    ):
        qualifier = "at least" if inclusive else "greater than"
        raise SpecError(f"{field} must be finite and {qualifier} {minimum}")
    return number


def _identifier(value: Any, field: str) -> str:
    value = _text(value, field)
    if not _ID.fullmatch(value):
        raise SpecError(f"{field} must contain only letters, digits, '.', '_' or '-'")
    return value


def _argument(value: Any) -> str:
    value = _text(value, "referee argument")
    try:
        parts = list(string.Formatter().parse(value))
    except ValueError as exc:
        raise SpecError("referee argument contains malformed placeholders") from exc
    for _, field, format_spec, conversion in parts:
        if field is not None and (
            field not in _PLACEHOLDERS or format_spec or conversion is not None
        ):
            raise SpecError("referee argument contains an unsupported placeholder")
    return value


def _fields(data: Mapping[str, Any], allowed: set[str], required: set[str],
            field: str) -> None:
    if not isinstance(data, Mapping) or not all(isinstance(k, str) for k in data):
        raise SpecError(f"{field} must be a table with string keys")
    extra = set(data) - allowed
    missing = required - set(data)
    if extra:
        raise SpecError(f"unknown {field} fields: {', '.join(sorted(extra))}")
    if missing:
        raise SpecError(f"missing {field} fields: {', '.join(sorted(missing))}")


@dataclass(frozen=True)
class Resources:
    """Requested memory is expressed in bytes; CPUs are whole logical CPUs."""

    mem: int
    cpus: int
    gpu_mem: int = 0

    def __post_init__(self) -> None:
        for field, minimum in (("mem", 1), ("cpus", 1), ("gpu_mem", 0)):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise SpecError(f"resources.{field} must be an integer >= {minimum}")


def _oracle(data: Any, status: str) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise SpecError("oracle must be a table")
    kind = data.get("kind")
    common = {"kind", "check"}
    schemas = {
        "receipt": (common | {"checks"}, common),
        "mobile": (common | {"track"}, common | {"track"}),
        "byte-equal": (
            common | {"fixture", "sha256", "baseline", "candidate"},
            common | {"fixture", "sha256"},
        ),
        "physarum-npz": (
            common | {"baseline", "candidate", "tube_key", "backbone_key", "d_key",
                      "rtol", "atol"},
            common | {"baseline", "candidate", "tube_key", "backbone_key", "d_key",
                      "rtol", "atol"},
        ),
    }
    if not isinstance(kind, str) or kind not in schemas:
        raise SpecError("oracle.kind must be receipt, mobile, byte-equal or physarum-npz")
    allowed, required = schemas[kind]
    _fields(data, allowed, required, "oracle")
    result = copy.deepcopy(dict(data))
    _text(result["check"], "oracle.check")
    for field in ("fixture", "baseline", "candidate", "tube_key", "backbone_key",
                  "d_key", "track"):
        if field in result:
            _text(result[field], f"oracle.{field}")
    for field in ("fixture", "baseline", "candidate"):
        if field in result:
            path = Path(result[field])
            if path.is_absolute() or ".." in path.parts or path == Path("."):
                raise SpecError(f"oracle.{field} must be a relative file path without '..'")
    if "checks" in result:
        checks = result["checks"]
        if not isinstance(checks, (list, tuple)) or not checks:
            raise SpecError("oracle.checks must be a nonempty array")
        checks = [_identifier(value, "oracle.checks") for value in checks]
        if len(checks) != len(set(checks)):
            raise SpecError("oracle.checks contains duplicate names")
        result["checks"] = checks
    if kind == "byte-equal":
        digest = result["sha256"]
        if not isinstance(digest, str) or not (
            _SHA256.fullmatch(digest) or (status == "pending-fixture" and digest == "")
        ):
            raise SpecError("oracle.sha256 must be a SHA-256 digest; only pending fixtures may omit it")
        result["sha256"] = digest.lower()
        if ("baseline" in result) != ("candidate" in result):
            raise SpecError("oracle.baseline and oracle.candidate must be supplied together")
    if kind == "physarum-npz":
        for field in ("rtol", "atol"):
            result[field] = _number(result[field], f"oracle.{field}", inclusive=True)
    return result


@dataclass(frozen=True)
class Spec:
    id: str
    objective: str
    metric: str
    referee: tuple[str, ...]
    oracle: dict[str, Any]
    device_class: str
    resources: Resources
    max_runtime: float
    parent: str | None = None
    amdahl_share: float = 1.0
    expected_gain: float = 1.0
    cost: float | None = None
    oracle_status: str = "ready"
    source: Path = Path("<memory>")
    devices: tuple[str, ...] = ()
    working_directory: str | None = None
    direction: str = "lower"

    @property
    def priority(self) -> float:
        return self.expected_gain * self.amdahl_share / (
            self.max_runtime if self.cost is None else self.cost
        )

    @classmethod
    def load(cls, path: str | Path) -> "Spec":
        path = Path(path)
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, ValueError) as exc:
            raise SpecError(f"cannot load {path}: {exc}") from exc
        try:
            return cls.from_mapping(data, source=path)
        except SpecError as exc:
            raise SpecError(f"{path}: {exc}") from exc

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *,
                     source: str | Path = Path("<memory>")) -> "Spec":
        required = {"id", "objective", "metric", "referee", "oracle", "device_class",
                    "resources", "max_runtime"}
        allowed = required | {"parent", "amdahl_share", "expected_gain", "cost",
                              "oracle_status", "devices", "working_directory", "direction"}
        _fields(data, allowed, required, "spec")
        identifier = _identifier(data["id"], "id")
        objective = _text(data["objective"], "objective")
        if objective not in {"speed", "capability", "intelligence"}:
            raise SpecError("objective must be speed, capability or intelligence")
        device_class = _text(data["device_class"], "device_class")
        if device_class not in {"gpu", "cpu", "phone"}:
            raise SpecError("device_class must be gpu, cpu or phone")
        metric = _text(data["metric"], "metric")
        argv = data["referee"]
        if not isinstance(argv, (list, tuple)) or not argv:
            raise SpecError("referee must be a nonempty argument array")
        referee = tuple(_argument(value) for value in argv)
        resources = data["resources"]
        _fields(resources, {"mem", "cpus", "gpu_mem"}, {"mem", "cpus"}, "resources")
        resources = Resources(**dict(resources))
        if device_class != "gpu" and resources.gpu_mem:
            raise SpecError("only GPU specs may request gpu_mem")
        if device_class == "gpu" and resources.gpu_mem == 0:
            raise SpecError("GPU specs must request positive gpu_mem")
        runtime = _number(data["max_runtime"], "max_runtime")
        share = _number(data.get("amdahl_share", 1), "amdahl_share")
        if share > 1:
            raise SpecError("amdahl_share must not exceed 1")
        gain = _number(data.get("expected_gain", 1), "expected_gain", inclusive=True)
        cost = _number(data.get("cost", runtime), "cost")
        if not math.isfinite(gain * share / cost):
            raise SpecError("priority must be finite")
        status = _text(data.get("oracle_status", "ready"), "oracle_status")
        if status not in {"ready", "pending-fixture"}:
            raise SpecError("oracle_status must be ready or pending-fixture")
        parent = data.get("parent")
        if parent is not None:
            parent = _identifier(parent, "parent")
            if parent == identifier:
                raise SpecError("a spec cannot be its own parent")
        devices = data.get("devices", ())
        if not isinstance(devices, (list, tuple)):
            raise SpecError("devices must be an array")
        devices = tuple(_text(value, "device") for value in devices)
        if len(devices) != len(set(devices)):
            raise SpecError("devices contains duplicates")
        if any(DEVICE_CLASSES.get(device) != device_class for device in devices):
            raise SpecError("devices must name fleet members of the requested device_class")
        directory = data.get("working_directory")
        if directory is not None:
            directory = _text(directory, "working_directory")
            if not Path(directory).is_absolute():
                raise SpecError("working_directory must be absolute")
        direction = _text(data.get("direction", "lower"), "direction")
        if direction not in {"lower", "higher"}:
            raise SpecError("direction must be lower or higher")
        oracle = _oracle(data["oracle"], status)
        if device_class == "phone":
            if oracle["kind"] != "mobile" or oracle.get("track") not in MOBILE_TRACKS:
                raise SpecError("phone specs must select a supported mobile oracle track")
            if referee != ("python3", "{repo}/turbo/mobile.py", "--track", oracle["track"]):
                raise SpecError("phone specs must use the fixed turbo/mobile.py wrapper")
        elif oracle["kind"] == "mobile":
            raise SpecError("mobile oracles require device_class phone")
        return cls(
            id=identifier, objective=objective, metric=metric, referee=referee,
            oracle=oracle, device_class=device_class,
            resources=resources, max_runtime=runtime, parent=parent,
            amdahl_share=share, expected_gain=gain, cost=cost, oracle_status=status,
            source=Path(source), devices=devices, working_directory=directory,
            direction=direction,
        )
