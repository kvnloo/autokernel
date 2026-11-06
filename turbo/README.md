# HC-TURBO

An admission-controlled dispatcher for a directory of hill-climb specifications.
It fills available slots, retains every run's evidence, and records only
`KEEP`, `REVERT`, `NOISE`, or `FAIL`. A successful process exit is insufficient
for a KEEP.

This implementation builds on the mobile seams at autokernel commit
`b5c0e58a13eb5d476f28ca9a4534bb28cc077c7d`. It wraps the fixed mobile referee;
`mobile/fulltime.py`, the original referee, candidates, and eval sets are unchanged.

## Run

Use an existing Python 3.11+ environment (Python 3.10 additionally needs the
already-available `tomli` module). Runtime integrations are the host's existing
`systemd-run`, `nvidia-smi`, `fleet`, `fleet-build`, and `adb` commands. The
dispatcher never installs software, starts a model service, or discovers phone
serials. Supply each connected phone's serial through the existing `S25_SERIAL`
and `S22_SERIAL` environment variables or the matching CLI options. An unset
serial holds that phone and leaves the other devices available.

Run on groot, from the repository root:

```sh
python3 -m turbo.turbo once
python3 -m turbo.turbo start
python3 -m turbo.turbo status
python3 -m turbo.turbo stop
```

`once` fills one wave and waits for its admitted runs to finish. `start` stays in
the foreground, reloads specs, and fills free slots continuously. `status` and
`stop` only inspect or write local control files; they do not contact devices.
The direct entry point `python3 turbo/turbo.py once` is also supported.

The default paths are `turbo/specs` and `turbo/state`; `--specs` and `--state`
override them. `--interval` is a polling interval in seconds, greater than zero
and at most ten. A host-user lock permits one fleet dispatcher, including when
different state directories are selected.

`stop` creates the state directory's `STOP` file. A STOP drains current runs and
prevents new launches, including a STOP arriving during source staging. Remove
that file explicitly to begin a new wave. SIGTERM or SIGINT instead cancels
owned runs. No process-name scan, global `pkill`, default-branch operation, or
automatic council invocation is involved.

## Specification and queue

Each experiment lives in a `kernel.toml` under the specs directory. Flat TOML
files are also accepted. Invalid types, unknown fields, invalid resource
requirements, unknown parents, cycles, duplicate IDs, and malformed oracles
make the entire reload fail closed.

| Field | Meaning |
|---|---|
| `id` | Stable letters/digits/dot/underscore/hyphen identifier |
| `objective` | `speed`, `capability`, or `intelligence` |
| `metric` | Referee's metric name |
| `direction` | `lower` (default) or `higher`; CI is candidate/control |
| `referee` | Argument array, executed without a host shell expansion |
| `oracle` | Structured equivalence check; see below |
| `oracle_status` | `ready` or `pending-fixture` |
| `device_class` | `gpu`, `cpu`, or `phone` |
| `devices` | Optional placement allowlist within the requested class |
| `resources.mem`, `resources.gpu_mem` | Bytes; GPU memory is required for GPU specs |
| `resources.cpus` | Positive whole logical CPUs |
| `max_runtime` | Positive finite seconds, including observer lifetime |
| `working_directory` | Optional host-absolute referee source directory |
| `parent` | Optional parent spec ID, checked for existence and cycles |
| `amdahl_share` | Fraction in `(0, 1]` |
| `expected_gain`, `cost` | Scheduling priors; nonnegative gain and positive cost |

Priority is `expected_gain * amdahl_share / cost`. Least-run order breaks equal
priorities. The dispatcher visits specs in rounds, so continuously successful
high-priority work does not exclude every lower-priority experiment. An offline
device does not block the next round on healthy devices. The twelve seeds use
equal policy weights; those weights are not measured gains or Amdahl estimates.

