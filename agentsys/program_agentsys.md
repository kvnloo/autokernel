# AgentSys: AutoKernel's method applied to the whole agent system

Same loop as `program.md` (profile → rank by share of end-to-end time → optimize one bottleneck
at a time with keep/revert → integrate), with the "model" being Kevin's real agent workload and
the "kernels" being the surfaces that decide how agents find things and wait.

| AutoKernel | AgentSys |
|---|---|
| PyTorch model + input shape | 45-day trace corpus (Hermes state.db; AgentsView for other harnesses) |
| `profile.py` per-op breakdown | `extract_hermes.py` + `analyze.py` + `discovery.py` → `PROFILE-*.md` |
| Amdahl ranking of kernels | ranked bottleneck table in `PROFILE-20261103.md` |
| `bench.py` correctness + speed | Locate referee (below): correct evidence found + wall seconds + turns |
| one kernel file edited per experiment | one surface per experiment (card text, locator index, one law, one tool) |
| keep if faster and correct | KEEP only if ratio < 1 − max(3%, A/A half-width) and every gate holds |
| `results.tsv` | `ledger.jsonl`, append-only, failures included |

## Objective (one scored number)

**Median wall seconds from task start to first correct evidence on the frozen Locate set**,
with turns-to-evidence as the tie-breaker. Hard gates: answer correct (path/issue/session matches
the label), no increase in tokens per task above 10%, no new tool errors.

Production guard metrics (recomputed weekly by the profiler, never used to KEEP): share of
turns that are pure search, empty-retry rate, blocking-wait hours, single-call turn share.

## Locate set (built from our own history, not synthetic)

Source: discovery runs of ≥5 search turns in the profile (1,795 runs, 45 days). For each run take
the owner/parent ask that started it and the evidence the run finally used (the read_file path,
issue, session id, or URL in the reply). Keep the ones where the label is unambiguous. Split
by kind: repo path, issue/PR, past session/decision, host fact, live service state. Freeze
60 dev + 40 held-out. Editing the set starts a new epoch with re-baselines.

## Referee

ABBA interleaved fresh Hermes oneshot sessions (candidate vs incumbent config), same model per
track (default Sol 6.1 via the semantic router; model choice is a separate Pareto decision),
same host, idle gate on groot memory (`/proc/pressure/memory` full avg60 < 1) because the
host runs at ~6 GB available. Paired bootstrap CI of the median ratio. A/A first to size noise.
Reuse `mobile/` pieces on branch `z0live/s25-autoresearch` (`council.py`, screen/confirm tiers).

## Tracks (one editable surface each)

| Track | Surface | Owner of the surface | Hypothesis |
|---|---|---|---|
| A0 | Build Locate set + referee | autokernel `agentsys/` | enabler |
| A | Toolkit card: one "where to look / which tool / when to batch / how to wait" page | kvnloo-skills (hub decision 2026-10-28) | cuts discovery turns and single-call turns |
| B | Locator memory: concept → canonical source index mined from runs that ended a search | z0intelligence memory MCP (`mcp__z0int_memory__search`) | cuts empty retries; answers "where is X" in 1 turn |
| C | Wait law: foreground `sleep > 30 s` → `would_deny`, suggest background + notify | laws registry `/mnt/zer0models/laws` (shadow first) | cuts the 105 h of blocking sleeps |
| D | ctx search latency (p50 46 s) | ctx CLI / its MCP | fix, or demote in the card behind session_search + AgentsView FTS |
| E0 | Cross-harness timing: OMP, Claude, Codex turn and tool times from AgentsView messages | tokenomics (measurement) | coverage, not speed |
| E1 | Host recorder: per-tool latency × host × load (cheap node exporter to sqlite, k3s nodes) | .files IaC | needed before any placement track; Kerdoios owns placement |
| F | Scoped search defaults (fff/z0_file_search, scoped rg roots) | z0intelligence harness-adapters | tail only (~10 h); expected small |

