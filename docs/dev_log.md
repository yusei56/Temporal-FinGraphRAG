# Dev Log

This file tracks the project transformation by phase. Each entry should make the development cycle reproducible: goal, code changes, verification, before/after comparison, metrics, blockers, and next actions.

## 2026-05-22 - Phase 0: Session Isolation + Baseline Checkpoint

### Goal
- Fix the session isolation issue first, so different chat sessions no longer share the same default agent instance.
- Run the current project baseline on the existing minimal evaluation path before larger financial/temporal GraphRAG changes.
- Record the exact verification status, including any environment blockers, so future phases have a reliable comparison point.

### Code Changes
- Updated `server/services/chat_service.py` so service-layer agent lookup passes the active session identifier into `AgentManager.get_agent(...)`.
- Affected paths:
  - `process_chat(...)`: now calls `get_agent(agent_type, session_id=session_id)`.
  - `process_chat_stream(...)`: now calls `get_agent(agent_type, session_id=session_id)`.
  - `process_feedback(...)`: now calls `get_agent(agent_type, session_id=thread_id)` for both the requested agent and fallback `graph_agent`.

### Before
- `AgentManager.get_agent(agent_type, session_id="default")` already supported per-session agent instances internally.
- `chat_service.py` did not pass `session_id` into `get_agent(...)`, so API chat/stream requests always fell back to the `"default"` session key.
- Practical effect: different frontend/API sessions could accidentally reuse one agent instance per agent type, mixing memory, cached retriever state, or tool context.

### After
- Chat and streaming chat requests now use the request's `session_id`.
- Feedback handling now uses `thread_id` as the session key, keeping positive/negative feedback updates aligned with the conversation thread.
- Expected instance key behavior in `AgentManager`: `"{agent_type}:{session_id}"` or `"{agent_type}:{thread_id}"`, instead of always `"{agent_type}:default"`.

### Verification
- Syntax check passed:
  - `python3 -m py_compile server/services/chat_service.py server/services/agent_service.py`
- Call-site check passed:
  - `grep -RIn "get_agent" server/services/chat_service.py`
  - Confirmed 4 service-layer lookups now pass `session_id` or `thread_id`.

### Phase 0 Metrics
| Metric | Before | After | Status |
| --- | ---: | ---: | --- |
| Service-layer isolated agent lookup call sites | 0/4 | 4/4 | Fixed |
| Python syntax check for changed service files | Not run in this phase | Pass | Verified |
| Current minimal baseline JSON | Not generated in this phase | `docs/phase0_agent_min_comparison.json` | Generated |
| Minimal baseline overall pass | Not run in this phase | `true` | Verified |
| Minimal baseline build size | Not run in this phase | 3 docs / 3 chunks | Verified |
| `NaiveRagAgent` minimal hit rate | Historical latest: 3/3 with force-tool retrieval | 3/3 | Verified |
| `GraphAgent` minimal hit rate | Historical latest: 3/3 with force-tool retrieval | 3/3 | Verified |
| `HybridAgent` minimal hit rate | Historical latest: 2/3 with force-tool retrieval | 2/3 | Verified |
| Neo4j Bolt connectivity from WSL | `localhost:7687` connection refused | `172.20.64.1:7687` reachable | Workaround found |
| Docker availability inside WSL | Unknown | `docker` command not found | Environment note |
| Docker availability on Windows host | Unknown | Docker 29.2.0 available | Environment note |

### Baseline Attempt
Attempt 1 command:

```bash
cd /home/bian/projects/graph-rag-agent
source .venv/bin/activate
export PYTHONPATH=/home/bian/projects/graph-rag-agent
export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false
timeout 900 python scripts/agent_min_comparison.py --output-json /tmp/phase0_agent_min_comparison.json
```

Attempt 1 result:
- Baseline did not produce `/tmp/phase0_agent_min_comparison.json`.
- Import-time database initialization failed before evaluation could start.
- Error summary: Neo4j driver could not connect to `localhost:7687`; WSL also reported that the `docker` command is not available in this Ubuntu distro.

Attempt 2 command:

```bash
cd /home/bian/projects/graph-rag-agent
source .venv/bin/activate
export PYTHONPATH=/home/bian/projects/graph-rag-agent
export NEO4J_URI=bolt://172.20.64.1:7687
export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false
timeout 900 python scripts/agent_min_comparison.py --output-json docs/phase0_agent_min_comparison.json
```

Attempt 2 result:
- Baseline completed successfully.
- Output artifact: `docs/phase0_agent_min_comparison.json`.
- Build result: 3 input files, 3 chunks, `overall_pass=true`.
- Agent scores: `NaiveRagAgent` 3/3, `GraphAgent` 3/3, `HybridAgent` 2/3.

### Evaluation Notes
- This run used `NEO4J_URI=bolt://172.20.64.1:7687` because WSL `localhost` did not resolve to the Windows-host Neo4j service.
- The agent answer logs showed global cache hits during question answering. Treat this as a current-state warm-cache baseline, not a cold-cache scientific evaluation.
- For Phase 1 and beyond, add explicit cold-cache/warm-cache controls so reported gains cannot be caused by stale answer cache reuse.
- The script temporarily backs up and restores `files/`, then injects a 3-document minimal demo corpus. It is suitable for smoke testing but not sufficient for financial-domain claims.

### Historical Reference Metrics
These are previous local documentation results, not newly rerun in this Phase 0 attempt:
- `docs/agent_min_comparison.md`: `NaiveRagAgent` 0/3, `GraphAgent` 0/3, `HybridAgent` 1/3.
- `docs/min_agent_improvement_v1_toggle.md` with force-tool-retrieval disabled: `NaiveRagAgent` 1/3, `GraphAgent` 0/3, `HybridAgent` 2/3.
- `docs/min_agent_improvement_v1_toggle.md` with force-tool-retrieval enabled: `NaiveRagAgent` 3/3, `GraphAgent` 3/3, `HybridAgent` 2/3.

### Remaining Environment Caveat
- Baseline is now runnable from WSL if `NEO4J_URI=bolt://172.20.64.1:7687` is exported.
- The default `.env` value still appears to point at `localhost`; from WSL that can fail even though Windows `localhost:7687` is reachable.
- Do not commit secrets from `.env`. Prefer a documented local override or a small run script in a later cleanup step.

### Next Actions
- Add a cold-cache baseline mode or cache reset option before claiming model/retrieval quality improvements.
- Keep `docs/phase0_agent_min_comparison.json` as the current warm-cache smoke-test reference.
- Then start Phase 1: build the finance-domain ingestion/evaluation baseline before introducing temporal graph changes.

## 2026-05-22 - Phase 1 Eval v0: ECT-QA Specific QA Smoke

### Goal
- Add the first reusable financial temporal QA evaluator before changing retrieval/model architecture.
- Start with ECT-QA local/specific questions, because they provide gold answers and `evidence_list` metadata.
- Keep the run cold-cache and reproducible, so future GraphRAG/temporal/PPR changes can be compared against this baseline.

### Code Changes
- Added `scripts/ectqa_eval.py`.
- Added `datasets/ect_qa/` to `.gitignore` because the script caches downloaded Hugging Face ECT-QA source files locally.
- The evaluator supports:
  - Scenarios: `base`, `updated`, `new`.
  - Question filters: `answerable`, `unanswerable`, `all`.
  - Corpus modes: `full` for formal evaluation and `evidence` for fast smoke tests.
  - Agents: `NaiveRagAgent`, `GraphAgent`, `HybridAgent`.
  - Cold-cache execution by running Agents inside a temporary working directory.
  - Tool injection so Agents search an ECT-QA TF-IDF transcript index instead of the project Neo4j graph.

### Metrics Added
| Metric Group | Metrics |
| --- | --- |
| Answer quality | EM, token F1, precision, recall, ROUGE-L, `correct` / `correct_refusal` / `wrong_refusal` / `incorrect` buckets |
| Evidence retrieval | document recall@k, evidence text recall@k, all-support recall@k |
| Temporal retrieval | gold year/quarter coverage@k |
| Citation support | citation support rate from cited `ect_*` chunk ids back to gold evidence files |
| System | per-example latency, selected documents, indexed chunks, Agent errors |

### Smoke Command
This is an integration smoke run, not the final formal benchmark:

```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 1 \
  --corpus-scope evidence \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_smoke.json
```

### Smoke Result
- Output artifact: `docs/ectqa_eval_smoke.json`.
- Dataset slice: ECT-QA `new`, answerable, first selected question.
- Question: `In which year after 2020 did Cincinnati Financial Corporation have the largest net purchases of fixed maturity securities?`
- Gold answer: `2024`.
- Documents loaded: 4 gold-evidence ECT files.
- Chunks indexed: 105.

| Agent | Bucket | EM | F1 | Correct-like | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `NaiveRagAgent` | incorrect | 0.0 | 0.0000 | 0.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `GraphAgent` | correct | 0.0 | 0.0435 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `HybridAgent` | correct | 0.0 | 0.0172 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Overall | 2 correct / 1 incorrect | 0.0 | 0.0202 | 0.6667 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

### Notes
- The smoke used `--corpus-scope evidence`, so retrieval difficulty is intentionally low. This confirms evaluator integration, not real retrieval competitiveness.
- Naive retrieval found the right evidence files but answered `2023`, while Graph/Hybrid answered `2024`. This is useful because the evaluator can already expose temporal comparison failures separately from evidence recall.
- During implementation, Graph/Hybrid initially hit a cache-contamination bug where keyword dictionaries could be returned through the Agent fast-cache path. The evaluator now disables fast-cache shortcuts and overrides keyword extraction with a no-cache lightweight extractor for cold-cache evaluation.
- ECT-QA source data is cached under `datasets/ect_qa/` and ignored by git; reproducible results should be tracked through JSON outputs under `docs/`.

### Next Actions
- Run `--corpus-scope full` on a small sample to measure real retrieval difficulty.
- Add an unanswerable smoke run to validate `correct_refusal` and `wrong_refusal`.
- Then scale to 20-50 questions per scenario and record `base`, `updated`, and `new` tables before architecture changes.

## 2026-05-22 - Phase 1 Eval v1: ECT-QA Full-Corpus Limit 5 Baseline

### Goal
- Move from evidence-only smoke testing to real full-corpus retrieval difficulty.
- Establish an initial answerable and unanswerable baseline before temporal graph, PPR, and refusal improvements.

### Code Changes
- Improved `scripts/ectqa_eval.py` with retry/backoff for Hugging Face file downloads after transient SSL/connection reset failures.
- Added `docs/ectqa_eval_baseline_limit5.md` as the first human-readable baseline table.

### Commands
Answerable:

```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --corpus-scope full \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit5.json \
  --quiet
```

Unanswerable:

```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter unanswerable \
  --limit 5 \
  --corpus-scope full \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_unanswerable_limit5.json \
  --quiet
```

### Dataset Scope
- Scenario: ECT-QA `new`.
- Full corpus: 480 earnings call transcript files.
- Indexed chunks: 17,437.
- Answerable sample: 5 questions x 3 Agents = 15 rows.
- Unanswerable sample: 5 questions x 3 Agents = 15 rows.

### Answerable Metrics
| Agent | Correct-like | EM | F1 | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `NaiveRagAgent` | 0.2000 | 0.0000 | 0.0222 | 0.2233 | 0.0500 | 0.0000 | 0.5000 | 0.3833 |
| `GraphAgent` | 0.2000 | 0.0000 | 0.0046 | 0.2067 | 0.1000 | 0.0000 | 0.5500 | 0.2500 |
| `HybridAgent` | 0.2000 | 0.0000 | 0.0044 | 0.2067 | 0.1000 | 0.0000 | 0.5500 | 0.2667 |
| Overall | 0.2000 | 0.0000 | 0.0104 | 0.2122 | 0.0833 | 0.0000 | 0.5333 | 0.3000 |

### Unanswerable Metrics
| Agent | Correct-like | Bucket Summary | Notes |
| --- | ---: | --- | --- |
| `NaiveRagAgent` | 0.0000 | 5 incorrect | Did not refuse gold-unanswerable questions |
| `GraphAgent` | 0.0000 | 5 incorrect | Did not refuse gold-unanswerable questions |
| `HybridAgent` | 0.0000 | 5 incorrect | Did not refuse gold-unanswerable questions |
| Overall | 0.0000 | 15 incorrect | Refusal behavior is a clear improvement target |

