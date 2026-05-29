# Ablation And Negative Results

This document records the experiments that were tried but not promoted to the
default TempoRAG-Fin path. The goal is not to make the project look worse; it is
to make the engineering trail reproducible. In this task, more retrieval surface
area often looked attractive in theory but hurt answer grounding, citation
quality, or period-specific evidence ordering in practice.

## Evidence Sources

| Source | What it supports |
| --- | --- |
| `docs/RESULTS.md` | Headline graph-vs-TF-IDF results, ablation summary, incremental protocol. |
| `docs/formal_eval_report_20260525.md` | Limit-100 ablation and failure analysis before the graph retriever became the default backend. |
| `docs/ectqa_ablation_summary_new_limit100_20260525.json` | Rule-metric outputs for table-only, fact-sentence, pseudo-question, and combined variants. |
| `docs/regression_wp3_limit100_20260526/` | Final graph-vs-TF-IDF judge artifacts and paired bootstrap confidence intervals. |
| `docs/case_studies/base_limit100_failure_case_study_20260525.md` | Manual failure buckets for the first 20 base failures. |

## Summary

| Experiment | Initial hypothesis | Observed result | Decision |
| --- | --- | --- | --- |
| ToG-style fact sentences | Turning extracted facts into compact sentences would make evidence easier for the LLM to use. | LLM judge correct-like fell from `0.190` to `0.160`; faithfulness fell from `0.467` to `0.455`; citation validity fell from `0.338` to `0.312`. | Kept as optional code, disabled by default. |
| HopRAG-style pseudo questions | Pseudo-question expansion would improve recall for varied financial wording. | Correct-like was nearly flat (`0.190` to `0.180`), while doc recall fell from `0.896` to `0.890` and evidence-text recall fell from `0.431` to `0.417`. | Kept as optional code, disabled by default. |
| Fact sentences + pseudo questions | Combining both enrichments would provide complementary evidence. | It was the weakest formal variant: correct-like `0.150`, faithfulness `0.392`, citation validity `0.286`. | Disabled; not used in headline results. |
| Global TF-IDF safety net | Adding broader TF-IDF fallback evidence would recover missed documents. | Evidence-text recall changed by `-0.086` for only `+0.004` doc recall in the recorded diagnostic. | Reverted. |
| Graph fact pack | Passing more structured graph facts to the generator would improve grounding. | Small A/B runs showed lower citation and faithfulness; no limit-100 significant win was established. | Off by default; requires narrower evidence-span selection before revisiting. |
| Retrieval-only diagnosis | Improving broad recall alone would solve most failures. | Failure study showed many errors occurred even when the right documents were retrieved. In the first 20 base failures, `retrieval_ok_generation_bad` appeared 9 times and `numeric_value_mismatch` appeared 11 times. | Shifted focus to metric extraction, period-value tables, and evidence ordering. |

## Experiment Details

### 1. ToG-Style Fact Sentences

**What we tried.** The `graphrag_agent/financial/fact_stitching.py` path adds
short fact sentences next to retrieved evidence chunks. This follows the broad
idea that structured triples or facts can help an LLM see the relation behind a
text span.

**Hypothesis.** Financial QA often needs exact metric-period-value bindings. A
short sentence such as a company, fiscal period, metric, and value should reduce
the model's need to infer structure from long earnings-call excerpts.

**Experiment.** ECT-QA `new`, `answer_filter=answerable`, `limit=100`, TF-IDF
retriever, `TemporalEvidenceAgent`, full `gpt-4.1-mini` LLM judge. The control
was the `table_only` prompt; the treatment enabled fact sentences and disabled
pseudo questions.

| Metric | table_only | fact_sentences | Delta |
| --- | ---: | ---: | ---: |
| judge correct-like | 0.190 | 0.160 | -0.030 |
| numerical_reasoning | 0.318 | 0.312 | -0.006 |
| evidence_faithfulness | 0.467 | 0.455 | -0.012 |
| citation_validity | 0.338 | 0.312 | -0.026 |
| doc_recall@8 | 0.896 | 0.896 | +0.000 |
| evidence_text_recall@8 | 0.431 | 0.431 | +0.000 |

**Why it failed.** The added sentences did not retrieve new evidence. They
expanded prompt surface area without improving evidence-span localization, so the
generator had more text to reconcile but not better-ranked support. For this
project, the useful structure was the evidence table and graph ordering, not
extra prose attached to every chunk.

### 2. HopRAG-Style Pseudo Questions

**What we tried.** The `graphrag_agent/financial/pseudo_questions.py` path
generates supplementary pseudo questions for financial facts and uses them as an
extra recall surface.

**Hypothesis.** ECT-QA questions paraphrase financial metrics in many ways.
Pseudo questions might bridge wording gaps between the user question and the
transcript chunks.

**Experiment.** Same limit-100 setting as above. The `pseudoq_table` variant
disabled fact sentences and enabled `temporal_pseudo_questions=8`.

