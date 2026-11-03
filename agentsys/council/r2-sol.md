Sol — round 2

Retractions, narrowing, defenses

[Measured] The facilitator’s timestamp check establishes that tool-wall excludes assistant generation. Shared sibling stamps and parent/child overlap remain accounting problems.

[Inference] RETRACT any residual ambiguity in my opening about generation entering tool-wall. Also withdraw OOM retries as a priority explanation: 93 SIGKILL/137 results among 204,740 results do not support that ranking; not all are OOMs.

[Inference] NARROW “context overhead” into separate mechanisms: worker startup, discovery turns, uncached input, and generation length. The session-level measurements weaken cached-prompt-size latency as the main target; they do not causally establish output length or reasoning effort as the cause. Per-call instrumentation is necessary.

[Inference] NARROW sleep savings to detection lag, unnecessary polling turns, and blocked availability. The measured 96.1 h of literal sleep establishes waiting, not recoverable time.

[Inference] DEFEND verified episodes and critical paths, separate workload tracks, capability-matched harness comparisons, ordinary parallelism before speculation, disposable storage snapshots, and sealed confirmation data.

[Inference] Agree with Astra: “Waiting for real work is not waste.” Agree with Opus: “Median … hides tails,” but use an untrimmed, deadline-capped mean rather than trimming away stalls. Disagree with Fable’s “ship without a referee”: scope defaults and DB placement can change correctness, freshness, and fallback behavior. They need bounded regression tests, even when they are bug fixes.

(a) Objectives and KEEP rule

[Inference] Optimize deadline-capped mean elapsed time to sufficient verified evidence on Locate and to verified completion on Execute; include one delegated-workload stratum. Treat Resume/Fleet as subsequent tracks, not additional two-week commitments. Publish success, p50/p90, turns, model expenditure, owner interventions, and host interference beside each score. Failures receive the fixed deadline penalty. On development data, require an estimated reduction exceeding max(3%, the prespecified A/A noise threshold); on sealed finalist confirmation, require a task-clustered confidence interval excluding no improvement. KEEP additionally requires correctness noninferiority under a predeclared margin, no safety regression, and compliance with predeclared tail/resource budgets. If the sample cannot establish those conditions, report inconclusive—not KEEP. Thresholds and confirmation access are frozen before adaptive search.

(b) Ordered two-week experiments

All proposals below are [Inference]; costs are planning budgets, not measured forecasts.

1. Days 1–2: minimum attribution and recorder.
   Owner repos: Tokenomics; hermes-agent instrumentation.
   Cost: two engineer-days plus 24 h passive collection.
   Capture actual model/tool start/end, sibling execution, child links, generation/output usage where available, cpu.stat deltas, PSI, faults, and queueing. Preserve missing fields explicitly.
   Falsifier: manually audited timelines cannot reconcile elapsed time or distinguish job completion from observation. Block causal claims until repaired; do not rebuild 45 days first.

2. Days 2–3: freeze the referee and run A/A.
   Owner repo: z0evals.
   Cost: one engineer-day; 20 development tasks × two paired A/A repetitions initially.
   Include difficult/ordinary locates, absent answers, stale indexes, and delegated tasks; pin snapshots and evidence rubrics. Seal confirmation by originating session/task family.
   Falsifier: observed noise makes the intended effect undetectable within budget. Increase repetitions or target larger effects.

3. Days 3–5: discovery upper bound and scoped policy.
   Owner repos: z0intelligence; kvnloo-skills; hermes-agent tool defaults.
   Cost: two engineer-days; three arms on 20 dev tasks.
   Compare baseline, correct-entry-point oracle, and compact card/scoped fallback. Add locator memory only if the oracle demonstrates useful headroom.
   Falsifier: oracle barely improves latency, or the deployable policy loses evidence coverage. Do not equate oracle benefit with achievable memory benefit.