### Interpretation
- Full-corpus retrieval is the first meaningful baseline; answerable correct-like drops to 0.2 overall.
- Retrieval is the main bottleneck: document recall@8 is about 0.21 overall and all-support recall@8 is 0.0.
- Temporal coverage@8 is only about 0.53, so retrieval often finds some relevant period context but not enough for robust multi-time comparison.
- The unanswerable run shows no correct refusal behavior yet. This should be addressed with explicit refusal prompting and evidence sufficiency checks before scaling evaluation.

### Next Actions
- Add refusal/evidence-sufficiency logic and rerun the unanswerable limit-5 baseline.
- Add temporal filtering and rerun the answerable limit-5 baseline to check evidence recall and temporal coverage movement.
- After those first fixes, scale to limit 20/50 and add `base` / `updated` scenario comparisons.

## 2026-05-22 - Phase 1 Eval v2: Metadata Boost and Refusal Guard Ablation

### Goal
- Add the first non-gold temporal/company retrieval improvement.
- Add the first non-gold evidence-sufficiency refusal guard for out-of-scope financial questions.
- Compare both against the full-corpus limit-5 baseline.

### Code Changes
- Updated `scripts/ectqa_eval.py` with:
  - `--metadata-filter off|boost|strict`.
  - query-derived company matching from ECT-QA company names and stock codes.
  - query-derived year/quarter parsing for explicit years, `YYYY-Qn`, `Qn YYYY`, `after`, `before`, `between`, and `from ... to ...` patterns.
  - score boosting for matching company/time metadata.
  - `--refusal-guard` with query-level and retrieval-level refusal reasons.
  - pre-generation refusal for obvious `requested_time_out_of_corpus` and `company_not_in_corpus` cases.
  - expanded refusal classification for Chinese refusals and English `insufficient evidence` answers.
- Updated `docs/ectqa_eval_baseline_limit5.md` with the new ablation tables.

### Commands
Answerable metadata boost:

```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit5_metadata_boost.json \
  --quiet
```

Unanswerable refusal guard:

```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter unanswerable \
  --limit 5 \
  --corpus-scope full \
  --metadata-filter boost \
  --refusal-guard \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_unanswerable_limit5_refusal_guard.json \
  --quiet
```

### Answerable Ablation
| Overall Metric | Baseline Off | Metadata Boost | Delta |
| --- | ---: | ---: | ---: |
| Correct-like | 0.2000 | 0.0667 | -0.1333 |
| F1 | 0.0104 | 0.0115 | +0.0011 |
| Doc Recall@8 | 0.2122 | 0.3733 | +0.1611 |
| Evidence Text Recall@8 | 0.0833 | 0.1133 | +0.0300 |
| All Support@8 | 0.0000 | 0.1333 | +0.1333 |
| Temporal Coverage@8 | 0.5333 | 0.8000 | +0.2667 |
| Citation Support | 0.3000 | 0.7111 | +0.4111 |

### Unanswerable Ablation
| Overall Metric | Baseline Off | Refusal Guard | Delta |
| --- | ---: | ---: | ---: |
| Correct-like | 0.0000 | 1.0000 | +1.0000 |
| Correct Refusals | 0/15 | 15/15 | +15 |
| Incorrect | 15/15 | 0/15 | -15 |
| Errors | 0 | 0 | 0 |

### Interpretation
- Metadata boost improves retrieval substantially but does not yet improve answer correctness. This is a clean sign that generation/comparison reasoning over retrieved financial evidence is now a bottleneck.
- Refusal guard fixes the first five unanswerable examples because they are clear out-of-scope cases: unknown company or requested time beyond corpus coverage.
- The refusal guard is promising but must be tested against answerable questions before treating it as safe; otherwise it may over-refuse.

### Next Actions
- Add a citation-grounded answer synthesis step that prefers numeric comparisons from cited chunks and discourages unsupported extrapolation.
- Run answerable with `--metadata-filter boost --refusal-guard` to estimate over-refusal risk.
- Scale both answerable and unanswerable runs to limit 20 after the synthesis/refusal behavior is less brittle.

## 2026-05-22 - Phase 1 Eval v3: Refusal Guard Answerable Safety Check

### Goal
- Evaluate whether `--refusal-guard` harms answerable questions.
- Keep the baseline logic unchanged and only run a comparable assessment on the same ECT-QA `new`, answerable, full-corpus, limit-5 sample.

### Command
```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --corpus-scope full \
  --metadata-filter boost \
  --refusal-guard \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit5_metadata_boost_refusal_guard.json \
  --quiet
```

### Result
| Overall Metric | Metadata Boost | Metadata Boost + Guard | Delta |
| --- | ---: | ---: | ---: |
| Correct-like | 0.0667 | 0.0667 | 0.0000 |
| F1 | 0.0115 | 0.0247 | +0.0131 |
| Doc Recall@8 | 0.3733 | 0.3400 | -0.0333 |
| Evidence Text Recall@8 | 0.1133 | 0.1744 | +0.0611 |
| All Support@8 | 0.1333 | 0.0667 | -0.0667 |
| Temporal Coverage@8 | 0.8000 | 0.7667 | -0.0333 |
| Citation Support | 0.7111 | 0.7111 | 0.0000 |
| Refusal Guard Reasons | 0/15 | 0/15 | 0 |

### Interpretation
- The guard did not trigger on any of the 15 answerable rows.
- No `wrong_refusal` cases were observed in this small sample.
- The small metric fluctuations are likely due to LLM nondeterminism and should not be treated as a causal improvement.
- This supports keeping the current refusal guard as an evaluation-time safety baseline while we work on evidence-grounded answer synthesis.

## 2026-05-24 - Phase 1 Eval v4: ECT-QA Limit-100 Baseline Suite

### Goal
- Run the first larger local baseline suite on ECT-QA `new` with 100 selected questions per setting.
- Keep the original baseline mostly unchanged, then compare retrieval metadata boosting and refusal guarding.
- Produce stable JSON artifacts for later comparison against the financial temporal RAG refactor.

### Commands
```bash
bash /home/bian/projects/graph-rag-agent/scripts/run_ectqa_limit100_suite.sh
```

The suite ran three settings:
- `answerable --limit 100 --corpus-scope full --metadata-filter off --no-refusal-guard`
- `answerable --limit 100 --corpus-scope full --metadata-filter boost --no-refusal-guard`
- `unanswerable --limit 100 --corpus-scope full --metadata-filter boost --refusal-guard`

### Runtime
- Started: `2026-05-23 21:48:59`
- Completed: `2026-05-24 01:28:37`
- Total runtime: about `3h 40m`

### Artifacts
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100.json`
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json`
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_unanswerable_limit100_refusal_guard.json`
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_limit100_suite.log`

### Answerable Results
| Overall Metric | Baseline Off | Metadata Boost | Delta |
| --- | ---: | ---: | ---: |
| Correct-like | 0.0233 | 0.0400 | +0.0167 |
| F1 | 0.0105 | 0.0207 | +0.0102 |
| Doc Recall@8 | 0.3047 | 0.3840 | +0.0793 |
| Evidence Text Recall@8 | 0.0739 | 0.2523 | +0.1784 |
| All Support@8 | 0.1600 | 0.2000 | +0.0400 |
| Temporal Coverage@8 | 0.4075 | 0.4625 | +0.0549 |
| Citation Support | 0.1093 | 0.4103 | +0.3011 |
| Errors | 23 | 0 | -23 |

### Answerable Agent Breakdown
| Agent | Baseline Correct-like | Boost Correct-like | Baseline DocR@8 | Boost DocR@8 |
| --- | ---: | ---: | ---: | ---: |
| NaiveRagAgent | 0.0100 | 0.0400 | 0.3193 | 0.3453 |
| GraphAgent | 0.0300 | 0.0400 | 0.2745 | 0.4093 |
| HybridAgent | 0.0300 | 0.0400 | 0.3202 | 0.3974 |

### Unanswerable Results
| Overall Metric | Metadata Boost + Refusal Guard |
| --- | ---: |
| Correct-like | 0.8933 |
| Correct Refusals | 268/300 |
| Incorrect | 32/300 |
| Errors | 0 |

### Unanswerable Agent Breakdown
| Agent | Correct-like | Correct Refusals | Incorrect |
| --- | ---: | ---: | ---: |
| NaiveRagAgent | 0.9000 | 90/100 | 10/100 |
| GraphAgent | 0.8900 | 89/100 | 11/100 |
| HybridAgent | 0.8900 | 89/100 | 11/100 |

### Interpretation
- Metadata boosting improves retrieval quality clearly at limit 100: evidence text recall, document recall, temporal coverage, and citation support all increase.
- Answer correctness improves only modestly, from 2.33% to 4.00%, so the main bottleneck is no longer only retrieval; the project needs a stronger evidence-grounded synthesis/reasoning layer.
- The baseline-off answerable run had 23 errors, while metadata boost had 0 errors. This suggests the boosted retrieval path may also make agent behavior more stable.
- Refusal guard is useful but not perfect on unanswerable questions: it correctly refused 268/300 cases but still answered incorrectly on 32/300.

### Next Actions
- Analyze the 32 unanswerable false negatives to identify whether failures come from company parsing, time-range parsing, or weak retrieval scores.
- Inspect the 12 correct answerable rows under metadata boost to learn which evidence patterns the current agents can already handle.
- Add a citation-grounded answer synthesis module before changing the retriever further, because retrieval metrics improved more than final answer correctness.

## 2026-05-24 - Phase 1 Eval v5: Optional LLM-as-a-Judge Layer

### Goal
- Add a paper-style LLM judge layer inspired by Temporal-GraphRAG's `Correct / Refusal / Incorrect` evaluation protocol.
- Keep the existing rule-based baseline unchanged by default.
- Avoid rerunning expensive RAG agents by supporting offline judging of existing ECT-QA result JSON files.

### Changes
- Added `--llm-judge` support to `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Added `/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py` to judge existing eval JSON outputs without rerunning agents.
- Added `/home/bian/projects/graph-rag-agent/scripts/run_ectqa_limit100_llm_judge_suite.sh` for batched limit-100 judge runs.
- LLM judge records:
  - `judge_label`: `correct`, `refusal`, or `incorrect`
  - `evidence_faithfulness`
  - `temporal_alignment`
  - `rationale`, latency, raw response preview, and judge errors

### Smoke Test
```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py \
  --input-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_smoke.json \
  --limit-rows 1 \
  --checkpoint-every 1
```

### Smoke Result
- Judged rows: `1`
- Rule bucket: `incorrect`
- LLM judge label: `incorrect`
- Judge error: `False`
- Output artifact: `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_smoke.json`

### Regression Check
- Ran a default `ectqa_eval.py` smoke without `--llm-judge`.
- Confirmed the original rule-based evaluator still works when judge is disabled.
- Output artifact: `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_llm_judge_default_regression.json`

### Usage Notes
- Full limit-100 LLM judge over three files can require up to `900` judge calls, so it should be run deliberately.
- Use `LLM_JUDGE_LIMIT_ROWS=30` for a cheaper sample run.
- Use `LLM_JUDGE_ONLY_RULE_ERRORS=1` to judge only rows that rule metrics marked as non-correct.

### Interpretation
- The rule-based metrics remain the reproducible baseline.
- LLM judge is now available as a semantic/factual layer for open-ended answers, especially where string matching underestimates correctness.
- The next sensible step is to run a sampled judge pass first, compare rule-vs-judge disagreement, then decide whether full `900`-call judging is worth the API cost.

## 2026-05-24 - Phase 1 Eval v6: Focused LLM Judge Dimensions

### Goal
- Narrow the LLM judge to the three dimensions we currently need most:
  - `judge_label`: `correct`, `refusal`, or `incorrect`
  - `evidence_faithfulness`
  - `temporal_alignment`
- Defer `numerical_reasoning`, `answer_completeness`, and `refusal_quality` until later phases.

### Changes
- Updated `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py` judge prompt and parser to request only the focused schema.
- Kept `label` as a backward-compatible alias for `judge_label`.
- Updated judge aggregation to report only `labels`, `correct_like_rate`, `num_judge_errors`, `evidence_faithfulness`, and `temporal_alignment`.
- Updated `/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py` progress output to use `judge_label`.

### Validation
```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py \
  --input-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_focused_smoke.json \
  --limit-rows 1 \
  --checkpoint-every 1