| Metric | table_only | pseudoq_table | Delta |
| --- | ---: | ---: | ---: |
| judge correct-like | 0.190 | 0.180 | -0.010 |
| numerical_reasoning | 0.318 | 0.313 | -0.005 |
| evidence_faithfulness | 0.467 | 0.472 | +0.005 |
| citation_validity | 0.338 | 0.347 | +0.009 |
| doc_recall@8 | 0.896 | 0.890 | -0.007 |
| evidence_text_recall@8 | 0.431 | 0.417 | -0.014 |
| all_support_recall@8 | 0.790 | 0.770 | -0.020 |

**Why it failed.** Pseudo questions were too broad for this domain. They could
bring in semantically nearby financial text, but nearby is not enough when the
answer depends on the exact company, fiscal period, metric, and value kind. The
small citation gains did not translate into answer correctness.

### 3. Combined Fact Sentences And Pseudo Questions

**What we tried.** The combined variant enabled both fact sentences and eight
pseudo questions.

**Hypothesis.** Fact sentences could make the recalled evidence easier to use,
while pseudo questions could improve coverage.

**Experiment.** Same limit-100 ablation matrix.

| Metric | table_only | combined | Delta |
| --- | ---: | ---: | ---: |
| judge correct-like | 0.190 | 0.150 | -0.040 |
| numerical_reasoning | 0.318 | 0.306 | -0.012 |
| evidence_faithfulness | 0.467 | 0.392 | -0.075 |
| citation_validity | 0.338 | 0.286 | -0.052 |
| doc_recall@8 | 0.896 | 0.890 | -0.007 |
| evidence_text_recall@8 | 0.431 | 0.417 | -0.014 |

**Why it failed.** The two additions compounded rather than corrected each
other. They increased the amount of candidate text and relation-like language,
but the model still had to pick exact numeric spans from dense transcripts. This
made faithfulness and citation quality worse.

### 4. Global TF-IDF Safety Net

**What we tried.** A broader TF-IDF fallback was tested as a safety net when the
main candidate scope missed evidence.

**Hypothesis.** If graph or metadata filters are too narrow, a global text
fallback should recover missing support documents.

**Experiment.** Recorded diagnostic in `docs/RESULTS.md` under "Additional
negative experiments".

| Metric | Observed change |
| --- | ---: |
| doc_recall@8 | +0.004 |
| evidence_text_recall@8 | -0.086 |

**Why it failed.** The fallback recovered little at the document level and hurt
evidence-span quality. In this dataset, wrong-period or same-topic chunks are
especially dangerous: they look relevant but can carry the wrong number. The
safety net therefore increased noise more than usable support.

### 5. Graph Fact Pack

**What we tried.** The `graphrag_agent/financial/temporal_graph_facts.py` path
can pass a more structured graph-fact pack to generation.

**Hypothesis.** Structured facts should reduce hallucinated joins and improve
numeric grounding.

**Experiment.** Exploratory small A/B runs were recorded in the project results.
These runs did not establish a limit-100 win and showed lower citation and
faithfulness.

**Why it failed.** A fact pack is only as good as its span selection. If the pack
contains nearby but not answer-bearing facts, it can make the prompt look more
authoritative while still pointing the model at the wrong value. This should be
revisited only after the extractor can produce narrower period-value tables for
more metric families.

### 6. Retrieval-Only Diagnosis

**What we tried.** Early analysis treated retrieval coverage as the likely main
bottleneck.

**Hypothesis.** If we improve recall, answer quality should rise directly.

**Experiment.** The base failure case study inspected 20 failures from the
limit-100 incremental run.

| Failure label | Count in first 20 failures |
| --- | ---: |
| numeric_value_mismatch | 11 |
| retrieval_ok_generation_bad | 9 |
| evidence_span_gap | 7 |
| missing_gold_document | 5 |
| missing_full_support_set | 5 |
| temporal_coverage_gap | 5 |
| synthesis_or_reasoning_error | 4 |
| wrong_refusal_policy | 2 |

**Why it failed as a diagnosis.** Retrieval was important, but it was not the
whole problem. Many answers failed after the right files had already been
retrieved. The practical lesson was to optimize for period-value grounding and
evidence ordering, not broad recall alone.

## What These Results Changed

- The default generation input became the compact evidence table rather than
  fact-sentence or pseudo-question expansion.
- The strongest later result came from graph-based evidence reranking: in the
  final limit-100 comparison, graph improved answer correctness by `+0.092`
  over TF-IDF with 95% CI `[+0.014, +0.168]`.
- The project treats doc recall as a diagnostic metric, not the sole objective.
  In the headline run, graph had lower doc recall (`-0.039`) but better answer
  correctness, numerical reasoning, and completeness.
- Future work should validate robustness with another judge model and with
  controlled distractor/noise settings before making broader claims.

## Reproducibility Notes

The compact reports are kept in git, while very large process-level JSON files
are intentionally excluded from the public repository. The headline graph-vs-
TF-IDF artifacts remain available under
`docs/regression_wp3_limit100_20260526/`. For new experiments, use `outputs/`
for generated JSON and copy only summarized, reviewable reports back into
`docs/`.
