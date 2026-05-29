# Original Graph-RAG-Agent Baseline LLM Judge - 2026-05-29

## Setup

- Input: `docs/ectqa_eval_full_answerable_limit100.json`
- Output: `docs/original_baseline_llm_judge_20260529/original_graph_rag_agent_limit100_full_judged.json`
- Dataset slice: ECT-QA `new`, `answer_filter=answerable`, `limit=100`
- Original agents judged: `NaiveRagAgent`, `GraphAgent`, `HybridAgent`
- Judge model: `gpt-4.1-mini`
- Judge profile: `full`
- Total judge rows: `300`
- Runtime: `1210.8s`

## Original Baseline Judge Results

| Agent | Judge correct_like | answer_correctness | evidence_faithfulness | temporal_alignment | numerical_reasoning | answer_completeness | citation_validity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NaiveRagAgent | 0.010 | 0.013 | 0.045 | 0.178 | 0.013 | 0.024 | 0.024 |
| GraphAgent | 0.010 | 0.016 | 0.026 | 0.123 | 0.014 | 0.019 | 0.013 |
| HybridAgent | 0.010 | 0.010 | 0.020 | 0.090 | 0.010 | 0.020 | 0.020 |

## Best Original Baseline vs Improved Temporal Graph RAG

Improved system artifact:
`docs/regression_wp3_limit100_20260526/table_only_limit100_graph_llm_judged.json`

| Metric | Best original baseline | Improved TemporalEvidenceAgent | Delta |
| --- | ---: | ---: | ---: |
| Judge correct_like | 0.010 | 0.220 | +0.210 |
| answer_correctness | 0.016 | 0.421 | +0.405 |
| evidence_faithfulness | 0.045 | 0.539 | +0.494 |
| temporal_alignment | 0.178 | 0.921 | +0.743 |
| numerical_reasoning | 0.014 | 0.390 | +0.376 |
| answer_completeness | 0.024 | 0.482 | +0.458 |
| citation_validity | 0.024 | 0.384 | +0.360 |

## Notes

- This fills the earlier evaluation gap: the original graph-rag-agent baseline now has full LLM Judge coverage for all three original agents.
- The comparison above uses the best original baseline value per metric, not a cherry-picked single weak agent.
- `GraphAgent` had `23` execution errors in the original baseline run; those errors are part of the original-system evaluation result.
- LangSmith upload returned 403 during the run, but local JSON output completed successfully with `num_judge_errors=0`.