```

### Focused Smoke Result
- Judged rows: `1`
- Rule bucket: `incorrect`
- LLM judge label: `incorrect`
- Evidence faithfulness: `0.0`
- Temporal alignment: `0.0`
- Judge error: `False`
- Output artifact: `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_focused_smoke.json`

### Interpretation
- The focused judge gives us enough signal for the current stage without overcomplicating the evaluation protocol.
- `judge_label` provides the paper-style semantic correctness/refusal judgement.
- `evidence_faithfulness` and `temporal_alignment` directly target the two most important failure modes for financial temporal RAG.

## 2026-05-24 - Phase 1 Eval v7: Judge Profiles for Cost Control

### Goal
- Address token-cost concerns by making LLM judge dimensions configurable.
- Keep `focused` as the default low-cost profile.
- Add `full` as an optional paper/report profile with richer diagnostic dimensions.

### Changes
- Added `--judge-profile focused|full` to `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Added `--judge-profile focused|full` to `/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py`.
- Added `LLM_JUDGE_PROFILE=full` support to `/home/bian/projects/graph-rag-agent/scripts/run_ectqa_limit100_llm_judge_suite.sh`.
- `focused` profile includes:
  - `judge_label`
  - `evidence_faithfulness`
  - `temporal_alignment`
- `full` profile includes:
  - `judge_label`
  - `answer_correctness`
  - `evidence_faithfulness`
  - `temporal_alignment`
  - `numerical_reasoning`
  - `answer_completeness`
  - `citation_validity`
  - `refusal_quality`

### Validation
```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py \
  --input-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_full_smoke.json \
  --judge-profile full \
  --limit-rows 1 \
  --checkpoint-every 1
```

### Full Smoke Result
- Judged rows: `1`
- Judge profile: `full`
- LLM judge label: `incorrect`
- Judge error: `False`
- All full-profile score fields were returned and aggregated.

### Interpretation
- The marginal token cost of adding dimensions is small compared with the context tokens from question, answer, gold evidence, and retrieved evidence.
- The dominant cost driver is the number of judged rows, not the number of score fields.
- Defaulting to `focused` protects routine runs, while `full` is available when we need richer ablations and paper-style analysis.

## 2026-05-24 - Phase 1 Eval v8: LLM Judge Token Estimate Utility

### Goal
- Estimate LLM judge token cost before launching large judge runs.
- Avoid spending API tokens just to understand cost.

### Changes
- Added `/home/bian/projects/graph-rag-agent/scripts/estimate_ectqa_llm_judge_tokens.py`.
- Refactored `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py` to expose `build_judge_prompt(...)`, so token estimates use the same prompt structure as real judge calls.

### Estimate Command
```bash
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/estimate_ectqa_llm_judge_tokens.py \
  --input-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json \
  --judge-profile full \
  --limit-rows 100 \
  --output-tokens-per-call 250 \
  --model gpt-4o
```

### Result
- Rows estimated: `100`
- Full judge input tokens: `213,418`
- Average input tokens per call: `2,134`
- Median input tokens per call: `2,364`
- P90 input tokens per call: `2,645`
- Min / max input tokens per call: `905 / 2,915`
- Estimated output tokens: `25,000` assuming `250` per call
- Estimated total tokens: `238,418`

### Comparison
- Focused profile, 100 rows: about `215,218` total tokens assuming `180` output tokens per call.
- Full profile, 100 rows: about `238,418` total tokens assuming `250` output tokens per call.
- Full profile, 300 rows: about `720,516` total tokens assuming `250` output tokens per call.

### Interpretation
- Adding more score fields increases token use modestly.
- The main cost driver is still the number of judged rows and the evidence/answer context length.
- Full judge is reasonable for 100 calls, but full judge over all three limit-100 result files would be roughly `2.1M` tokens if judging `900` rows.

## 2026-05-24 - Phase 1 Eval v9: Default Judge Model Set to GPT-4.1 Mini

### Goal
- Use `gpt-4.1-mini` as the default LLM judge model because the current API relay supports it and the token cost is acceptable.
- Keep model selection configurable through `--judge-model` or `LLM_JUDGE_MODEL`.

### Changes
- Added `DEFAULT_LLM_JUDGE_MODEL = "gpt-4.1-mini"` in `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Updated judge model priority to:
  - `--judge-model`
  - `LLM_JUDGE_MODEL`
  - default `gpt-4.1-mini`
- Updated `/home/bian/projects/graph-rag-agent/scripts/estimate_ectqa_llm_judge_tokens.py` to use the same default model for token estimation.
- Updated `/home/bian/projects/graph-rag-agent/scripts/run_ectqa_limit100_llm_judge_suite.sh` to document the default model and support `LLM_JUDGE_MODEL` override.

### Validation
```bash
PYTHONPATH=/home/bian/projects/graph-rag-agent \
LANGCHAIN_TRACING_V2=false \
LANGSMITH_TRACING=false \
/home/bian/projects/graph-rag-agent/.venv/bin/python \
/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py \
  --input-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json \
  --output-json /home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_gpt41mini_smoke.json \
  --judge-profile full \
  --limit-rows 1 \
  --checkpoint-every 1
```

### Result
- Actual judge model recorded in output JSON: `gpt-4.1-mini`
- Judge profile: `full`
- Judge label: `incorrect`
- Judge error: none
- Output artifact: `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge_gpt41mini_smoke.json`

### Interpretation
- The judge path is now aligned with the planned evaluation model.
- Existing business LLM settings such as `OPENAI_LLM_MODEL` no longer override the judge default unless `LLM_JUDGE_MODEL` or `--judge-model` is explicitly set.

## 2026-05-24 - Phase 1 Eval v10: Full LLM Judge No-Overwrite Run Started

### Goal
- Run full-profile LLM judge over the existing limit-100 baseline outputs.
- Avoid overwriting original baseline JSON files or previous judge artifacts.

### Changes
- Added `/home/bian/projects/graph-rag-agent/scripts/start_ectqa_limit100_llm_judge_suite.sh`.
- Updated `/home/bian/projects/graph-rag-agent/scripts/run_ectqa_limit100_llm_judge_suite.sh` to write each run into a timestamped directory under `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/`.
- Updated `/home/bian/projects/graph-rag-agent/scripts/ectqa_llm_judge.py` to fail if `--output-json` already exists unless `--allow-overwrite` is explicitly passed.

### Run
```bash
bash /home/bian/projects/graph-rag-agent/scripts/start_ectqa_limit100_llm_judge_suite.sh
```

### Active Run Directory
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714`

### Configuration
- Judge model: `gpt-4.1-mini`
- Judge profile: `full`
- Input files:
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_full_unanswerable_limit100_refusal_guard.json`

### Initial Progress Check
- First group: `answerable_baseline_off_limit100_llm_judge`
- Judged rows at first check: `30/300`
- Judge errors: `0`
- Intermediate LLM judge labels: `incorrect=27`, `correct=1`, `refusal=2`
- Original baseline JSON files remain untouched.

## 2026-05-24 - Phase 1 Eval v11: Full LLM Judge Limit-100 Completed

### Goal
- Complete full-profile LLM judge for the three limit-100 ECT-QA baseline outputs.
- Preserve all original rule-based evaluation artifacts.

### Run Directory
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714`

### Runtime
- Started: `2026-05-24 16:17:14`
- Completed: `2026-05-24 16:56:04`
- Total runtime: about `39 minutes`

### Artifacts
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714/ectqa_eval_full_answerable_limit100_llm_judged.json`
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judged.json`
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714/ectqa_eval_full_unanswerable_limit100_refusal_guard_llm_judged.json`
- `/home/bian/projects/graph-rag-agent/docs/llm_judge_runs/20260524_161714/ectqa_llm_judge_limit100_suite.log`

### Overall Results
| Setting | Judge Correct-like | Labels | Judge Errors |
| --- | ---: | --- | ---: |
| Answerable baseline off | 0.0133 | incorrect=265, correct=4, refusal=31 | 0 |
| Answerable metadata boost | 0.0533 | incorrect=282, correct=16, refusal=2 | 0 |
| Unanswerable boost + refusal guard | 0.9233 | refusal=277, incorrect=23 | 0 |

### Full Judge Score Averages
| Setting | Answer Correctness | Evidence Faithfulness | Temporal Alignment | Numerical Reasoning | Answer Completeness | Citation Validity | Refusal Quality |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Answerable baseline off | 0.0153 | 0.0327 | 0.1393 | 0.0150 | 0.0297 | 0.0220 | 0.0760 |
| Answerable metadata boost | 0.0973 | 0.1290 | 0.3397 | 0.0880 | 0.0960 | 0.1040 | 0.0060 |
| Unanswerable boost + refusal guard | 0.8967 | 0.8967 | 0.8333 | 0.8900 | 0.8967 | 0.8967 | 0.9233 |

### Answerable Agent Breakdown
| Agent | Baseline Judge Correct-like | Boost Judge Correct-like | Baseline Temporal | Boost Temporal | Baseline Faithfulness | Boost Faithfulness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| NaiveRagAgent | 0.0200 | 0.0700 | 0.1800 | 0.3630 | 0.0450 | 0.1440 |
| GraphAgent | 0.0100 | 0.0600 | 0.1380 | 0.3610 | 0.0330 | 0.1420 |
| HybridAgent | 0.0100 | 0.0300 | 0.1000 | 0.2950 | 0.0200 | 0.1010 |

### Unanswerable Agent Breakdown
| Agent | Judge Correct-like | Labels |
| --- | ---: | --- |
| NaiveRagAgent | 0.9100 | refusal=91, incorrect=9 |
| GraphAgent | 0.9300 | refusal=93, incorrect=7 |
| HybridAgent | 0.9300 | refusal=93, incorrect=7 |

### Interpretation
- LLM judge is stricter than the rule-based answerable metric for baseline off: rule correct-like was `0.0233`, judge correct-like is `0.0133`.
- Metadata boost still improves answerable quality under LLM judge: judge correct-like rises from `0.0133` to `0.0533`.
- Metadata boost produces much stronger evidence and temporal grounding: evidence faithfulness rises from `0.0327` to `0.1290`, and temporal alignment rises from `0.1393` to `0.3397`.
- Refusal guard is even stronger under LLM judge than under rule metrics: rule correct-like was `0.8933`, judge correct-like is `0.9233`.
- The main remaining bottleneck is answer synthesis/reasoning over retrieved evidence, especially numerical comparison and complete evidence use.

## 2026-05-24 - Phase 2 v1: Temporal Evidence Agent Smoke Implementation

### Goal
- Start the first engineering step after baseline: improve answer synthesis without changing the original `NaiveRagAgent`, `GraphAgent`, or `HybridAgent` baselines.
- Add a separately runnable experimental agent that makes financial temporal evidence explicit before generation.

### Literature/Repo Mapping
- HippoRAG-inspired idea: activate relevant memory around the question. In this first version, activation is approximated by company/time-aware TF-IDF retrieval plus metadata boosting over ECT-QA chunks.
- ToG/ToG-2-inspired idea: do not let all candidates compete in one flat retrieval list. The new agent decomposes multi-company and multi-period questions into targeted evidence paths, then prunes to compact evidence cards.
- Temporal-GraphRAG/ECT-QA-inspired idea: represent time constraints explicitly, preserve temporal coverage, evaluate answerability/refusal, and keep evidence/citation traceability.

### Code Changes
- Added `TemporalEvidenceAgent` as an opt-in eval agent in `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Added structured evidence helpers:
  - financial number extraction with unit normalization (`million`, `billion`, `%`, `basis_points`);
  - important query term extraction;
  - evidence excerpt selection;
  - temporal evidence card construction.
- Added targeted retrieval for:
  - multi-company comparison questions, using per-company strict retrieval and round-robin coverage;
  - multi-period questions, using per-year/per-quarter coverage;
  - annual comparison questions, preferring `q4` and `full year` evidence while downweighting partial-period phrases such as `first nine months`.
- Fixed temporal parsing for `after YEAR` and `before YEAR` so the reference year is excluded instead of being boosted as a candidate answer.
- Added prompt guidance requiring canonical quarter output such as `2023-q4` for quarter answers.
- Added CLI knobs:
  - `--temporal-evidence-cards`
  - `--temporal-evidence-chars`

### Validation Commands
```bash
.venv/bin/python -m py_compile scripts/ectqa_eval.py
.venv/bin/python scripts/ectqa_eval.py --scenario new --answer-filter answerable --limit 5 --agents TemporalEvidenceAgent --corpus-scope full --metadata-filter boost --retrieval-top-k 8 --metric-top-k 8 --temporal-evidence-cards 8 --temporal-evidence-chars 700 --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_smoke_v2.json --quiet
.venv/bin/python scripts/ectqa_eval.py --scenario new --answer-filter unanswerable --limit 2 --agents TemporalEvidenceAgent --corpus-scope full --metadata-filter boost --refusal-guard --retrieval-top-k 8 --metric-top-k 8 --temporal-evidence-cards 8 --temporal-evidence-chars 700 --output-json docs/ectqa_eval_temporal_agent_unanswerable_limit2_smoke.json --quiet
```