Null by measurement (do not build): grep→rg swaps for small searches, CLI cold-start work,
Rust rewrites of tools already under the 0.17 s harness floor.

## v1 after council 2026-11-03 (supersedes the objective and track order above; see `council/SYNTHESIS-20261103.md`)

Objective: per track (Locate now, Execute next), deadline-capped episode wall time to evidence that
passes the frozen sufficiency rubric. Turns are co-primary. The statistic (trimmed mean or capped
mean) is chosen on the A/A. Track B is gated on the oracle bound.

| # | Experiment (2 weeks) | Owner | Falsifier |
|---|---|---|---|
| 1 | Per-call usage + real tool start/end + child links in Hermes; 24 h host recorder (cpu.stat of agent.slice, PSI, faults, IO) | hermes-agent, Tokenomics, .files | slice throttling <1% over 24 h → drop quota work |
| 2 | Stratified Locate set + rubric; referee A/A | z0evals | half-width >15% → grow set or reduce noise before any KEEP |
| 3 | Oracle entry-point bound, then toolkit card / scoped fallback A/B | z0evals, kvnloo-skills, z0int | oracle saves <15% → no locator memory |
| 4 | Scoped-search defaults + fd/grep deadlines, with an evidence-coverage regression test | hermes-agent | fd p90 still >30 s next profile |
| 5 | wait-until primitive (exit / file / log regex / CI) + shadow lag logging, starting with log watching (65 h) | hermes-agent, laws registry | measured detection lag <15% of sleep s |
| 6 | Lifecycle + output budget: warm vs cold subagents, lean catalog, low vs default effort on Sol/Astra; fx/pi reference arms; lean-leaf routing arm | hermes-agent, z0evals | gains vanish at capability parity, or pass drops |
| 7 | Model-free hardware replay: quota 2 vs higher × NVMe/SATA snapshot × first-touch/warm; ctx attribution | Kerdoios, .files, ctx | no cell moves p90 > 2× A/A spread |
| 8 | Bounded 2-way batching of hand-labelled independent reads (69% lexical upper bound) | hermes-agent, z0int | few semantically independent pairs, or contention erases gains |

## Running the loop (implemented 2026-11-03)

| AutoKernel | AgentSys file |
|---|---|
| `prepare.py` (fixed inputs) | `mine_locate.py` mines candidates from state.db; the frozen set is `$AGENTSYS_DATA/locate-v1.jsonl` (private) |
| `bench.py` (correctness + time) | `referee.py`: real `hermes chat --oneshot` episodes, ABBA, idle-gated, failures charged at the deadline |
| keep/revert + `results.tsv` | `loop.py aa` (noise floor) and `loop.py ab` (KEEP/DROP/INCONCLUSIVE, promotes the arm), `ledger.jsonl` |
| one experiment | `experiment.sh <exp> <track> <candidate-arm>` |

Arms are JSON in `$AGENTSYS_DATA/arms/` (`incumbent.json` is the current best). A surface is whatever
the arm changes: `preamble_file` (toolkit card), model/provider/reasoning, max turns. The referee runs on
the free Kilo Opus 5.5 lane by default so the loop does not consume paid subscriptions.
`AGENTSYS_DATA` defaults to `/mnt/zer0models/sft-svlm/data/agentsys` (0700; trace-derived, never commit).

## Loop rules

- Each direction gets 3 rounds, then keep, drop or redesign.
- Stall = 3 consecutive non-keeps on a track. 1st stall: 1-round council (Opus 5.5, Fable 5.1,
  gpt-6-astra, gpt-6.1-sol). 2nd: 2 rounds + re-profile + research wave. 3rd: stop and record.
- Model-free ticks run the queue (extract, rescore, referee commands); free lanes only for
  non-code judgment; Opus and council on FLAGS.
- Unattended continuation only via Hermes `/goal`, armed by Kevin.