`ledger.jsonl` is append-only, file-locked, flushed and fsynced. Each record has
`epoch`, `id`, `device`, `cmd`, `decision`, `ci`, and `run_dir`, with timestamps,
oracle evidence and owner-state labels. A truncated/corrupt ledger halts writes
instead of silently discarding evidence. Three consecutive non-KEEPs for the
same device/spec produce a council flag in `status.json`; a KEEP resets that
streak. This reuses the three-non-KEEP trigger in `mobile/council.py`, without
launching its model sessions.

`status.json` is replaced atomically. It reports each device's capacity, occupied
slots/current IDs, last decision, runs completed in the preceding hour, skip
reasons, and council flags. Foreground window names are not persisted there.

## Admission and ownership

| Device | Cap | Admission and execution |
|---|---:|---|
| `groot-gpu` | 2 | Fresh MemAvailable and GPU-0 free memory; GPU 0 pinned for execution |
| `groot-cpu` | 1 | Fresh MemAvailable and online CPU count |
| `mbp` | 3 | Fresh installed fleet single-node probe; Kueue bench submission |
| `s25` | 1 | Fresh battery, live HAL temperature, call, audio, activity and focus readings |
| `s22` | 1 | Same phone gates, with an explicit independent serial |

Groot requires at least 6 GiB MemAvailable and enough memory for the spec.
Shared CPU/GPU reservations are subtracted conservatively. GPU-0 free memory
must cover the spec and other reserved GPU work. Every groot job is bounded by
`systemd-run --user --scope -p MemoryMax=...` and a CPU quota. Source staging is
followed by a second live reading immediately before launch.

Phone `resources` bound the host worker, including its quality scorer. Each
phone seed reserves 8 GiB and one host CPU as a configurable budget, not a
measured footprint. These reservations share Groot's memory/CPU accounting;
a phone launch needs both fresh host capacity and fresh phone telemetry. Its
owned systemd scope disables CUDA so quality scoring cannot consume an
unreserved GPU. The existing candidate still controls benchmark threads on
the phone. Real admission can therefore admit fewer than the maximum slots.

MBP execution always uses `fleet-build --node mbp --class bench
--checkpoint-safe`; it never falls back to direct execution or another host.
Source is staged, outputs are fetched, and an interrupted incomplete referee
invocation restarts from its isolated source snapshot. A completed invocation
has an atomic checkpoint and is not replayed on Kueue readmission. Referees
must confine mutable benchmark state to their supplied working directory.