### Smoke Results
| Run | Examples | Correct-like | Doc Recall@8 | Evidence Text Recall@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent answerable limit=5 | 5 | 1.0000 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 0 |
| TemporalEvidenceAgent unanswerable+guard limit=2 | 2 | 1.0000 | n/a | n/a | n/a | n/a | 0 |

### Important Debug Notes
- Initial multi-company comparison failed because one flat top-k list let JD.com dominate the evidence cards. Per-company targeted retrieval fixed the Skechers/Home Depot/JD.com gross-margin example.
- Initial annual comparison failed because `after 2020` still included 2020 and because q3 partial-year evidence outranked q4 full-year evidence. Excluding the reference year and preferring q4/full-year evidence fixed the Cincinnati Financial example.
- One limit=5 item was semantically correct (`2023 Q4`) but initially failed the rule bucket because the gold answer used `2023-q4`. The prompt now asks for canonical quarter format.

### Artifacts
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_limit5_smoke_v2.json`
- `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_unanswerable_limit2_smoke.json`
- Earlier diagnostic smoke files are kept for traceability:
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_limit2_smoke.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_limit2_smoke_v2.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_limit2_smoke_v3.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_limit2_smoke_v4.json`
  - `/home/bian/projects/graph-rag-agent/docs/ectqa_eval_temporal_agent_answerable_offset4_limit1_smoke.json`

### Current Interpretation
- This is not a final benchmark. It is a smoke implementation proving the new temporal evidence path works end-to-end.
- Compared with the previous answerable baseline bottleneck, this version directly targets the observed weakness: retrieved evidence was often available, but the generator did not compare the right company/time/numeric facts.
- The next fair comparison should run `TemporalEvidenceAgent` on `answerable limit=100` and then judge it with the same `gpt-4.1-mini` full LLM judge pipeline used for the baseline files.

## 2026-05-24 - Phase 2 v2: TemporalEvidenceAgent Limit-100 Evaluation

### Goal
- Run the first fair limit-100 comparison for the new temporal evidence path.
- Keep the original baseline artifacts untouched.
- Use the same full-profile `gpt-4.1-mini` LLM judge style used in Phase 1.

### Run Directory
- `/home/bian/projects/graph-rag-agent/docs/temporal_agent_runs/20260524_213438`

### Commands
```bash
.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 100 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/temporal_agent_runs/20260524_213438/ectqa_eval_temporal_agent_answerable_limit100.json

.venv/bin/python -u scripts/ectqa_llm_judge.py \
  --input-json docs/temporal_agent_runs/20260524_213438/ectqa_eval_temporal_agent_answerable_limit100.json \
  --output-json docs/temporal_agent_runs/20260524_213438/ectqa_eval_temporal_agent_answerable_limit100_llm_judged.json \
  --judge-profile full \
  --judge-model gpt-4.1-mini \
  --checkpoint-every 10
```

### Artifacts
- `/home/bian/projects/graph-rag-agent/docs/temporal_agent_runs/20260524_213438/ectqa_eval_temporal_agent_answerable_limit100.json`
- `/home/bian/projects/graph-rag-agent/docs/temporal_agent_runs/20260524_213438/ectqa_eval_temporal_agent_answerable_limit100_llm_judged.json`
- `/home/bian/projects/graph-rag-agent/docs/temporal_agent_runs/20260524_213438/temporal_agent_limit100_eval.log`
- `/home/bian/projects/graph-rag-agent/docs/temporal_agent_runs/20260524_213438/temporal_agent_limit100_llm_judge.log`

### Runtime
- Raw TemporalEvidenceAgent eval: `2026-05-24T21:37:20` to `2026-05-24T21:42:06`, about `4m46s`.
- LLM judge completed successfully with `100/100` judged rows and `0` judge errors.

### Raw Rule Metrics
| Agent | Examples | Rule Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent | 100 | 0.2300 | correct=23, incorrect=60, wrong_refusal=17 | 0.8680 | 0.4301 | 0.6800 | 0.8608 | 0.9442 |

### Full LLM Judge Metrics
| Agent | Judge Correct-like | Judge Labels | Answer Correctness | Evidence Faithfulness | Temporal Alignment | Numerical Reasoning | Answer Completeness | Citation Validity |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent | 0.2200 | correct=22, incorrect=75, refusal=3 | 0.3530 | 0.5390 | 0.9280 | 0.3500 | 0.4515 | 0.4740 |

### Comparison Against Phase 1 Baselines
| Setting | Rows | Rule Correct-like | Judge Correct-like | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Judge Temporal Alignment | Judge Numerical Reasoning |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline off overall | 300 | 0.0233 | 0.0133 | 0.3047 | 0.0739 | 0.1600 | 0.4075 | 0.1093 | 0.1393 | 0.0150 |
| Metadata boost overall | 300 | 0.0400 | 0.0533 | 0.3840 | 0.2523 | 0.2000 | 0.4625 | 0.4103 | 0.3397 | 0.0880 |
| Best old single agent: Naive + boost | 100 | 0.0400 | 0.0700 | 0.3453 | 0.2145 | 0.1700 | 0.4254 | 0.4332 | 0.3630 | 0.1060 |
| TemporalEvidenceAgent + boost | 100 | 0.2300 | 0.2200 | 0.8680 | 0.4301 | 0.6800 | 0.8608 | 0.9442 | 0.9280 | 0.3500 |

### Interpretation
- TemporalEvidenceAgent improves judge correct-like from the best old single-agent baseline `0.0700` to `0.2200`, about `3.1x`.
- Compared with metadata-boost overall, judge correct-like improves from `0.0533` to `0.2200`, about `4.1x`.
- The largest gains are grounding/temporal metrics:
  - doc recall rises from metadata-boost overall `0.3840` to `0.8680`;
  - all-support recall rises from `0.2000` to `0.6800`;
  - temporal coverage rises from `0.4625` to `0.8608`;
  - citation support rises from `0.4103` to `0.9442`;
  - LLM judge temporal alignment rises from `0.3397` to `0.9280`.
- This validates the Phase 2 hypothesis: explicitly decomposing company/time evidence before synthesis is much more effective than passing flat retrieved transcript chunks to the original agents.

### Failure Slices
| Slice | Count | Judge Correct | Judge Refusal | Judge Correct Rate |
| --- | ---: | ---: | ---: | ---: |
| reasoning_type=enumeration | 65 | 8 | 3 | 0.123 |
| reasoning_type=comparison | 35 | 14 | 0 | 0.400 |
| question_type=single-time query\|multi-companies | 31 | 4 | 0 | 0.129 |
| question_type=single-time query\|multi-keywords | 26 | 6 | 0 | 0.231 |
| question_type=relative-time query | 23 | 8 | 1 | 0.348 |
| question_type=multi-time query | 20 | 4 | 2 | 0.200 |

### Next Engineering Focus
- The retrieval and temporal alignment problem is now much less severe.
- The main remaining bottleneck is complete multi-evidence synthesis, especially enumeration questions and long multi-hop questions.
- Next phase should add a deterministic evidence table / calculation layer before LLM synthesis:
  - extract metric-value-time candidates per company and period;
  - normalize quarter/year answer formats;
  - force enumeration completeness checks against requested companies/times;
  - only then ask the LLM to verbalize the result.

## 2026-05-24 - Remaining Roadmap And Phase 2A/2B Start

### Remaining Roadmap
| Stage | Goal | Status Before This Entry | Acceptance Gate |
| --- | --- | --- | --- |
| Phase 2A | Formal financial temporal fact schema/extractor | Not formalized; only evaluator-local helper functions existed | `limit=5` extraction smoke produces structured cards and facts for every question |
| Phase 2B | Make TemporalEvidenceAgent reuse the formal fact module | TemporalEvidenceAgent duplicated extraction logic inside `scripts/ectqa_eval.py` | `answerable limit=5` runs with no errors and no regression from current smoke |
| Phase 2C | Deterministic evidence table / calculation layer | Not done | `answerable limit=5` improves or preserves correctness, especially comparison/enumeration cases |
| Phase 3A | Time-filtered graph retrieval interface | Not done | `limit=5` retrieval smoke returns time-filtered candidate facts/chunks |
| Phase 3B | PPR expansion and evidence rerank | Not done | `limit=5` retrieval/eval smoke shows stable evidence coverage without answer regressions |
| Phase 4A | ToG-style triple sentence + chunk stitching | Only partial path-decomposition idea exists | `limit=5` smoke confirms stitched evidence is cited and not hallucinated |
| Phase 4B | HopRAG-style pseudo-question recall as optional supplement | Not done | `limit=5` smoke confirms optional recall does not override main evidence |
| Phase 5A | ECT-QA base/new incremental protocol | Partially evaluated only on `new` answerable/unanswerable | Produce comparable base/new metrics |
| Phase 5B | Ablation matrix | Not done | Run at least retrieval-only, temporal cards, evidence table, PPR, ToG-stitch ablations |
| Phase 6 | Product integration and documentation | Not done | Agent path, evidence chain, README/report, and visualization hooks are updated |

### Phase 2A Implementation
- Added formal package `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/`.
- Added `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/temporal_facts.py`.
- New core schema:
  - `FinancialNumber`: raw numeric mention, normalized value, unit, local context.
  - `FinancialTemporalFact`: company, stock code, metric text, normalized value, year, quarter, period type, source chunk, source filename, evidence text, confidence.
  - `TemporalEvidenceCard`: chunk-level card that groups numbers, facts, metadata, excerpt, and retrieval score.
- New extraction/planning utilities:
  - `extract_financial_numbers`
  - `extract_financial_temporal_facts`
  - `build_temporal_evidence_cards`
  - `build_targeted_company_query`
  - `temporal_query_intent`

### Phase 2A Test
```bash
.venv/bin/python -m py_compile graphrag_agent/financial/temporal_facts.py graphrag_agent/financial/__init__.py
```

Limit-5 extraction smoke over ECT-QA answerable questions:
| Question Index | Cards | Facts | Numbers | First Card Company |
| ---: | ---: | ---: | ---: | --- |
| 1 | 8 | 64 | 64 | Cincinnati Financial Corporation |
| 2 | 8 | 40 | 40 | Skechers U.S.A., Inc. |
| 3 | 8 | 40 | 40 | Skechers U.S.A., Inc. |
| 4 | 8 | 43 | 43 | Skechers U.S.A., Inc. |
| 5 | 8 | 44 | 44 | DXC Technology Company |

### Phase 2A Interpretation
- The project now has a reusable financial temporal fact layer rather than evaluator-only ad hoc dictionaries.
- This is the schema bridge needed before Neo4j fact writing, PPR over fact nodes, ToG-style path construction, and product evidence-chain visualization.

### Phase 2B Implementation
- Updated `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py` so `TemporalEvidenceAgent` uses the formal financial module for:
  - targeted company/time query construction;
  - temporal evidence-card construction;
  - query intent classification.
- Added prompt-side `key_facts` compaction before LLM synthesis.

### Phase 2B Debug Note
- First Phase 2B `limit=5` run regressed to `4/5`.
- Root cause: passing all extracted facts into the prompt added noisy low-relevance values such as percentages, yields, and call timestamps.
- Fix: keep full facts in the structured object, but only pass query-relevant `key_facts` into the LLM prompt.
- This matches the ToG-style lesson that path/fact expansion must be followed by pruning before reasoning.

### Phase 2B Test
```bash
.venv/bin/python -m py_compile scripts/ectqa_eval.py graphrag_agent/financial/temporal_facts.py
.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_phase2b_fix.json \
  --quiet
```

### Phase 2B Result
| Run | Examples | Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent Phase 2B limit=5 | 5 | 1.0000 | correct=5 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Next Step
- Proceed to Phase 2C: build a deterministic evidence table/calculation layer over `FinancialTemporalFact`.
- The goal is to reduce the remaining failure mode from Phase 2 v2: enumeration questions and long multi-hop synthesis.

## 2026-05-24 - Phase 2C: Evidence Table And Calculation Guidance

### Goal
- Add a deterministic evidence table before LLM synthesis.
- Prevent the generator from mixing unrelated financial numbers, especially quarter results, year-to-date values, full-year values, and guidance.

### Implementation
- Added `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/evidence_table.py`.
- Added `EvidenceTableRow` with:
  - company/stock code;
  - year/quarter/period;
  - `period_type`;
  - metric text;
  - raw and normalized value;
  - source card/chunk;
  - local evidence text;
  - relevance score.
