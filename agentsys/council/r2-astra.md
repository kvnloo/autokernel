Astra — round 2

Inference — Convergence: optimize verified episode completion, remove avoidable decisions and synchronization, and tune hardware only where interventions demonstrate benefit. No wholesale migration or new router-training program.

Own round-1 revisions

  RETRACT — Inference: withdraw any implication that memory-pressure kills deserve a leading retry-reduction experiment. Measured: only 93 SIGKILL/137 results among 204,740 results; these are not all established OOM kills.

  NARROW — Inference: my harness/context track must separate startup, cached input, uncached input, and output/effort. Measured: session-level associations implicate output length and Astra’s uncached input more strongly than cached context. These ecological associations do not establish per-call causality.

  NARROW — Inference: the sleep uncertainty is now specifically completion-detection lag and blocked availability, not whether command wall was actual sleeping. Measured: literal sleeps explain 96.1 of 105.3 hours; log/file watching dominates. Those hours are not a recoverable-savings estimate.

  DEFEND — Inference: episode critical paths, capability-matched harness comparisons, bounded concurrency, placement-bundle terminology, sealed confirmation, and no blanket sleep denial remain correct. Measured: timestamps exclude generation, resolving that ambiguity; sibling and parent/child overlap remain.

Cross-examination

Inference — Agree with Sol’s “agent remained available sooner”: readiness lag and availability need separate endpoints. Reject Opus’s 50–85-hour savings projection without measured overshoot.

Inference — Agree with Fable’s “remove the model” for hardware isolation, but reject “ship without a referee” for DB placement or search bounds. Faster searches that truncate necessary evidence fail the objective.

Inference — Agree with Opus’s concern that “the median hides the tails”; disagree with using a trimmed mean as the remedy, because trimming can remove the stalls we are targeting.

Inference — Reject “almost certainly IO/CoW-bound” for ctx. No supplied measurement attributes its latency to storage.

(a) Objectives and KEEP

Inference — Approve Locate and Execute/Verify as separate scored tracks, with Resume and fleet throughput diagnostic only this fortnight. Primary score: mean capped time to verified sufficiency/completion, charging every failure the predeclared deadline; report uncapped failures, success, p50/p90, turns, measured tokens/cost, owner interventions, and host interference separately. Freeze snapshots, rubrics, task-family splits, and deployment weights, including ordinary and absent-answer cases alongside pathological searches. Calibrate sample size and minimum useful improvement with A/A before treatment; 3% is a floor, not promised resolution. KEEP only when the finalist’s paired, task-family-bootstrap 95% upper confidence bound on its latency ratio is below 1−δ, with prespecified correctness noninferiority and tail/resource guardrails satisfied. Safety/provenance failures veto promotion; insufficient power means inconclusive. Adapt on development only; use sealed confirmation once per selected family, plus a second-model spot check.

(b) Ordered two-week experiments

Inference — Costs below are engineering-day caps, not measured forecasts. Every model experiment has a preapproved call/spend cap.

1. Days 1–2: instrumentation and referee A/A.
   Owner: Tokenomics; hermes-agent emits spans; z0evals grades.
   Cost: 2 days, 24-hour recorder, 20 paired development episodes initially.
   Capture actual tool starts/ends, child links, per-call usage where available, TTFT, completion, cache state, quota/PSI.
   Falsifier: unresolved critical-path attribution or A/A too noisy for the planned effect; block promotion until resolved.

2. Days 2–3: Locate oracle and separate policy ablations.
   Owner: z0evals, z0intelligence, kvnloo-skills.
   Cost: 1.5 days, 20 development tasks.
   Compare baseline, oracle entry point, compact card, and existing locator separately; include stale/absent answers.
   Falsifier: oracle yields no material benefit, or practical arms lose evidence sufficiency.

3. Days 3–4: scope and independent-call batching.
   Owner: hermes-agent tools; z0intelligence orchestration.
   Cost: 1.5 days; manually label 30 episodes, replay eligible cases.
   Compare scope defaults separately from bounded two-call concurrency; judge semantic dependencies, not token overlap.
   Falsifier: evidence misses, few eligible dependencies, or contention erases critical-path gains.

