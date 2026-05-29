# Formal ECT-QA Evaluation Report - 2026-05-25

This report summarizes the first formal `limit=100` evaluation pass after the financial temporal RAG refactor.

## Artifacts

| Artifact | Path |
| --- | --- |
| Base failure case study | `docs/case_studies/base_limit100_failure_case_study_20260525.md` |
| Incremental summary | `docs/ectqa_incremental_summary_temporal_limit100_20260525.json` |
| Ablation summary | `docs/ectqa_ablation_summary_new_limit100_20260525.json` |
| Judge runner | `scripts/run_limit100_formal_judge_20260525.sh` |

## Incremental Protocol

Settings:

- Agent: `TemporalEvidenceAgent`
- `limit=100`
- `answer_filter=answerable`
- `corpus_scope=full`
- `metadata_filter=boost`
- `temporal_pseudo_questions=0`
- `temporal_fact_sentences=true`
- LLM judge: `gpt-4.1-mini`, `judge_profile=full`

| Run | Corpus | Rule Correct-like | LLM Judge Correct-like | Judge Labels | Doc R@8 | Evidence Text R@8 | All Support@8 | Temporal Coverage@8 | Faithfulness | Numerical | Citation Validity |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Base old questions | old | 0.09 | 0.20 | correct=20, incorrect=80 | 0.8993 | 0.4786 | 0.7700 | 0.9010 | 0.4830 | 0.3140 | 0.3740 |
| Retention old questions | old+new | 0.08 | 0.18 | correct=18, incorrect=81, refusal=1 | 0.8993 | 0.4770 | 0.7700 | 0.9010 | 0.5105 | 0.3245 | 0.4020 |
| New questions | old+new | 0.23 | 0.17 | correct=17, incorrect=80, refusal=3 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4430 | 0.2900 | 0.3140 |

### Incremental Interpretation

- Rule metrics undercount some semantically acceptable answers, especially on old questions; LLM judge raises base from `0.09` to `0.20`.
- Retention is close to base: judge correct-like drops from `0.20` to `0.18`, so adding new corpus does not cause a large stability collapse.
- New acquisition remains weak under LLM judge: rule says `0.23`, but judge says `0.17`.
- Retrieval is not the only bottleneck. Doc recall is near `0.90`, but evidence text recall is only `0.43-0.48`.
- Temporal alignment is high (`0.943-0.977`), so the main failures are not usually year/quarter parsing; they are evidence-span localization, numeric extraction, and final synthesis.

## Ablation Matrix On New Split

Settings:

- Scenario: `new`
- Agent: `TemporalEvidenceAgent`
- `limit=100`
- `answer_filter=answerable`
- LLM judge: `gpt-4.1-mini`, `judge_profile=full`

| Variant | Fact Sentences | Pseudo Questions | Rule Correct-like | LLM Judge Correct-like | Judge Labels | Doc R@8 | Evidence Text R@8 | All Support@8 | Temporal Coverage@8 | Faithfulness | Numerical | Citation Validity |
| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `table_only` | off | 0 | 0.24 | 0.19 | correct=19, incorrect=80, refusal=1 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4670 | 0.3180 | 0.3380 |
| `fact_sentences` | on | 0 | 0.21 | 0.16 | correct=16, incorrect=82, refusal=2 | 0.8962 | 0.4308 | 0.7900 | 0.8891 | 0.4545 | 0.3115 | 0.3115 |
| `pseudoq_table` | off | 8 | 0.24 | 0.18 | correct=18, incorrect=79, refusal=3 | 0.8896 | 0.4170 | 0.7700 | 0.8824 | 0.4720 | 0.3130 | 0.3470 |
| `fact_sentences_pseudoq` | on | 8 | 0.23 | 0.15 | correct=15, incorrect=82, refusal=3 | 0.8896 | 0.4170 | 0.7700 | 0.8824 | 0.3920 | 0.3060 | 0.2860 |

### Ablation Interpretation

- The best current variant is `table_only`, not the more complex fact-sentence or pseudo-question variants.
- `fact_sentences` did not improve formal accuracy. It likely adds prompt surface area without solving the deeper evidence localization problem.
- `pseudoq_table` slightly reduces retrieval coverage in this run, suggesting pseudo-question recall introduces noise.
- The combined variant is worst under LLM judge, especially on faithfulness and citation validity.
- Current default for future formal runs should be `table_only` unless a targeted fix proves otherwise.

## Base Failure Case Study

The first 20 base failures show this diagnostic distribution:

| Label | Count |
| --- | ---: |
| `numeric_value_mismatch` | 11 |
| `retrieval_ok_generation_bad` | 9 |
| `evidence_span_gap` | 7 |
| `missing_gold_document` | 5 |
| `missing_full_support_set` | 5 |
| `temporal_coverage_gap` | 5 |
| `synthesis_or_reasoning_error` | 4 |
| `wrong_refusal_policy` | 2 |

Key reading:

- Many failures happen even when the right documents are retrieved.
- The model frequently picks nearby but wrong values from the same call transcript.
- Multi-quarter enumeration remains difficult because each period needs an exact value, not just a relevant document.
- Some rule failures are false negatives, but LLM judge still confirms the overall system is far from solved.

## Engineering Decision

Do not keep adding retrieval-generation tricks blindly. The next high-value change should be narrower:

1. Improve evidence-span localization inside retrieved chunks.
2. Build metric-specific extractors for common ECT-QA metrics such as free cash flow, gross margin, cash/equivalents/investments, sales, EPS, revenue, and purchases.
3. Add deterministic period-value tables before the LLM sees the prompt.
4. Re-run `table_only` against the new extractor and compare it to this report.

The current evidence says we should temporarily treat `fact_sentences` and `pseudo_questions` as experimental options, not default improvements.