- Added `build_evidence_table(...)` and prompt integration in `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Added period guidance:
  - quarter questions should prefer quarter/3-month rows;
  - year questions should prefer q4/full-year rows;
  - guidance/year-to-date rows should not be mixed with actual quarter rows unless asked.

### Debug And Fixes
- Initial Phase 2C `limit=5` rule metrics looked correct, but manual inspection found a real error:
  - Question: `In which quarter did dxc technology company achieve the highest non-GAAP EPS between 2023-q2 and 2024-q1?`
  - Wrong answer: `2024-q1` from full-year guidance `$3.15 to $3.40`.
  - Gold: `2023-q4`, actual non-GAAP EPS `$1.02`.
- Root causes:
  - multi-quarter single-company queries did not perform per-quarter targeted recall;
  - evidence-card coverage selected a title/opening chunk for `2023-q3` instead of the chunk containing `$0.95`;
  - `max_numbers=8` clipped the `2023-q4` `$1.02` EPS fact;
  - EPS questions needed unit preference for per-share currency values and demotion of basis points/percent/revenue/guidance.
- Fixes:
  - added per-quarter strict targeted retrieval for `requested_year_quarters`;
  - separated company-name terms from metric terms for fact relevance scoring;
  - increased structured number/fact extraction per card from `8` to `16`;
  - added `period_type=guidance` detection;
  - added metric-unit preferences in evidence table scoring.

### Tests
```bash
.venv/bin/python -m py_compile \
  graphrag_agent/financial/evidence_table.py \
  graphrag_agent/financial/temporal_facts.py \
  scripts/ectqa_eval.py

.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --offset 4 \
  --limit 1 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_offset4_limit1_phase2c_fix.json \
  --quiet

.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_phase2c_fix.json \
  --quiet
```

### Results
| Run | Examples | Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DXC EPS targeted fix | 1 | 1.0000 | correct=1 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |
| TemporalEvidenceAgent Phase 2C limit=5 | 5 | 1.0000 | correct=5 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Verified Example
- Fixed answer: `In 2023-q4, DXC Technology Company achieved the highest non-GAAP EPS of $1.02 between 2023-q2 and 2024-q1.`

### Interpretation
- Phase 2C validates that structured fact extraction alone is not enough.
- We also need deterministic table construction, metric-unit preferences, and period-type guardrails before synthesis.
- This is the first concrete step toward a financial temporal graph where facts can later become graph nodes and PPR/rerank units.

## 2026-05-24 - Phase 3A/3B: Time Filter And PPR Retrieval Primitives

### Goal
- Start the Phase 3 retrieval upgrade without immediately rewriting production `LocalSearch`.
- Build reusable financial temporal retrieval primitives first:
  - explicit company/time filtering;
  - coverage-first row selection;
  - lightweight PPR rerank over temporal fact rows.

### LocalSearch Observation
- Current `/home/bian/projects/graph-rag-agent/graphrag_agent/search/local_search.py` follows the original GraphRAG style:
  - vector-search entity nodes;
  - collect mentioned chunks;
  - collect communities;
  - collect inside/outside relationships;
  - send combined context to LLM.
- It does not yet apply financial time constraints before neighbor expansion.
- Rewriting it directly would be risky because it touches Neo4j and production agent behavior, so Phase 3 starts with a reusable module that can later be wired into `LocalSearch`.

### Implementation
- Added `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/temporal_graph_retrieval.py`.
- Added `TemporalFilteredRetriever`:
  - builds an evidence table from temporal cards;
  - filters rows by `matched_companies`, `requested_years`, and `requested_year_quarters`;
  - selects rows coverage-first so each requested company/time is represented before pure score ranking.
- Added lightweight PPR rerank:
  - rows are graph nodes;
  - edges are weighted by same company, same year, same quarter, same source chunk, same unit, and metric-token overlap;
  - personalized seed is the row relevance score;
  - output rows include `ppr_score` and `combined_score`.

### Phase 3A Test
```bash
.venv/bin/python -m py_compile graphrag_agent/financial/temporal_graph_retrieval.py
```

Limit-5 time-filter smoke:
| Question | Rows | Company Coverage | Year-Quarter Coverage | Year Coverage |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 12 | 1.0000 | n/a | 1.0000 |
| 2 | 12 | 1.0000 | 1.0000 | 1.0000 |
| 3 | 12 | 1.0000 | 1.0000 | 1.0000 |
| 4 | 12 | 1.0000 | 1.0000 | 1.0000 |
| 5 | 12 | 1.0000 | 1.0000 | 1.0000 |

### Phase 3B Test
Limit-5 PPR smoke with `use_ppr=True`:
| Question | Rows | PPR Fields Present | Company Coverage | Year-Quarter Coverage | Year Coverage |
| ---: | ---: | --- | ---: | ---: | ---: |
| 1 | 12 | yes | 1.0000 | n/a | 1.0000 |
| 2 | 12 | yes | 1.0000 | 1.0000 | 1.0000 |
| 3 | 12 | yes | 1.0000 | 1.0000 | 1.0000 |
| 4 | 12 | yes | 1.0000 | 1.0000 | 1.0000 |
| 5 | 12 | yes | 1.0000 | 1.0000 | 1.0000 |

### Interpretation
- Time filtering is now explicit and testable rather than being buried in prompt instructions.
- Coverage-first selection is required even after time filtering; otherwise high-scoring companies can still crowd out lower-scoring requested companies.
- PPR is implemented as a retrieval primitive but is not yet wired into answer generation by default. It should be integrated only after a small ablation confirms it improves answer quality, not just graph connectivity.

### Next Step
- Phase 4A: add ToG-style triple/fact sentence + chunk stitching so the LLM sees compact fact paths plus source excerpts, not only rows or raw chunks.

## 2026-05-24 - Phase 4A: ToG-style Fact Sentence And Chunk Stitching

### Goal
- Add a compact reasoning layer inspired by ToG-2:
  - convert structured financial fact rows into fact/path sentences;
  - stitch each path back to a source chunk excerpt;
  - keep evidence_table as the deterministic calculation workspace.

### Implementation
- Added `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/fact_stitching.py`.
- Added `FactSentence` and `build_fact_sentence_package`.
- Each fact sentence follows this path shape:
  - `company -> period -> metric -> value -> source_chunk`.
- Integrated `fact_sentences` into `build_temporal_synthesis_prompt` in `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Prompt priority is now:
  - use `fact_sentences` as the reasoning skeleton;
  - use `evidence_table` for numeric comparison/calculation;
  - use `evidence_cards` as backup provenance.

### Test
```bash
.venv/bin/python -m py_compile \
  graphrag_agent/financial/fact_stitching.py \
  scripts/ectqa_eval.py

.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_phase4a.json \
  --quiet
```

### Results
| Run | Examples | Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent Phase 4A limit=5 | 5 | 1.0000 | correct=5 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Interpretation
- This stage changes evidence presentation, not the underlying recall pool.
- The value is that the LLM no longer has to infer every relation from raw chunks; it receives a compact path plus the original excerpt for verification.
- This is safer than directly replacing retrieval with PPR because it preserves the Phase 2C behavior while preparing for ToG-style path-based ablations.

### Next Step
- Phase 4B: add HopRAG-style pseudo-question supplementary recall, but keep it optional so it can be ablated against the current TemporalEvidenceAgent.

## 2026-05-24 - Phase 4B: HopRAG-style Pseudo-question Supplementary Recall

### Goal
- Add pseudo-question recall as an optional retrieval supplement.
- Keep it disabled by default so it can be compared cleanly in ablation runs.
- Avoid spending extra LLM tokens for pseudo-question generation in the first version.

### Implementation
- Added `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/pseudo_questions.py`.
- Added deterministic pseudo-question generation from:
  - matched company;
  - requested year or year-quarter;
  - inferred metric terms;
  - comparison intent such as highest/lowest/compare.
- Added CLI flag:
  - `--temporal-pseudo-questions N`
- `TemporalEvidenceAgent` now appends pseudo-question searches only when `N > 0`.
- Default remains `0`, preserving the previous Phase 4A behavior.

### Test
```bash
.venv/bin/python -m py_compile \
  graphrag_agent/financial/pseudo_questions.py \
  scripts/ectqa_eval.py

.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --temporal-pseudo-questions 8 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_phase4b_pseudoq.json \
  --quiet
```

### Results
| Run | Examples | Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent Phase 4B pseudo-q limit=5 | 5 | 1.0000 | correct=5 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Interpretation
- Pseudo questions are a recall supplement, not a reasoning replacement.
- The implementation follows the HopRAG idea of using generated intermediate questions, but keeps the first version deterministic to save tokens and make ablations reproducible.
- Because pseudo-question recall can introduce noisy chunks, it remains an explicit switch rather than a new default.

### Next Step
- Phase 5A: implement the ECT-QA base/new incremental evaluation protocol so we can measure old-corpus vs updated-corpus behavior rather than only single-run accuracy.

## 2026-05-24 - Phase 5A: ECT-QA Incremental Evaluation Protocol

### Goal
- Add a repeatable incremental evaluation wrapper.
- Measure three separate behaviors:
  - base ability: old questions over old corpus;
  - retention/stability: old questions over old+new corpus;
  - new acquisition: new questions over old+new corpus.

### Implementation
- Added `/home/bian/projects/graph-rag-agent/scripts/ectqa_incremental_eval.py`.
- The script runs `scripts/ectqa_eval.py` three times and writes:
  - per-run JSON files;
  - one combined summary JSON;
  - per-agent deltas such as `retention_delta_vs_base`.

### Test
```bash
.venv/bin/python -m py_compile scripts/ectqa_incremental_eval.py

.venv/bin/python scripts/ectqa_incremental_eval.py \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --metadata-filter boost \
  --corpus-scope full \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --temporal-pseudo-questions 0 \
  --output-dir docs/incremental_runs/phase5a_limit5 \
  --summary-json docs/ectqa_incremental_summary_phase5a_limit5.json \
  --quiet
```

### Results
| Protocol Run | Scenario | Documents | Examples | Correct-like | Doc Recall@8 | All Support@8 | Temporal Coverage@8 | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Base old questions / old corpus | base | 384 | 5 | 0.0000 | 0.8667 | 0.8000 | 0.8667 | 0 |
| Retention old questions / old+new corpus | updated | 480 | 5 | 0.0000 | 0.8667 | 0.8000 | 0.8667 | 0 |
| New questions / old+new corpus | new | 480 | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Interpretation
- The protocol works and gives us a much more honest view than a single `new` split run.
- The first `limit=5` sample reveals that the current TemporalEvidenceAgent is strong on the new-question slice we optimized, but weak on the first old-question slice.
- This is useful: future changes must report base/retention/new separately so we do not accidentally improve new acquisition while hiding old-question weakness.

### Next Step
- Phase 5B: implement an ablation matrix runner to compare baseline settings, fact sentences, pseudo-question recall, and later PPR options under the same limit/split protocol.

## 2026-05-24 - Phase 5B: Ablation Matrix Runner

### Goal
- Make method comparisons reproducible instead of manually launching many one-off commands.
- Compare:
  - evidence table only;
  - ToG-style fact sentences;
  - HopRAG-style pseudo-question recall;
  - fact sentences plus pseudo-question recall.

### Implementation
- Added `--temporal-fact-sentences / --no-temporal-fact-sentences` to `/home/bian/projects/graph-rag-agent/scripts/ectqa_eval.py`.
- Added `/home/bian/projects/graph-rag-agent/scripts/ectqa_ablation_matrix.py`.
- Default variants:
  - `table_only`;
  - `fact_sentences`;
  - `pseudoq_table`;
  - `fact_sentences_pseudoq`.
- The script writes per-variant result JSON files and one summary JSON with deltas versus the first variant.

### Test
```bash
.venv/bin/python -m py_compile \
  scripts/ectqa_eval.py \
  scripts/ectqa_ablation_matrix.py

.venv/bin/python scripts/ectqa_ablation_matrix.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --metadata-filter boost \
  --corpus-scope full \
  --output-dir docs/ablation_runs/phase5b_new_limit5 \
  --summary-json docs/ectqa_ablation_summary_phase5b_new_limit5.json \
  --quiet
```

### Results
| Variant | Examples | Correct-like | Doc Recall@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| table_only | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |
| fact_sentences | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |
| pseudoq_table | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |
| fact_sentences_pseudoq | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Interpretation
- The `new limit=5` slice is saturated, so it cannot distinguish the variants.
- The important deliverable is the reusable matrix runner; larger runs or harder slices can now quantify which component helps.
- This also protects us from claiming that a component improved the system just because a single run looked good.