4. Days 4–5: completion-driven watching.
   Owner: hermes-agent; oss-factory for queued jobs; laws registry in shadow.
   Cost: 1.5 days, 20 instrumented log/file watchers plus representative CI waits.
   Record event-ready, wake, agent-resume, missed-event and cancellation timestamps.
   Falsifier: negligible detection lag/availability benefit or unreliable notification. Preserve legitimate backoff.

5. Days 6–7: harness mechanisms and output budget.
   Owner: hermes-agent, z0evals; Tokenomics attribution.
   Cost: 2 days; extend harness-speed-v1 rather than restart it.
   Stage matched warm/cold lifecycle, catalog retrieval, then effort/output-budget ablations; retain pi/fx reference arms and delegated tasks.
   Falsifier: gains vanish under capability parity, discovery overhead cancels savings, or verification quality declines.

6. Days 7–8: ctx attribution and hardware replay.
   Owner: Tokenomics, ctx implementation repo, Kerdoios, .files; z0evals.
   Cost: 1.5 days, primarily model-free.
   Profile actual ctx paths first; replay representative queries and tool bursts using the design below.
   Falsifier: no latency sensitivity to the suspected resource; stop that tuning branch rather than relocate production state.

7. Days 9–10: router shadow and finalist confirmation.
   Owner: z0intelligence; z0evals; z0 registry/distribution.
   Cost: 2 days, capped confirmation budget.
   Compare deterministic selection against existing kNN including dispatch cost; confirm only development winners and publish Pareto results.
   Falsifier: downstream benefit fails KEEP or disappears after routing overhead. No SLM training this fortnight.

(c) Hardware/stress design

Inference — Replay immutable, authorized tool workloads first; validate winners with full episodes afterward.
Inference — Test shared agent.slice quota 2 versus an approved higher limit, at fixed aggregate concurrency; the short throttling window is not a capacity diagnosis.
Inference — Test disposable NVMe/xfs versus SATA/btrfs snapshots as placement bundles, warm and controlled first-touch; no global cache flush.
Inference — Separate quiet and bounded memory/CPU co-tenant lanes; add GPU contention only if router spans justify it.
Inference — Randomize AB/BA across days, run A/A per regime, log PSI/throttling/faults/IO and owner disruption; retain failures and realistic contention.
Inference — Compare eligible groot/mbp placement including transfer/queue costs; preserve fleet-build ownership and stop stress at predefined disruption limits.

(d) Harness decision

Inference — Port demonstrated mechanisms and experimentally route bounded, capability-matched leaves now; do not migrate.
Inference — Reject Fable’s arbitrary “within ~20% of fx” prerequisite: routing needs an outcome/parity gate, not a brand-relative threshold.
Inference — Charge adapters, permissions, memory/provenance, startup and retries to each arm; continue H0×Z×R×S separately.

(e) Which tool when

Inference — Harness defaults enforce scope, deadlines, cancellation, freshness and safe concurrency.
Inference — kvnloo-skills owns compact skill text; z0intelligence harness-adapters own capabilities, retrieval, memory and routing.
Inference — Keep frequent tools resident; retrieve uncommon contracts; explicit path → canonical read, repo discovery → indexed lookup with bounded fallback, notes → document retrieval.
Inference — Kerdoios owns placement/quota; z0 registers components and distributes through ./z0 onboard—no second registry.
Inference — Begin with deterministic rules; kNN earns adoption through verified outcomes including overhead, not historical-choice accuracy.

(f) Remaining disagreements and decisive measurements

Inference — Waits as the largest lever: settle with event-ready-to-agent-resume lag on representative watchers.
Inference — Prompt diet versus output/effort: settle with randomized per-call-instrumented ablation at fixed model and task.
Inference — ctx storage attribution: settle with identical-query placement intervention plus CPU/IO/lock spans.
Inference — Route now versus port first: settle with capability-matched warm episode completion time and correctness, including adapter cost.
