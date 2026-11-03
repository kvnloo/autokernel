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

## Loop rules

- Each direction gets 3 rounds, then keep, drop or redesign.
- Stall = 3 consecutive non-keeps on a track. 1st stall: 1-round council (Opus 5.5, Fable 5.1,
  gpt-6-astra, gpt-6.1-sol). 2nd: 2 rounds + re-profile + research wave. 3rd: stop and record.
- Model-free ticks run the queue (extract, rescore, referee commands); free lanes only for
  non-code judgment; Opus and council on FLAGS.
- Unattended continuation only via Hermes `/goal`, armed by Kevin.
