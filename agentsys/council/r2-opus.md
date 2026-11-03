Council seat: Opus 5.5, round 2

Labels: [M] Measured, [S] Source, [I] Inference.

What the new data changes

1. Tool wall excludes the model's own generation time [M]. Fable and Astra both raised the timestamp threat, and it is now settled. Sibling calls in one turn still share a stamp, so per-tool hours remain an upper bound.

2. The limit is bigger than nproc=2 suggested. All of agent.slice gets 2 CPUs, shared by every harness and every tool [M].
   - Parallel tool calls and concurrent subagents compete for those same 2 CPUs. Any batching gain is capped by this quota, so the quota measurement has to come before the parallelism work [I].
   - Throttling so far is 12 of 542 periods [M], too short a window to conclude anything.

3. Kills: 93 SIGKILL and 137 SIGTERM out of 204,740 tool results [M]. Retries from OOM kills are negligible.

4. Sleeps: 91% of the 105 h of sleep-containing commands is literal `sleep N` [M], and 65 h of it is followed by log or file watching [M]. Sol and Astra were right that waiting isn't automatically waste ("Separate work finished sooner from agent remained available sooner"). But a fixed sleep before a tail or grep is a readiness check that a wait-until-regex primitive replaces directly [I].

5. Turn time follows output tokens and cache misses, not cached prompt size [M, ecological]. Cache-read is 96–97%, so cutting the prompt mostly helps cold start and subagent spawn, not steady-state turns [I]. The new lever is output and reasoning length on the GPT seats: Astra +19 s per 1k output tokens [M].

What I retract, narrow and defend from my round-1 text

Retract:
- The threat that the timestamp marks stream start.
- OOM kills as a hidden source of retries.
- "Turn latency tracks input tokens" as a per-turn lever (attack surface 4 as I wrote it).
- "Polling overshoot is SQL only, no new runs." Overshoot can't be measured from stored data [M], so it needs forward instrumentation.

Narrow:
- Prompt diet now applies to cold start, subagent spawn and cache-miss turns only.
- The wait track starts with log/file watching (65 h), then CI/gh (12 h).
- "nproc=2 throttles builds" becomes "a 2-CPU slice shared by all agents caps parallelism"; the size of the effect is unknown until the 24 h log.
- Locate set: I accept Astra, Sol and Fable. Stratify it with ordinary short locates, no-answer cases and stale-memory cases, split by repo and session.

Defend:
- Critical path as the unit of optimization (Astra and Sol frame it the same way).
- Mean or trimmed mean as the headline, with p90 as a guard (Fable: "stalls live in tails").
- Harness cold start is not null (all four seats agree).
- Oracle-path upper bound before building locator memory.
- RLM and SoL-Pi stay out of the speed referee (all agree).
- Port rather than migrate.

Concede: turns become co-primary with wall (Fable). Speculative execution is postponed (Fable: "buys nothing on the median" at 0.3 s tools).

(a) Objectives and KEEP rule

Each track (Locate, Execute, Resume) is scored per episode on two co-primary measures: 20%-trimmed-mean wall seconds to evidence that passes a frozen sufficiency rubric, and model turns. Failures and timeouts count at the timeout value. The Pareto report adds p90 wall, output tokens, cost, owner minutes and pass rate.

KEEP a candidate only when all of these hold:
- The paired-bootstrap 95% upper bound on the wall ratio, resampled by task family, is below 1 − max(3%, the A/A half-width).
- Pass rate is non-inferior within 5 points.
- p90 is no worse than +10%.
- A spot check on a second model agrees in direction.

The loop runs on dev only. The sealed held-out set is touched once, for finalists.

(b) Experiments for the next 2 weeks, in order

1. Host recorder plus per-call usage logging.
   - What: cpu.stat for agent.slice, PSI, iostat and GPU every 5 s into SQLite, plus Hermes recording input, cached, output and reasoning tokens for every API call.
   - Owner: Tokenomics, .files, hermes-agent. Cost: about 0.5 day.
   - Falsifier for the quota: throttled periods under 1% during agent-busy hours over 24 h. If so, drop the quota work.

2. Critical-path rebuild, then an A/A of the referee.
   - What: nest children inside parents, label adjacent single-call pairs for argument independence, then run the A/A.
   - Owner: z0evals. Cost: 1 day plus about 40 runs.
   - Falsifiers: an A/A half-width above 15% means the set must grow before any KEEP. Fewer than 25% independent pairs means batching is dropped.