The fleet observer can detach from live remote work. Cancellation therefore
uses the exact job ID emitted by that owned observer with `fleet kill mbp ID`.
An unidentified or unconfirmed remote lifetime holds further MBP submissions.
Unverified phone cleanup similarly holds that phone. These holds persist in
`holds.json` across restarts; clear an entry only while stopped and after
verifying the owned work has ended.
Only fleet-build's verified, explicit no-admission exit is automatically
retryable. The implementation follows the installed fleet interface inspected
at [.files commit bfa8252](https://github.com/kvnloo/.files/blob/bfa825249b80cce957d24ad724be0d8f11098bdf/scripts/fleet-build).

Phone admission requires battery at least 30% or charging, AP at most 42 C,
battery at most 38 C, and known idle telephony/audio call states. Unknown,
nonfinite, stale, or cached-only thermal readings hold admission. A call or
focus change interrupts work. Focus equality uses an opaque digest.

Runs are tagged `owner_in_use` or `owner_idle`; owner activity is allowed for
measurement, but only idle confirmation can KEEP or update calibration. The wrapper also monitors
during long phone blocks and isolates device state from the existing mobile
supervisor. Only reviewed generated shell commands and benchmark file transfers
are allowed. `adb push`/`pull` are permitted; installs, UI events, activity
launches, and display operations are rejected. Observing the focus state does
not operate a display. Cleanup uses run-qualified paths and owned PIDs, verifies
process disappearance, then removes only that run's temporary phone asset tree.

## Referees, oracles and seeds

Host referees run in a fresh copy of their source directory. Host-absolute
referee paths are configuration: the corresponding source file is copied and
the executable argument is rebased into that run's working directory. Existing
output directories and old `result.json` files are excluded. The dispatcher
retains stdout, stderr, raw artifacts, the launch command and the source
snapshot under `state/runs/<unique-id>`.

A host referee supplies a `result.json` in its isolated working directory, or
a complete JSON decision object on stdout. Its receipt fields are:

| Field | Required evidence |
|---|---|
| `decision` | One of the four ledger decisions |
| `ci` | For KEEP: positive finite ordered `lo`, `mid`, `hi` candidate/control ratios |
| `oracle_passed` | Boolean true for an attested receipt oracle |
| `checks` | Named boolean assertions required by the configured oracle |
| `artifacts` | Relative baseline/candidate output paths for byte comparison |
| `fixture_sha256` | Exact pinned input digest for byte comparison |

No receipt, malformed output, a nonzero exit, failed checks or a CI that does
not establish improvement prevents KEEP. The fixed external referee owns its
algorithm-specific measurement and CG tolerance; this dispatcher does not
fabricate those values.

- **HC-1** calls exactly `bash /mnt/zer0models/workspace/zer0/data/operations/z19-research-20261105/hc1/repo/run_hc1.sh`.
  Its receipt must attest `aa_passed`, `tube_sets_equal`,
  `backbone_sets_equal`, and `d_within_cg_tolerance`. Existing `out/*.npz`
  artifacts remain in the run evidence. The optional `physarum-npz` oracle can
  independently compare configured NPZ pairs and array keys with explicit
  tolerances, after those actual output names/tolerances are established.
- **HC-2** calls exactly `python3 /mnt/zer0models/workspace/zer0/data/operations/z19-research-20261105/hc2/bench_hc2.py`.
  Its seed names `fixtures/hc2-redaction.jsonl` and intentionally leaves its
  unknown SHA-256 empty with `oracle_status = "pending-fixture"`. It can never
  KEEP in this state, even if the referee reports KEEP. Once a real fixture is
  pinned and the status is explicitly changed to `ready`, the byte-equal oracle
  requires matching nonempty complete-corpus outputs, the exact input digest,
  and `complete_fixture`/`overlapping_secrets` assertions. No replacement corpus
  or fabricated digest is committed.
- **Ten mobile specs** map to the real candidate track IDs: `tts/phonon`,
  `tts/pocket`, `s2s/lfm3`, `asr/kroko_asr`, `asr/moonshine_asr`,
  `asr/niagara_asr`, `asr/parakeet_tdt`, `asr/qwen3_asr`, `vad/pulsevad`,
  and `vad/silero_vad`. The adapter selects the next baseline, A/A noise
  calibration, or A/B confirmation from existing candidates and bounded thread
  variations, using isolated state. The original VAD lanes lack a correctness
  scorer; their improvements remain NOISE until a real oracle is available.

HC-1/HC-2 source paths and mobile binary/model locations are host-owned inputs.
They are not included in this repository. Missing inputs or unsupported output
contracts produce a visible failure/hold, never invented success. The seed set
does not invent a GPU workload: GPU slots become eligible when real GPU specs
are added to the same queue.

## Verification and remaining evidence

The pytest suite uses fake device readings and fake processes. It does not
issue real phone commands, launch referees, install software, or use hardware.
It covers schema validation, pending fixtures, queue ordering/cycles/ledger
append, admission, cap filling, STOP drain, final admission, call/focus aborts,
owner-only confirmation, checkpoint evidence, and command restrictions.

```sh
python3 -m pytest turbo/tests -q
```

**All tests are UNRUN in the authoring session.** No benchmark, hardware canary,
speedup, or fixture equivalence is claimed. The next local verification is the
pytest command above, then one admitted `once` wave using the actual installed
fleet and host referees. Verify the host receipt contract, S22 assets and live
HAL fields, mobile quality dependencies, and exact remote cancellation. HC-2
remains pending until its real fixture and SHA-256 are supplied.

Requirement and clarification:
[factory issue 391](https://github.com/kvnloo/oss-factory/issues/391) and
[HC-TURBO clarification](https://github.com/kvnloo/oss-factory/issues/391#issuecomment-6679525583).
