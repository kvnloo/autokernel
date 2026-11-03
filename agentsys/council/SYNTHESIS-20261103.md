# Council synthesis, 2026-11-03 (facilitator: Opus, Hermes clean)

Seats: Opus 5.5, Fable 5.1, gpt-6-astra, gpt-6.1-sol. All four passed the preflight and both rounds
(exit 0, `api_calls >= 1`, `failed: false`, observed model matched the requested one). Raw texts: `r1-*.md`, `r2-*.md`.
Briefs: `brief-r1.md` and `brief-r2.md`. Seat scripts: `preflight.sh` and `round.sh`.

## Facilitator measurements run between rounds (Measured)
- Tool-wall timestamps are written when generation ends (`memory` p50 0.05 s), so tool wall excludes model generation.
- `agent.slice` has `cpu.max 200000 100000`: 2 CPUs shared by every agent and tool on the 20-thread groot.
- Kills: 93 SIGKILL and 137 SIGTERM in 204,740 tool results over 45 days. Negligible.
- Sleeps: 96.1 of the 105.3 h is literal `sleep N`. By target: 64.9 h log/file watching, 11.9 h CI/gh.
- Turn time vs tokens, session-level: turn time follows output tokens (Astra +19 s per 1k, Sol +9.7 s, Opus +2.3 s) and, for Astra, uncached input. Cached context has almost no effect (+0.008 to 0.026 s per 1k). Cache-read is 96–97%.
- After round 2: of 8,306 pairs of consecutive single-call read-only turns (clean and default profiles, 21 days), 69% had no lexical dependency on the previous result. This is an UPPER bound on batching: Astra and Sol both said semantic dependency must be hand-labelled. The <25% falsifier Opus and Fable proposed is not triggered.

## What the seats converged on (all four, in their own round-2 text)
1. **Unit of optimization:** a verified episode's critical path (model, tool, wait and child spans). Not per-tool time.
2. **Scoring:**
   - Wall time and turns are co-primary.
   - Failures and timeouts are charged at the deadline.
   - Guards: p90 wall, pass-rate non-inferiority, a safety/provenance veto, a second-model spot check.
   - The loop runs only on dev tasks. A sealed confirmation set is split by repo, task family and session.
   - The A/A run sets the KEEP threshold. "Inconclusive" is a valid outcome.
3. **Locate set** is stratified: the long-search pathologies plus ordinary locates, absent-answer cases, stale-index cases and a delegated-task stratum. Each task has a frozen sufficiency rubric and a written answer key.
4. **Instrumentation first:**
   - Hermes records real tool start/end, child links and per-API-call usage (input, cached, output, TTFT).
   - A 24 h host recorder logs cpu.stat, PSI, faults and IO.
5. **Oracle upper bound before locator memory.** Inject the correct entry point. If that saves less than ~15%, don't build Track B.
6. **Waits:**
   - Build a wait-until primitive (process exit, file or log regex, CI state). Log in shadow mode only; never block a wait outright.
   - Start with the 65 h of log watching.
   - The saving is detection lag plus freed turns, which is unmeasured. Raw sleep hours are not the saving.
7. **Prompt diet is demoted.** It now targets cold start, subagent spawn and cache misses only.
8. **New track: output/effort budget per turn class** for the GPT seats.
9. **Harnesses:** port the fx/pi mechanisms into Hermes (warm workers, catalog → retrieval, no forced-terminal guidance, dependency-aware batching). Keep fx and pi as fixed reference arms. No migration.
10. **Hardware:**
    - Replay recorded tool sequences on disposable snapshots, with no model in the loop.
    - Factors: slice quota 2 vs higher; NVMe/xfs vs SATA/btrfs, called a *placement bundle* rather than a filesystem result; declared warm vs first-touch cache.
    - Run an A/A in every cell and randomize AB/BA across days. Log covariates on every trial.
    - No live DB moves and no global cache flush.
    - GPU co-tenancy, mbp placement and phones are postponed.
11. **Which tool when, in four layers:**
    - L0: code defaults in the tools (scope, deadlines, caps, wait-until, safe concurrency).
    - L1: a small resident toolkit card with the rest retrieved per step. Text in kvnloo-skills; retrieval and capabilities in z0intelligence harness-adapters; distributed by `./z0 onboard`.
    - L2: a router in z0intelligence. Deterministic rules first, then kNN. An SLM only if it wins end-to-end including its own dispatch cost.
    - L3: placement in Kerdoios.
    - Tokenomics measures and z0evals validates.
12. **Retracted by their authors:** OOM kills as a lever, the timestamp-semantics threat, and prompt size as a per-turn lever. All four keep "harness cold start is NOT null". grep→rg and Rust rewrites stay dropped. Speculative execution is postponed.

## Disagreements still open, and the measurement that settles each
| Issue | Positions | Settled by |
|---|---|---|
| KEEP statistic | trimmed mean (Opus, Fable) vs deadline-capped untrimmed mean (Astra, Sol: trimming removes the stalls we target) | compute both on the A/A; pick the one with the smaller half-width relative to the target effect |
| Route leaf tasks to pi/fx now? | after porting / once warm Hermes is within 20% of fx (Opus, Fable) vs now, as a capability-matched arm (Astra, Sol) | capability-matched warm-episode comparison (Exp 6) |
| Ship scoped-search defaults without a referee? | yes, it is a bug (Fable, Opus) vs bounded regression tests first (Astra, Sol) | facilitator: ship with a small evidence-coverage regression test, no full ABBA |
| Size of the wait win | 50–85 h (Opus r1) vs 0–9% of wall (Fable) | event-ready → agent-resume lag on instrumented watchers (Exp 5) |
| Is ctx search slow because of IO or its implementation? | CoW/IO (Fable, Opus) vs unattributed (Astra) | same-query replay on both placements with CPU/IO/lock spans (Exp 7) |
| Prompt diet vs generation budget | — | per-call randomized ablation after Exp 1 |

These are proposals for the owner, not adopted protocol.