4. Days 5–6: bounded independent-read batching.
   Owner repos: hermes-agent; z0intelligence.
   Cost: one engineer-day; annotate 30 traces, test ten eligible workflows.
   Limit initial concurrency to two; verify semantic independence, not merely absence of copied argument strings.
   Falsifier: insufficient eligible work, unchanged critical path, or contention erases savings.

5. Days 6–8: completion-driven waiting.
   Owner repos: hermes-agent; oss-factory for queued jobs.
   Cost: two engineer-days; instrument ten representative log/file watchers plus five CI/job waits.
   Prioritize the measured 64.9 h log/file-watching category. Compare identical jobs using polling versus reliable completion/condition events.
   Falsifier: detection lag and polling rounds are negligible, or notifications miss completion. Keep sleep laws in shadow; never blanket-deny legitimate waits.

6. Days 8–10: lifecycle and generation-budget ablations.
   Owner repos: hermes-agent; z0evals; Tokenomics.
   Cost: two engineer-days; staged paired tests on 12 tasks.
   Separate resident/cold workers, active catalog, and supported effort/output-budget settings; retain pi/fx reference arms.
   Falsifier: gains disappear with lifecycle/capability parity, or reduced generation harms verification. Session-level output correlations alone justify no production change.

7. Days 10–12: targeted hardware and ctx diagnosis.
   Owner repos: Tokenomics; Kerdoios; .files; ctx implementation repository.
   Cost: two engineer-days; token-free replay first, small end-to-end confirmation second.
   Attribute ctx CPU/IO/locks before placement changes; test CPU quota and snapshot placement sequentially.
   Falsifier: interventions do not improve replay latency or production-relevant tails.

8. Days 13–14: finalist confirmation.
   Owner repo: z0evals.
   Cost: one engineer-day plus the frozen confirmation budget.
   Confirm at most two candidate bundles, include a second-model spot check, and publish nulls and failures.
   Falsifier: KEEP gates fail; revert or retain as an unpromoted experiment.

(c) Hardware/stress design

[Inference] Replay representative read-only queries and eligible tool sequences on consistent disposable snapshots.
[Inference] Compare shared slice quota 2 versus one approved higher allocation; hold workload/concurrency fixed.
[Inference] Compare NVMe/xfs versus SATA/btrfs as placement bundles, with declared warm/first-touch states.
[Inference] Add bounded memory pressure separately; GPU contention only if router spans show material exposure.
[Inference] Randomize paired blocks; run A/A per regime; retain realistic co-tenant runs and failures.
[Inference] Record pressure, throttling, faults, IO and disruption; no live DB moves or global cache flushing.

(d) Harness decision

[Inference] Port demonstrated mechanisms and experimentally route capability-matched leaf tasks; do not migrate wholesale.
[Measured] Warm Hermes ACP already rivals the supplied cold pi/fx TTFT results.
[Inference] Reject Fable’s arbitrary “within ~20%” prerequisite for routing; require outcome, permission, state, and cost parity instead.

(e) Which tool when

[Inference] Tool implementations own deterministic scope, deadlines, caps, freshness and cancellation.
[Inference] kvnloo-skills owns compact skill text; z0intelligence harness-adapters own capabilities, memory and routing.
[Inference] z0 registers components; ./z0 onboard distributes them—no competing hub.
[Inference] Start with rules/kNN and bounded discovery; SLM selection must repay dispatch overhead through verified outcomes.
[Inference] Kerdoios owns host/quota placement; Tokenomics measures; z0evals validates; oss-factory owns queues.

(f) Remaining disagreements and decisive measurements

[Inference] Wait savings magnitude remains unresolved: settle with the distribution of actual completion-to-observation lag on instrumented watchers.

[Inference] “Prompt diet” versus generation budget remains unresolved: settle with a randomized, per-call-instrumented ablation holding model, task and lifecycle fixed.

[Inference] ctx storage versus implementation bottleneck remains unresolved: settle with the same query/snapshot replay on both placements, with CPU/IO/lock spans.

[Inference] Port-only versus selective routing remains unresolved: settle with one capability-matched resident-harness comparison measuring verified completion, full cost, and state/permission parity.