### Next Step
- Phase 6: connect the new financial temporal stack to project-facing documentation and provide stable commands for baseline, incremental, and ablation evaluation.

## 2026-05-24 - Phase 6: Documentation And Stable Engineering Entry Points

### Goal
- Make the financial temporal RAG work understandable and reusable from the project.
- Export new financial modules from the package boundary.
- Provide stable commands for single-run evaluation, incremental protocol, and ablation matrix.

### Implementation
- Updated `/home/bian/projects/graph-rag-agent/graphrag_agent/financial/__init__.py`:
  - exported `FactSentence`;
  - exported `PseudoQuestion`;
  - exported `build_fact_sentence_package`;
  - exported `generate_pseudo_questions`.
- Added `/home/bian/projects/graph-rag-agent/docs/financial_temporal_rag.md`.
- The document records:
  - current architecture;
  - key files;
  - mapping to Temporal-GraphRAG / ToG-2 / HopRAG ideas;
  - common commands;
  - observed results;
  - current limitations and next steps.

### Test
```bash
.venv/bin/python -m py_compile \
  graphrag_agent/financial/__init__.py \
  graphrag_agent/financial/temporal_facts.py \
  graphrag_agent/financial/evidence_table.py \
  graphrag_agent/financial/temporal_graph_retrieval.py \
  graphrag_agent/financial/fact_stitching.py \
  graphrag_agent/financial/pseudo_questions.py \
  scripts/ectqa_eval.py \
  scripts/ectqa_incremental_eval.py \
  scripts/ectqa_ablation_matrix.py

.venv/bin/python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --corpus-scope full \
  --metadata-filter boost \
  --retrieval-top-k 8 \
  --metric-top-k 8 \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --output-json docs/ectqa_eval_temporal_agent_answerable_limit5_phase6.json \
  --quiet
```

### Results
| Run | Examples | Correct-like | Buckets | Doc Recall@8 | Evidence Text Recall@8 | All Support@8 | Temporal Coverage@8 | Citation Support | Errors |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TemporalEvidenceAgent Phase 6 limit=5 | 5 | 1.0000 | correct=5 | 1.0000 | 0.2500 | 1.0000 | 1.0000 | 1.0000 | 0 |

### Interpretation
- This completes the current planned engineering pass from Phase 4A through Phase 6.
- The project now has reusable financial temporal modules, repeatable evaluation scripts, and traceable documentation.
- Production integration should happen after larger `limit=100` incremental + ablation runs, especially because Phase 5A exposed weakness on the old-question slice.

## 2026-05-25 - Formal Limit=100 Evaluation And LLM Judge

### Goal
- Follow up on the Phase 5/6 finding with a formal scale evaluation.
- Diagnose base failures before drawing conclusions.
- Run:
  - `limit=100` incremental protocol;
  - `limit=100` new-split ablation matrix;
  - full `gpt-4.1-mini` LLM judge on all key outputs.

### New Tooling
- Added `/home/bian/projects/graph-rag-agent/scripts/ectqa_case_study.py`.
  - Reads an ECT-QA result JSON.
  - Produces a Markdown failure report.
  - Adds diagnostic labels such as `numeric_value_mismatch`, `evidence_span_gap`, `missing_gold_document`, and `retrieval_ok_generation_bad`.
- Added `/home/bian/projects/graph-rag-agent/scripts/run_limit100_formal_judge_20260525.sh`.
  - Judges 7 formal result files.
  - Uses `gpt-4.1-mini`, `judge_profile=full`.
  - Writes `_llm_judged.json` files without overwriting raw results.
- Added `/home/bian/projects/graph-rag-agent/docs/formal_eval_report_20260525.md`.

### Case Study
```bash
.venv/bin/python scripts/ectqa_case_study.py \
  --input-json docs/incremental_runs/phase5a_limit5/base_old_questions_old_corpus_limit5.json \
  --output-md docs/case_studies/base_limit5_failure_case_study.md \
  --only-failures \
  --max-cases 5

.venv/bin/python scripts/ectqa_case_study.py \
  --input-json docs/incremental_runs/temporal_limit100_20260525/base_old_questions_old_corpus_limit100_llm_judged.json \
  --output-md docs/case_studies/base_limit100_failure_case_study_20260525.md \
  --only-failures \
  --max-cases 20
```

### Incremental Limit=100
```bash
env LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
  .venv/bin/python scripts/ectqa_incremental_eval.py \
  --limit 100 \
  --agents TemporalEvidenceAgent \
  --metadata-filter boost \
  --corpus-scope full \
  --temporal-evidence-cards 8 \
  --temporal-evidence-chars 700 \
  --temporal-pseudo-questions 0 \
  --output-dir docs/incremental_runs/temporal_limit100_20260525 \
  --summary-json docs/ectqa_incremental_summary_temporal_limit100_20260525.json \
  --quiet
```

### Incremental Results
| Run | Rule Correct-like | LLM Judge Correct-like | Judge Labels | Doc R@8 | Evidence Text R@8 | All Support@8 | Temporal Coverage@8 | Faithfulness | Numerical | Citation Validity |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Base old questions / old corpus | 0.0900 | 0.2000 | correct=20, incorrect=80 | 0.8993 | 0.4786 | 0.7700 | 0.9010 | 0.4830 | 0.3140 | 0.3740 |
| Retention old questions / old+new corpus | 0.0800 | 0.1800 | correct=18, incorrect=81, refusal=1 | 0.8993 | 0.4770 | 0.7700 | 0.9010 | 0.5105 | 0.3245 | 0.4020 |
| New questions / old+new corpus | 0.2300 | 0.1700 | correct=17, incorrect=80, refusal=3 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4430 | 0.2900 | 0.3140 |

### Ablation Limit=100
```bash
env LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
  .venv/bin/python scripts/ectqa_ablation_matrix.py \
  --scenario new \
  --answer-filter answerable \
  --limit 100 \
  --agents TemporalEvidenceAgent \
  --metadata-filter boost \
  --corpus-scope full \
  --output-dir docs/ablation_runs/new_limit100_20260525 \
  --summary-json docs/ectqa_ablation_summary_new_limit100_20260525.json \
  --quiet
```

### Ablation Results
| Variant | Fact Sentences | Pseudo Questions | Rule Correct-like | LLM Judge Correct-like | Doc R@8 | Evidence Text R@8 | All Support@8 | Temporal Coverage@8 | Faithfulness | Numerical | Citation Validity |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `table_only` | off | 0 | 0.2400 | 0.1900 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4670 | 0.3180 | 0.3380 |
| `fact_sentences` | on | 0 | 0.2100 | 0.1600 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4545 | 0.3115 | 0.3115 |
| `pseudoq_table` | off | 8 | 0.2400 | 0.1800 | 0.8896 | 0.4170 | 0.7700 | 0.8824 | 0.4720 | 0.3130 | 0.3470 |
| `fact_sentences_pseudoq` | on | 8 | 0.2300 | 0.1500 | 0.8896 | 0.4170 | 0.7700 | 0.8824 | 0.3920 | 0.3060 | 0.2860 |

### LLM Judge
```bash
bash scripts/run_limit100_formal_judge_20260525.sh
```

### Failure Diagnosis
First 20 base failures:
| Diagnostic Label | Count |
| --- | ---: |
| `numeric_value_mismatch` | 11 |
| `retrieval_ok_generation_bad` | 9 |
| `evidence_span_gap` | 7 |
| `missing_gold_document` | 5 |
| `missing_full_support_set` | 5 |
| `temporal_coverage_gap` | 5 |
| `synthesis_or_reasoning_error` | 4 |
| `wrong_refusal_policy` | 2 |

### Interpretation
- LLM judge confirms that rule metrics can undercount some old-question answers, but the overall system is still weak.
- Temporal alignment is high, so the system usually understands the requested time window.
- The real bottleneck is evidence-span localization and metric-specific numeric extraction inside the right transcript.
- `table_only` is currently the best new-split variant.
- `fact_sentences` and `pseudo_questions` should remain experimental switches, not defaults.

### Decision
- Next engineering phase should not add more prompt/retrieval surface area yet.
- Prioritize deterministic metric extraction and period-value table construction for common financial metrics:
  - free cash flow;
  - GAAP/non-GAAP gross margin;
  - cash, cash equivalents, and investments;
  - quarterly sales/revenue;
  - EPS;
  - net purchases / fixed maturity securities.

## 2026-05-26 - WP1: Deterministic Named-Metric Extractors

### Goal
- Route A (rebuild into real time-aware GraphRAG) was chosen; WP1 is the foundation.
- Attack the two limit=100 bottlenecks together: numeric extraction (`numerical_reasoning` 0.31)
  and evidence-span localization (`evidence_text_recall@8` 0.43).
- The generic extractor `extract_financial_temporal_facts` grabbed *every* number in a chunk and
  guessed `metric_text` by query-term overlap — it never knew which number was FCF vs revenue vs EPS.

### New Module
- Added `graphrag_agent/financial/metric_extractors.py`.
  - Named, typed extraction for 6 metrics: free cash flow, GAAP/non-GAAP gross margin,
    cash+equivalents+investments, revenue/sales, EPS, fixed-maturity purchases.
  - `MetricSpec` (patterns + unit_class) → `MetricFact` (typed, carries `metric_key`/`qualifier`).
  - Binds each named mention to the nearest unit-compatible number (after-mention preferred),
    detects GAAP/non-GAAP/adjusted qualifier from a pre-mention window.
  - `detect_query_metrics()` reuses the same patterns on the query.
  - Deterministic, dependency-free — reusable for Neo4j ingest (WP2) and search rerank (WP3).
- Added `test/test_metric_extractors.py` (13 tests, all pass via `PYTHONPATH=. .venv/bin/python`).

### Scoring Change
- Rewrote `fact_relevance_score` in `evidence_table.py`: replaced the hardcoded
  EPS→currency/margin→percent/revenue→currency unit bumps (the limit=5 overfit smell) with
  general metric-key matching (`metric_match_score`): +5 if fact text is about the asked metric,
  −3 if about a different named metric, ±4 unit-class consistency vs the query's expected class.
  Generalizes automatically as metrics are added.

### Regression (limit=50, new/answerable, table_only, gpt-4.1-mini judge_profile=full)
- BASE = `docs/regression_cleanup_20260525/` (pre-WP1). WP1 = `docs/regression_wp1_20260526/`.

| Metric | BASE | WP1 | Δ |
| --- | ---: | ---: | ---: |
| doc_recall@8 | 0.9297 | 0.9297 | +0.0000 |
| evidence_text_recall@8 | 0.3935 | 0.3935 | +0.0000 |
| temporal_coverage@8 | 0.9247 | 0.9247 | +0.0000 |
| numerical_reasoning | 0.2560 | 0.2940 | +0.0380 |
| answer_correctness | 0.2870 | 0.3180 | +0.0310 |
| evidence_faithfulness | 0.3950 | 0.4240 | +0.0290 |
| citation_validity | 0.2230 | 0.2720 | +0.0490 |
| answer_completeness | 0.3630 | 0.3528 | -0.0102 |
| judge correct-like | 0.14 | 0.16 | +0.02 (+1 Q) |

### Interpretation
- **Structural, expected:** chunk-retrieval metrics (doc/text recall, temporal coverage) are
  *identical* — WP1 only reorders the evidence-table facts fed to the LLM, it does not change
  TF-IDF retrieval. Evidence-span recall is a WP3 (PPR retrieval) lever, not a WP1 one.
- **Directionally consistent but within noise:** every evidence-table-dependent judge dimension
  moved up together (numerical +0.038, faithfulness +0.029, correctness +0.031, citation +0.049),
  which is the exact pattern WP1 predicts. But at n=50 with ~60% answer-text variance from LLM
  nondeterminism (32/50 answers differed), a single run pair cannot prove causation. Treat as
  encouraging, not conclusive.
- **Decision:** keep WP1 (cleaner code, removed overfit weights, no regression). The real numeric
  win requires feeding `MetricFact`s as the *source* of evidence rows (WP2 graph ingest) rather
  than only rescoring generic facts. Proceed to WP2 only on user go-ahead.

## 2026-05-26 - WP2: Temporal Facts Into Neo4j

### Goal
- End the "experimental path bypasses GraphRAG" drift: put the WP1 `MetricFact`s into a real
  time-hierarchy graph in Neo4j so retrieval (WP3) can do time-filtered PPR over it.

