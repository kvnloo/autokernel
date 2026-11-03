Fable 5.1 — round 2 (cross-examination)

Standing-rule check: no new rule in this message; this session still exposes no memory/skill_manage tool, nothing saved.

RETRACT / NARROW / DEFEND (my round-1 text)

RETRACT
- "OOM kills show up as retry turns; count them." Measured: 230 kills in 204,740 results (0.11%). Not a lever. Drop from attack surface 5.
- "Turn time vs tool wall may include generation." Measured #1 settles it: stamp is at generation end; tool wall excludes model generation. Opus raised the same doubt; both withdrawn.
- "Prompt diet → turn p50 8.1→~6 s" as track #2. Measured #5: cached context slope is 0.008–0.026 s/1k; at 96–97% cache-read, catalog size barely touches turn time. Prompt diet is a cost and cold-start lever, not a per-turn latency lever.

NARROW
- Waiting "−5–8% total wall": Measured #4 says 91% of sleep wall is the literal sleep and 65 h is log/file watching. The avoidable part is the overshoot, still unmeasured. I now put the range at 0–9% of total wall, settled by experiment 5 below.
- "cgroup quota shapes the tails": the quota is real (2 CPUs for the whole agent.slice, Measured #2), but throttling in the short window is 1.94 s/54 s ≈ 3.6% of periods. Hypothesis survives; it needs the 24 h log before any replay matrix.
- "Astra slower is provider/effort": Measured #5 (output +19 s/1k for Astra vs +2.3 s for Opus) supports effort/output length as the cause. Inference: Astra's seat is reasoning-heavy, and output budget, not prompt size, is the GPT-seat lever.

DEFEND
- Turn count co-primary, pass rate a veto, p90 guard. Nobody opposed; Astra and Sol add a sufficiency rubric, which I accept.
- Model-free trace replay for hardware. Astra/Sol converge ("replay read-only DB/index queries on snapshots"); Opus's stress-ng-on-referee design stays as the confirmation lane only.
- Port, don't migrate; route only after Hermes is within ~20% of fx on the same set. Opus agrees ("port their mechanisms"); Astra/Sol want bounded experimental routing in parallel. Fine as an arm, not as a policy.
- Ship scoped search, fd timeouts and warm subagent workers as code with no referee. Opus's correction that "CLI cold start = null" must not cover harness cold start is right; Sol/Astra say the same.

Where I disagree with peers
- Opus: "use the mean or total for the headline, not the median." Agree for the hours accounting (where the 105 h of sleeps live), disagree for the KEEP statistic: a mean over 60 tasks with 300 s fd tails is dominated by three tasks. Use trimmed mean (10%) for KEEP, p90 as guard, median reported.
- Opus: speculative search ("read-only, side-effect-free, safe"). Measured #5 plus tool p50 0.3 s against 8 s turns means speculating tool calls saves ≤0.3 s/turn; what pays is speculating the next *action* (SPAgent/PASTE), which requires a predictable sequence. Postpone until experiment 4 shows recurrence.
- Opus: "wake-on-event saves 50–85 h." Measured #4 makes the shape right (log watching 65 h, CI 12 h) but the size is overshoot, not sleep wall. Hold the number until measured.
- Astra: "first correct evidence rewards premature stopping." Partly right; the fix is Sol's frozen sufficiency rubric plus answer key, not a different objective.

CONVERGED PROGRAM

(a) Objective and KEEP rule
Tracks with a shared Pareto table (wall, turns, output tokens, pass, p90, host interference); the AutoKernel loop scores only the Locate track for the next two weeks. Locate statistic: paired ratio (candidate/baseline) of 10%-trimmed-mean wall to sufficient evidence under a frozen rubric and written answer key, with turn count co-primary. KEEP when the paired-bootstrap 90% CI upper bound of the wall ratio is below 1, the point estimate is below 1 − max(3%, A/A half-width), turns are not worse, pass rate is non-inferior (no more than one extra failure on 60 dev), and p90 does not worsen by more than 10%. Failures and timeouts count at the timeout value, not excluded. Loop runs on the 60 dev tasks only; the 40 held-out tasks are touched once per candidate family; split by repo/task family and time, not by run. Each KEEP gets a second-model spot check on 10 tasks.

(b) Ordered experiments, next two weeks
1. Referee A/A, 2 × 60 dev tasks, randomized AB/BA with declared cache state. Owner z0evals. Cost: ~1 day, ~$40 tokens. Falsifier: half-width > 15% → referee cannot resolve the program; reduce noise (pin worktrees, warm ACP) before anything else.
2. Instrumentation: per-API-call usage (input, cached, output, TTFT) into the state DB; 24 h cpu.stat/PSI/iostat recorder on groot. Owner Tokenomics (usage), .files (E1 recorder). Cost: 1 day. Falsifier for the quota track: throttled fraction < 1% over 24 h → drop quota experiments.
3. Oracle-path upper bound: 20 dev Locate tasks with the correct path injected. Owner z0evals, z0intelligence for interpretation. Cost: half a day. Falsifier: < 15% wall saving → drop Track B (locator memory), keep only the toolkit card.
4. Independence labelling: SQL over consecutive single-call pairs (next args share no token with previous result) plus the recurrence rate of search→read sequences. Owner Tokenomics. Cost: half a day, no runs. Falsifier: < 25% independent → drop batching and speculation tracks.
5. Wait-until primitive (process exit / file / log-regex / gh run watch, with timeout) and overshoot measurement: replay 20 of the 2,305 log-watch cases, compare job-done time to original wake. Owner hermes-agent; laws registry in shadow (log, never deny). Cost: 2 days. Falsifier: overshoot < 15% of sleep wall.
6. Output budget per turn class: Locate turns at low effort / short reasoning vs default, same model, via vLLM-SR or profile flag. Owner hermes-agent profiles, z0intelligence routing. Cost: 1 day. Falsifier: wall improves < 10% or pass rate drops.
7. Ship as code, no referee: scoped roots and timeouts for fd/grep/search_files, warm worker pool for subagents, skill catalog bytes → retrieval. Owner hermes-agent; capability wiring in z0intelligence harness-adapters. Cost: 2 days. Falsifier: fd p90 and subagent cold TTFT unchanged in next week's profile.
8. Hardware replay matrix (see c). Owner .files and Kerdoios; ctx repo for the DB. Cost: 2 days after item 2. Falsifier: no cell shows ≥ 2× on ctx search or p90 of terminal replay → leave placement alone.

(c) Hardware/stress design
Model-free replay of recorded tool sequences (ctx/session/memory queries on a disposable DB snapshot; 200 terminal commands from the p90 tail) on groot.
Factors: cpu.max 2 / 8 / unbounded for a test slice; DB on NVMe-xfs vs SATA-btrfs vs SATA-btrfs+nodatacow copy; page cache declared cold (vmtouch -e on the copy, no global drop_caches) vs warm.
A/A in every cell first; ≥ 20 pairs, interleaved AB/BA, blocked by time of day.
Covariates logged per trial: PSI cpu/mem/io, nr_throttled, iostat await, GPU util, co-tenant list; trials with co-tenant spikes flagged, not silently dropped.
Stress is a controlled level (stress-ng --vm fixed size), not background noise; owner disruption recorded as an outcome.
Confirmation only: rerun the Locate referee under the winning cell. GPU co-tenancy, mbp placement and phones postponed.

(d) Harness decision
Port, don't migrate: warm workers, catalog→retrieval, drop forced-terminal guidance, dependency-aware batching into Hermes; fx and pi stay as fixed reference arms in every referee run. Route leaf subagents needing no memory/skills/laws to an fx-like lean profile as one experimental arm, promoted only if it passes the KEEP rule with capability parity. Revisit migration only if Hermes remains > 20% behind fx after item 7.

(e) Which tool when
Layer 0, code defaults (hermes-agent tool implementations): scope, timeouts, result caps, wait-until, batching; zero tokens.
Layer 1, toolkit card: 3–5 capabilities retrieved just-in-time per step; text in kvnloo-skills, retrieval/capabilities in z0intelligence harness-adapters, distributed by z0 onboard.
Layer 2, router (z0intelligence): kNN baseline today; SLM only when it beats kNN on end-to-end episode outcome including its own latency.
Layer 3, placement (Kerdoios): host/harness per task class and pressure, fed by the E1 recorder.
Measurement stays with Tokenomics; studies with z0evals.

(f) Remaining disagreements and the settling measurement
- Mean vs trimmed-mean/median for KEEP (Opus vs me): compute all three on the A/A; adopt the one with the smallest half-width.
- Size of the wait win (Opus 50–85 h vs my 0–9% of wall): overshoot from experiment 5.
- Speculative search (Opus for, me/Sol/Astra postpone): recurrence rate from experiment 4; ≥ 40% predictable next-call → revisit.
- Experimental routing to pi/fx now (Astra/Sol) vs after porting (Opus/me): the lean-subagent arm's KEEP result in item 7's follow-up referee.
- Whether prompt size matters at all for GPT seats (Measured #5 is session-level, ecological): the per-call regression from item 2 with uncached tokens as a term.
