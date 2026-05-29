# TempoRAG-Fin Results

This document summarizes the main evaluation artifacts for the financial temporal RAG track.

Evaluation harness:

- Agent/eval: `scripts/ectqa_eval.py`
- Offline judge: `scripts/ectqa_llm_judge.py`
- Judge model: `gpt-4.1-mini`
- Judge profile: `full`
- Standard report style: paired bootstrap 95% confidence intervals over continuous judge scores.

## Table 1: Baseline, Graph Retriever vs TF-IDF

Scenario: `new`, `answer_filter=answerable`, `limit=100`, `table_only`.

Artifacts: `docs/regression_wp3_limit100_20260526/`

| Metric | TF-IDF | Graph | Delta | 95% CI |
| --- | ---: | ---: | ---: | --- |
| answer_correctness | 0.329 | 0.421 | +0.092 | [+0.014, +0.168] |
| numerical_reasoning | 0.307 | 0.390 | +0.083 | [+0.011, +0.158] |
| answer_completeness | 0.400 | 0.482 | +0.083 | [+0.007, +0.161] |
| evidence_faithfulness | 0.479 | 0.539 | +0.060 | [-0.024, +0.142] |
| citation_validity | 0.343 | 0.384 | +0.041 | [-0.040, +0.122] |
| judge correct_like | 0.190 | 0.220 | +0.030 | [-0.030, +0.100] |
| doc_recall@8 | 0.896 | 0.857 | -0.039 | [-0.080, -0.008] |
| evidence_text_recall@8 | 0.431 | 0.434 | +0.003 | [-0.050, +0.054] |

Reading:

- The graph retriever improves the main continuous answer-quality dimensions: correctness, numerical reasoning, and completeness.
- It pays a real doc-recall cost.
- Evidence-text recall is almost flat, which suggests the graph path improves evidence ordering rather than broad document recall.
- Binary `correct_like` is reported but should not be over-interpreted at `n=100`.

## Table 2: Prompt/Retrieval Add-on Ablation

Scenario: `new`, `answer_filter=answerable`, `limit=100`, TF-IDF retriever.

Artifacts: raw process JSON files are archived outside git; the summarized
values below are retained for traceability.

| Variant | judge_correct_like | numerical | faithfulness | citation | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| table_only | 0.190 | 0.318 | 0.467 | 0.338 | best default |
| fact_sentences | 0.160 | 0.312 | 0.455 | 0.312 | hurts |
| pseudoq_table | 0.180 | 0.313 | 0.472 | 0.347 | neutral/slightly noisy |
| fact_sentences_pseudoq | 0.150 | 0.306 | 0.392 | 0.286 | hurts |

Additional negative experiments:

| Experiment | Result | Current status |
| --- | --- | --- |
| Global TF-IDF safety net | evidence_text_recall -0.086 for only +0.004 doc_recall | reverted |
| Graph fact pack | lower citation/faithfulness in small A/B runs | off by default |

Reading:

- More prompt surface area did not help.
- The winning changes are deterministic metric extraction and graph-based evidence reranking.
- Fact sentences, pseudo questions, and graph fact packs remain optional experiments.

## Table 3: Incremental Protocol

`limit=100` for each protocol slice, TF-IDF retriever.

Artifacts: raw process JSON files are archived outside git; the summarized
values below are retained for traceability.

| Scenario | Rule correct_like | Judge correct_like | doc_recall@8 | evidence_text_recall@8 |
| --- | ---: | ---: | ---: | ---: |
| base: old questions over old corpus | 0.090 | 0.200 | 0.899 | 0.479 |
| retention: old questions over old+new corpus | 0.080 | 0.180 | 0.899 | 0.477 |
| new: new questions over old+new corpus | 0.230 | 0.170 | 0.896 | 0.431 |

Reading:

- Retention is stable when new documents are added.
- New questions are harder under LLM judge even though rule correct_like is higher.
- This protocol remains useful for future regressions and can now be run with `--retriever graph`.

## Caveats

- The graph-vs-TF-IDF result is the headline result.
- Tables 2 and 3 were produced before the graph retriever became the recommended retrieval backend.
- LLM answer generation has run-to-run variance even at low temperature via the OpenAI-compatible proxy.
- Reproduce the headline graph-vs-TF-IDF values from
  `docs/regression_wp3_limit100_20260526/` before making a final paper/report claim.