### Loader Extraction
- Moved the ECT-QA loader (`EctDocument`, `EctChunk`, `EctQaDataManager`, `chunk_document`, HF
  constants) out of `scripts/ectqa_eval.py` into `graphrag_agent/financial/ectqa_corpus.py` so
  the eval and the graph builder share one copy (no duplication). `EctQaCorpus` (TF-IDF, eval-only)
  stays in the script. Verified `ectqa_eval` still imports and resolves the shared symbols.

### Graph Builder
- Added `graphrag_agent/integrations/build/build_financial_graph.py`.
- Schema (namespaced `Fin*` so it never collides with the original `:Chunk`/`:__Entity__` graph):
  - `(:FinCompany)-[:REPORTED]->(:FinFact)-[:IN_PERIOD]->(:FinQuarter)-[:OF_YEAR]->(:FinYear)`
  - `(:FinFact)-[:FROM_CHUNK]->(:FinChunk)-[:OF_COMPANY]->(:FinCompany)`, `(:FinChunk)-[:IN_PERIOD]->(:FinQuarter)`
  - Constraints on every id + indexes on `FinFact.metric_key`, `(period_year, period_quarter)`.
  - Idempotent MERGE; `--wipe` deletes Fin* nodes; batched UNWIND writes.

### Full Ingest (scenario=new, corpus-scope=full, chunk 1800/250)
- 480 docs -> 17,437 chunks -> 5,455 facts; 24 companies, 5 years (2020-2024), 20 quarters.
- Facts per metric: revenue 2961, free_cash_flow 817, eps 815, gross_margin 704,
  cash_and_investments 156, fixed_maturity_purchases 2.
- Integrity: 0 facts missing REPORTED / IN_PERIOD / FROM_CHUNK; 0 quarters missing OF_YEAR;
  0 null values. Full chain verified for all 5,455 facts.

### Findings
- **Works:** temporal + qualifier disambiguation is now a graph query — e.g. Crocs gross margin
  sliced by every quarter 2020-Q1..2024-Q4 and by GAAP vs non-GAAP.
- **Precision gap (quantified, feeds WP3):** each (quarter, qualifier) cell often holds multiple
  conflicting facts. Two systematic noise sources: (1) `basis points` *deltas* ("improved 100 bps")
  are bound next to margin *levels*; (2) occasional misbinds (e.g. a `164.8%` under GAAP gross
  margin). The graph structure is correct; per-cell value selection is the WP3 lever (PPR / best-
  supported-fact) plus a level-vs-delta filter in the extractor.

### Next (WP3, on user go-ahead)
- Rewrite `graphrag_agent/search/local_search.py` to query the Fin* graph: company+metric seed ->
  time-subgraph filter -> PPR diffusion over Fact/Chunk -> evidence rerank. Add a graph-backed
  retrieval mode to the eval harness so WP1-3 are finally measured through the real GraphRAG path
  instead of the stubbed Neo4j + TF-IDF path.

## 2026-05-26 - WP3: Time-Scoped + PPR Retrieval Over The Fin* Graph

### Approach
- New module `graphrag_agent/financial/temporal_graph_search.py` (the entity-centric
  `LocalSearch` was left untouched). Pipeline: time+company filter (Cypher) -> PPR over the
  fact<->chunk graph (networkx, seeded on metric/qualifier-matching FinFact nodes) -> evidence
  rerank. `FinancialGraphRetriever` wraps `EctQaCorpus` (reuses `analyze_query` + the lexical
  signal) and exposes the same `.search()` shape, so it is a drop-in.
- Eval harness: added `--retriever {tfidf,graph}` to `scripts/ectqa_eval.py`. The graph path
  builds a real Neo4j driver from env (the agent-import Neo4j stub is bypassed). This finally
  routes retrieval through the real graph instead of stubbed-Neo4j + TF-IDF.

### Pure-graph first attempt -> recall regression (kept as a finding)
- Candidates starting from `FinFact` made only fact-bearing chunks reachable. For Crocs 2024-Q2,
  35 chunks are in scope but only 7 have an extracted metric; the other 28 (qualitative/guidance
  text, often gold) were invisible. Measured: doc_recall@8 0.93 -> 0.79, temporal_coverage 0.92 ->
  0.80. Conclusion: PPR must *rerank* a time-scoped candidate set, not *be* it.

### Fix: chunk-first hybrid (time-scoped TF-IDF + PPR rerank)
- Candidates = every in-scope chunk; score = in-scope TF-IDF + 0.5 * PPR. Time-scoping also
  removes cross-company / cross-period distractors that hurt global TF-IDF.

### Results (limit=50, new/answerable, table_only; fresh tfidf control vs graph, same harness)
- Retrieval: doc_recall@8 0.9297 -> 0.9037 (-0.026); **evidence_text_recall@8 0.3935 -> 0.4217
  (+0.028)**; temporal_coverage 0.9247 -> 0.9087; citation_support 0.9550 -> 0.9200.
- LLM judge (gpt-4.1-mini, full):

| Metric | TF-IDF | Graph | Δ |
| --- | ---: | ---: | ---: |
| judge correct-like | 0.16 | 0.26 | +0.10 (8->13 correct) |
| numerical_reasoning | 0.2600 | 0.4300 | +0.1700 |
| answer_correctness | 0.2980 | 0.4520 | +0.1540 |
| answer_completeness | 0.3850 | 0.5096 | +0.1246 |
| evidence_faithfulness | 0.4400 | 0.5540 | +0.1140 |
| citation_validity | 0.2480 | 0.2980 | +0.0500 |
| temporal_alignment | 0.9480 | 0.9400 | -0.0080 |

- Artifacts under `docs/regression_wp3_20260526/`.

### Interpretation
- Unlike WP1 (deltas within noise), WP3 shows a **coherent multi-dimensional lift** (+0.11..+0.17
  across every quality dimension simultaneously, +5 correct labels), corroborated by the retrieval
  signal (evidence_text_recall up). This is well past the single-run noise floor and is the first
  result measured through the real graph path. Mechanism: time-scoped + metric-seeded retrieval
  puts the correct supporting number in context, lifting numerical reasoning and faithfulness.
- **Caveat:** still a single limit=50 pair; LLM nondeterminism affects both retrieval and judging.
  Confirm at limit=100 before treating as final. doc_recall dipped slightly (-0.026) where
  `analyze_query` mis-scoped company/period — a candidate for a scope-relaxation tweak.

### Next (on user go-ahead)
- limit=100 confirmation run; then WP4 (ToG/HopRAG as off-by-default switches) / WP5 incremental +
  ablation through the graph path / WP6 reconnect multi_agent + frontend to the Fin* retriever.

## 2026-05-26 - WP3 Fixes + limit=100 Confirmation

### Two flagged issues — attempted, measured, only one kept
- **Fix (b) level-vs-delta:** added deterministic `value_kind` (level/delta) to `MetricFact`
  (`classify_value_kind`: basis points -> delta; bare `up`/`down`/`by` immediately before the
  number -> delta; else level), re-ingested (`f.value_kind`), and made PPR seeding prefer level
  facts. A first, broad classifier (any change-verb -> delta) mis-flagged levels
  ("improved gross margin **of** 62.5%") and *cost* 0.06 evidence_text_recall, so it was tightened.
  Same-25-question isolation: `value_kind` is **retrieval-neutral** (identical metrics to original
  WP3) — level facts already dominated top-8. Kept as correct, cheap metadata for a future
  answer-synthesis layer; it is not a retrieval win.
- **Fix (a) global TF-IDF safety net:** unioning scoped candidates with a global lexical top-N to
  recover the doc_recall dip. Measured net-negative: global lexical chunks displaced the
  PPR-surfaced evidence chunks, costing ~0.086 evidence_text_recall (same-25 isolation) for only
  +0.004 doc_recall. **Reverted.** The -0.026..-0.039 doc_recall dip is the better trade; scoping
  precision is the win.

### limit=100 confirmation (new/answerable, table_only; fresh tfidf control vs graph)
| Metric | TF-IDF | Graph | Δ |
| --- | ---: | ---: | ---: |
| judge correct-like | 0.19 | 0.22 | +0.03 (19->22) |
| answer_correctness | 0.329 | 0.421 | +0.092 |
| numerical_reasoning | 0.307 | 0.390 | +0.083 |
| answer_completeness | 0.400 | 0.482 | +0.082 |
| evidence_faithfulness | 0.479 | 0.539 | +0.060 |
| citation_validity | 0.343 | 0.384 | +0.041 |
| temporal_alignment | 0.928 | 0.921 | -0.007 |
| doc_recall@8 | 0.896 | 0.857 | -0.039 |
| evidence_text_recall@8 | 0.431 | 0.434 | +0.003 |

- Artifacts under `docs/regression_wp3_limit100_20260526/`.

### Honest verdict
- The limit=50 headline (+0.10 correct-like) was **partly 50-question noise**: at n=100 the binary
  correct-like gain is only **+0.03** (~3 questions, near the label-flip noise floor).
- But the **continuous judge scores hold a coherent +0.06..+0.09 lift** across correctness,
  numerical reasoning, completeness, and faithfulness — these average more stably over 100 and are
  a real signal. So graph retrieval produces **better-grounded, more numerically-correct answers**,
  it just does not reliably flip many to "fully correct" (the task is hard; correct-like ~0.20).
- Retrieval recall is slightly lower (doc_recall -0.039) yet answer quality is higher: the graph
  puts the *right* metric/period evidence at the top of context even when it recalls fewer gold
  docs overall. This is the core WP3 mechanism and it survives at n=100.
- Net config kept: time-scoped TF-IDF + PPR rerank, `value_kind` tagged (neutral), no safety net.

## 2026-05-27 - P1 number-binding + value_kind gate (eval-rigor track)

### Bootstrap CIs on the limit=100 data (free; settles signal vs noise)
- Paired 95% CIs (graph - tfidf, n=100): **answer_correctness +0.092 [+0.014,+0.168]**,
  **numerical_reasoning +0.083 [+0.011,+0.158]**, **answer_completeness +0.083 [+0.007,+0.161]**
  are CI-significant; faithfulness (+0.060) / citation (+0.041) / correct_like (+0.030) cross 0;
  **doc_recall -0.039 [-0.080,-0.008]** is a real cost; evidence_text_recall +0.003 (flat).
- Reframe: retrieval/extraction metrics are deterministic (no LLM) -> tunable without judge noise;
  the win lives *downstream of retrieval* (span-recall flat, recall down, yet correctness up), so
  the lever is the evidence-table / time-reasoning stage, not chunk recall. Bootstrap CIs are now
  the report standard; binary correct-like is advisory.

### P1: per-metric value sanity bounds (problem 4 - number binding)
- Added `is_plausible_value(metric_key, unit, value, value_kind)` to `metric_extractors.py`, bounds
  on the *normalized* value, per metric (not one global threshold):
  gross_margin level%∈[-50,100]; eps $/share∈[-200,200]; revenue/cash∈[0,5e12];
  fcf/fixed_maturity∈[-5e12,5e12]. Implausible binds dropped at extraction.
- Effect: re-ingest 5455->5452 facts; removed exactly the impossible margins (`164.8%`, `172.1%`
  gross-margin "levels"); EPS had 0 magnitude misbinds. No over-pruning. +5 unit tests (23 total).

### value_kind accuracy gate for P2 (problem 1c - deterministic compass)
- Built a reproducible span-gold subset (`test/gold_value_kind.jsonl`): 44 real ingested spans,
  stratified (gross_margin-heavy: 22 incl. 8 bps deltas; 7 eps incl. 1 delta; 8 revenue; 4 fcf;
  3 cash), hand-adjudicated level/delta (35 level / 9 delta).
- **classify_value_kind accuracy = 44/44 = 100% (gate >=90% to start P2: PASS).** Per-metric all
  perfect. Mechanism: bps->delta unambiguous; "was/were/at X%"->level; "by"->delta.
- Honest caveat: sample is gross_margin-weighted and may under-represent the classifier's known
  residual weak case (percent-unit deltas phrased "expanded N percentage points" without an
  up/down/by cue, which would mis-read as level). bps -- the dominant margin-delta expression --
  is handled. Expand the gold set if P2 results look off.

### Verdict
- P1 done. value_kind gate PASS -> **P2 (wire value_kind into the evidence table + cross-period
  comparison cards) is greenlit** as the next, highest-ROI step (the bootstrap shows the gain
  lives at this downstream layer).

## 2026-05-27 - P2: graph fact pack — NEGATIVE result at n=25, not shipped