3. Ship scoped search defaults and timeouts for fd, grep and unscoped roots as plain code, with no referee.
   - Owner: hermes-agent. Cost: hours.
   - Falsifier: fd p90 is still above 30 s in next week's profile.

4. A wait-until primitive (process exit, file or log regex, PR/CI state), plus a shadow law that logs each sleep's intent and its overshoot going forward.
   - Owner: hermes-agent and the laws registry. Cost: 1–2 days.
   - Falsifier: across 1 week of shadow logs, overshoot is under 15% of sleep seconds.

5. Oracle-path upper bound on 20 Locate tasks, then a toolkit-card A/B.
   - Owner: z0evals; card text in kvnloo-skills.
   - Falsifier: the oracle saves under 15% of wall, so the turn floor dominates.

6. Output and effort for the GPT seats: Sol and Astra at low vs default effort on the Locate dev set.
   - Owner: hermes-agent profiles and vLLM-SR.
   - Falsifier: the wall difference is within the A/A half-width, or pass rate drops.

7. Warm worker or ACP for subagents plus a lean catalog, by extending harness-speed-v1 with lifecycle parity and a delegated task class.
   - Owner: hermes-agent and z0evals.
   - Falsifier: warm vs cold delegated wall improves by under 10%.

8. Model-free trace replay for the hardware design in (c).
   - Owner: Kerdoios and .files. Cost: 1 day, no tokens.
   - Falsifier: ctx search cold/warm ratio under 2× and quota-insensitive replays.

(c) Hardware and stress design

- Replay recorded read-only tool sequences from Locate runs and the terminal tail with no model in the loop, on disposable DB and index snapshots. Never touch live databases, never flush global caches.
- Factors: agent.slice quota (2 vs 6 CPUs) × snapshot placement (NVMe xfs, SATA btrfs, btrfs nodatacow; this is a placement bundle) × first-touch vs warm.
- Stress lane: controlled stress-ng memory and CPU co-tenant levels, plus llama.cpp serving vs idle for vLLM-SR route latency.
- An A/A in every cell. Randomized AB/BA blocks across days. Recorder covariates on every trial.
- Bootstrap p50 and p90 by sequence.
- Promote to an agent-in-loop run only for factors that move p90 by more than 2× the A/A spread. groot vs mbp only for build and test classes, under the fleet-build rule.

(d) Harness decision

Port fx/pi mechanisms into Hermes: warm workers, a near-zero resident catalog with retrieval, and no forced-terminal guidance. Keep fx and pi as fixed reference arms in every referee run. Route leaf subagents that need no memory, skills or laws to a lean profile experimentally, and only for as long as warm, lean Hermes is more than 20% slower than fx on the same tasks. No migration.

(e) Which tool when

1. Code defaults (scope, timeouts, caps, wait-until) live in hermes-agent and z0intelligence harness-adapters.
2. Capability cards are text in kvnloo-skills: a small resident set, the rest retrieved per step.
3. The router (kNN first, an SLM only if it beats kNN end-to-end) lives in z0intelligence and is fed by experiments 2 and 5.
4. Host and harness placement belongs to Kerdoios, measurement to Tokenomics, distribution to `./z0 onboard`, and enforcement to the laws registry.

(f) Remaining disagreements and the measurement that settles each

- Trimmed mean vs median headline (draft, Astra). Settle with experiment 2: pick the statistic with the smaller A/A half-width relative to the effect each would detect on dev.
- When to route to pi/fx: now (Astra) vs once Hermes is within 20% (Fable, me). Settle with experiment 7: if lean, warm Hermes is within 20% of fx-base, don't route.
- Fresh oneshots vs replay with the original context prefix. Settle with a 10-task pilot: if historical turn counts track more closely with the prefix, use the prefix.
- How much prompt diet matters per turn (Fable ranked it #2). Settle with experiment 1: a within-session regression on per-call usage.
- Whether the shared 2-CPU quota caps parallel gains. Settle with experiment 8 at quota 2 vs 6, with concurrency 1 vs 3.

Standing-rule check: nothing saved. This session has no memory or skill_manage tool, and the brief sets council protocol, not a new standing rule.