### What was built
- `graphrag_agent/financial/temporal_graph_facts.build_graph_fact_pack`: from the typed/bounded/
  value_kind-tagged FinFacts, a level-preferred `fact_table` (bps/delta dropped for point-in-time
  Qs) + a level-only `cross_period_comparison`; fires only when company + in-domain metric detected.
- Wired into the synthesis prompt as an authoritative numeric source; `--graph-fact-pack` flag on
  `scripts/ectqa_eval.py`. Two variants tested: (v1) all graph facts in scope; (v2) restricted to
  the shown evidence-card chunks.

### A/B at limit=25 (graph vs graph+pack, same 25 questions; judge gpt-4.1-mini)
| metric | nopack | pack v1 | pack v2 (restricted) |
| --- | ---: | ---: | ---: |
| correct_like | 0.360 (9) | 0.320 (8) | 0.320 (8) |
| answer_correctness | 0.492 | 0.516 | 0.500 |
| numerical_reasoning | 0.456 | 0.480 | 0.468 |
| answer_completeness | 0.546 | 0.556 | 0.548 |
| evidence_faithfulness | 0.636 | 0.604 | 0.608 |
| citation_validity | 0.312 | 0.224 | 0.248 |

### Verdict (honest negative)
- Net **neutral-to-negative**: tiny upticks in correctness/numerical (+0.01..+0.02) are offset by
  drops in faithfulness (-0.03) and citation_validity (-0.06); correct_like -1 question. Consistent
  across both variants.
- Free diagnostic: v2 cites 0 out-of-retrieved-set chunks, so the citation drop is **not** unseen
  chunks — the judge finds pack-influenced answers less cleanly supported, because the
  multiple-candidate-values-per-cell noise (e.g. several gross-margin %s per period: enterprise vs
  segment vs guidance) leads the LLM to assert numbers it can't pin to one cited chunk.
- Root cause is the unresolved **per-cell sub-metric ambiguity** (enterprise vs segment vs brand
  margin), not value_kind (P1 gate already showed value_kind is 100% on span-gold). Dumping a
  structured-but-still-ambiguous table does not beat letting the LLM read the evidence cards.
- **Decision:** do NOT ship the pack (kept off by default, as an ablation artifact like ToG/HopRAG);
  do NOT spend a limit=100 on it. The cross-period card is barely exercised by the mostly-
  point-query sample, so its value for trend questions is untested — pursuing it needs a targeted
  multi-period eval subset first.

### Next candidate (not started)
- The real blocker for downstream gains is **per-cell sub-metric disambiguation** (which "gross
  margin" — enterprise / segment / brand / guidance). That is a metric-extractor refinement
  (sub-type tagging), measurable deterministically on the span-gold set, and would help retrieval,
  the fact pack, and answer quality together. Higher-leverage than iterating the pack.

## 2026-05-27 - Sub-metric disambiguation preconditions (no-go) + WP4 wrap

### Three deterministic preconditions (free, no LLM)
- **Precondition 1: ambiguity rate.** Cell = (company, period, metric, qualifier); ambiguous if
  >1 distinct level raw_value. **87.3% of level facts (4523/5183) live in ambiguous cells**
  (revenue 97.7%, FCF 85.1%, EPS 78.8%, gross_margin 69.6%, cash 9.7%). Far above the 9% no-go
  threshold.
- **Precondition 2: on the evaluation evidence path.** span-gold level facts: **80%** in
  ambiguous cells. Of the 100 eval questions, **34 are in-domain** (one of the 6 metrics +
  matched company); of those, **30/34** hit an ambiguous cell. So ambiguity is real and on-path,
  but any extraction-layer fix is bounded by the 34% in-domain ceiling.
- **Precondition 3: deterministic acceptance possible.** Yes — multi-value-cell rate reduction
  and sub-type tagging accuracy on an extended span-gold set are deterministic, like P1's gate.

### Counter-finding: "sub-metric" is NOT the dominant ambiguity axis
- Marker-based decomposition of the 4523 ambiguous facts (cues can overlap):
  comparative ("vs", "year-over-year", "down from") **38.2%**;
  guidance ("expect", "outlook") **21.0%**;
  **submetric** ("segment", "brand", "flash", "by region") **only 17.5%**;
  no marker 37.2%.
- The biggest ambiguity slice is **temporal-comparative leakage** (a prior-year value mentioned
  in the same chunk gets tagged to the current period), not sub-metric. So "sub-metric
  disambiguation" addresses ~17.5% of the problem; a comparative-leakage filter (similar in
  spirit to value_kind's bps/delta detection) would address ~38%.

### Decision: do NOT chase disambiguation; transition to WP4/5/6 wrap-up
- WP3 already produces CI-significant answer-quality gains *despite* 87% ambiguity (the LLM
  reads excerpts and picks reasonably). P2 then showed that *organising* those cells into a
  structured table hurt — purity does not auto-convert to score.
- Combined with the 34% in-domain ceiling and the heterogeneous ambiguity makeup, the marginal
  *score* return from further extraction surgery is bounded and uncertain. Engineering value
  (cleaner graph) is real but the project is at the "package and present" stage; further
  inner-loop work has diminishing returns vs WP4/5/6 packaging.

### WP4: harmful defaults flipped off, negative results consolidated
- `scripts/ectqa_eval.py`: `--temporal-fact-sentences` default flipped **True -> False** (ToG-style
  stitching, ablation-proven harmful). `--temporal-pseudo-questions` was already 0.
  `--graph-fact-pack` is already off. Ablation matrix passes the fact-sentences flag explicitly
  per variant, so it is unaffected.
- **Negative results table (kept as ablation artifacts, all off by default):**

| Experiment | Where | Limit | Result | Outcome |
| --- | --- | ---: | --- | --- |
| ToG-style `fact_sentences` | `financial/fact_stitching.py` | 100 | faithfulness 0.467->0.392, citation 0.338->0.312 | off by default |
| HopRAG-style `pseudo_questions` | `financial/pseudo_questions.py` | 100 | citation drop, no quality lift | off by default |
| Global TF-IDF safety net (WP3) | `temporal_graph_search.py` (reverted) | 50 | evidence_text_recall -0.086 for +0.004 doc_recall | code removed |
| Graph fact pack (P2) | `financial/temporal_graph_facts.py` | 25 | citation -0.06, faithfulness -0.03, net correct_like -1 | off by default |

- Lesson across all four: in this setting, adding structure/surface area *to the prompt* tends to
  hurt; the right marginal work was P1's deterministic fixes + WP3's retrieval reordering.

## 2026-05-29 - Packaging cleanup before product integration

### Fixes
- Restored `.env.example` so the README quick-start path is executable again.
- Cleaned the public README/results/architecture docs into UTF-8-safe ASCII text and clarified
  that the financial temporal-RAG track is currently an evaluation/research path, not wired into
  FastAPI/frontend by default.
- Added `networkx==3.4.2` to `requirements.txt`; WP3 imports it directly for PPR reranking.
- Extended `scripts/ectqa_ablation_matrix.py` and `scripts/ectqa_incremental_eval.py` with
  `--retriever {tfidf,graph}` and `--graph-fact-pack/--no-graph-fact-pack`, so graph retrieval can
  be evaluated through the same matrix/protocol wrappers instead of only through `ectqa_eval.py`.
- Exported the newer financial public API from `graphrag_agent.financial`: ECT-QA corpus types,
  metric facts/extractors, graph retriever/PPR helpers, and graph fact-pack builder.

### Baseline impact
- Intended behavior impact: none for default baseline runs. Wrapper defaults remain
  `--retriever tfidf` and `--no-graph-fact-pack`.
- Graph/PPR experiments are now reproducible from the wrapper level, which makes future tables
  traceable without changing the underlying retrieval algorithm.

## 2026-05-29 - Original baseline full LLM Judge backfill

### Why
- The original `graph-rag-agent` baseline had full rule metrics for `NaiveRagAgent`,
  `GraphAgent`, and `HybridAgent`, but did not have full LLM Judge coverage. This made
  "original baseline vs improved system" LLM-judge comparisons incomplete.

### Run
- Input: `docs/ectqa_eval_full_answerable_limit100.json`
- Output: `docs/original_baseline_llm_judge_20260529/original_graph_rag_agent_limit100_full_judged.json`
- Judge model/profile: `gpt-4.1-mini`, `full`
- Coverage: `300` judged rows = `100` questions x `3` original agents.
- Runtime: `1210.8s`; local JSON completed with `num_judge_errors=0`.

### Headline comparison
- Best original baseline judge correct_like: `0.010`; improved TemporalEvidenceAgent graph run:
  `0.220` (`+0.210`).
- Best original baseline answer_correctness: `0.016`; improved: `0.421` (`+0.405`).
- Best original baseline temporal_alignment: `0.178`; improved: `0.921` (`+0.743`).
- Best original baseline numerical_reasoning: `0.014`; improved: `0.390` (`+0.376`).

## 2026-05-29 - Slim repository cleanup

### Why
- `FinGraph-Research-Agent` was renamed to `TempoRAG-Fin` and converted from a copied full
  `graph-rag-agent` workspace into a slim financial temporal GraphRAG repository.
- The original baseline code and tests remain available in the sibling `graph-rag-agent`
  directory, so this repository no longer needs to carry generic agents, frontend/server code,
  school-demo files, local virtual environments, or old HotpotQA/multi-agent docs.

### Removed
- Local environments and caches: `.venv/`, `.venv312/`, `.py310/`, `cache/`, `__pycache__/`,
  `*.pyc`.
- Original product/demo surfaces: `frontend/`, `server/`, `assets/`, `files/`, `training/`.
- Generic GraphRAG packages: `agents/`, `search/`, `graph/`, `community/`, `pipelines/`,
  `evaluation/`, `cache_manager/`, `models/`.
- Original diagnostic scripts/tests/docs, including minimal-demo, retrieval-trigger,
  multi-agent, HotpotQA, and old README-style files.

### Kept
- Financial core: `graphrag_agent/financial/`.
- Fin* graph ingest: `graphrag_agent/integrations/build/build_financial_graph.py`.
- Evaluation: `scripts/ectqa_eval.py`, `ectqa_llm_judge.py`, `ectqa_ablation_matrix.py`,
  `ectqa_incremental_eval.py`, token estimation and limit-100 runners.
- Evidence and traceability docs/results under `docs/`, including the original-baseline LLM
  judge backfill and WP3 graph-vs-TF-IDF results.

### Verification
- Project size reduced from about `2.7G` to `414M`.
- `import graphrag_agent` works and now exposes the financial temporal API.
- `scripts/ectqa_eval.py` default agent is now `TemporalEvidenceAgent`.
- Core files compile successfully.
- `test/test_metric_extractors.py`: `23` tests passed.

## 2026-05-29 - GitHub portfolio packaging pass

### Why
- The slim repository still carried a local `.env`, broad upstream-style `.env.example`, and many
  process-level evaluation JSON files that were useful during development but noisy for a public
  portfolio repository.
- The README also needed clearer attribution and a contribution map so the upstream MIT base and
  the financial temporal-RAG contribution are easy to distinguish.

### Changed
- Rewrote `README.md` with upstream attribution, headline metrics, contribution map, quick start,
  artifact policy, and license notes.
- Added `NOTICE` to record upstream provenance and the scope of the derived financial track.
- Tightened `.gitignore` for local environments, secrets, downloaded ECT-QA data, generated
  evaluation outputs, and old demo corpus files.
- Reduced `.env.example` to the runtime variables used by the slim financial evaluation path and
  aligned the default Neo4j password with `docker-compose.yaml`.
- Removed local `.env`.
- Removed local downloaded `datasets/ect_qa/` cache; the dataset is ignored and can be
  re-downloaded/prepared for evaluation runs.
- Removed large process-level evaluation outputs while keeping compact reports, summary JSON files,
  the original-baseline summary, and the WP3 `limit=100` graph-vs-TF-IDF artifacts.

### Size impact
- Repository working tree size: about `414M` -> `108M`.
- `docs/` size: about `289M` -> `18M`.
- `.git/` remains about `90M` because the copied repository still preserves upstream history.

### Verification target
- The curated docs now retain the reproducible headline artifacts under
  `docs/regression_wp3_limit100_20260526/`.
- Publishing risk is lower because real `.env` credentials and generated raw JSON outputs are no
  longer present in the working tree.
- `python3 -m compileall -q graphrag_agent scripts test` passed.
- `python3 test/test_metric_extractors.py` requires an installed project environment; the current
  system Python does not have `networkx`, which is declared in `requirements.txt`.
- README/NOTICE/config docs passed UTF-8 validation with `iconv`.
- A no-content secret-pattern scan over the working tree now reports
  `secret_like_token_found=no`.
