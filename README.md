# TempoRAG-Fin

[中文说明](README.zh-CN.md)

> Time-aware financial GraphRAG for earnings-call QA. On ECT-QA, it improves
> `temporal_alignment` from **0.178 to 0.921** against the original
> `graph-rag-agent` baseline, while the graph retriever delivers
> CI-significant gains over TF-IDF in answer correctness and numerical
> reasoning.

> Built on top of
> [graph-rag-agent](https://github.com/1517005260/graph-rag-agent)
> (MIT License, Copyright 2025 GLK). This repository is a slim research copy:
> the financial temporal RAG track, ECT-QA evaluation harness, LLM judge, and
> project reports are the added contribution; the original generic GraphRAG
> product surfaces were removed to keep the portfolio project focused.

![TempoRAG-Fin architecture](docs/assets/architecture.svg)

## Why This Project

Financial earnings-call QA is hard for a very specific reason: the same company
reports the same metric every quarter, often with current values, prior-period
comparisons, and delta language packed into one sentence. A generic retriever
can find a relevant transcript but still mix up the year, quarter, metric, or
value kind.

TempoRAG-Fin narrows the task to ECT-QA earnings-call questions and makes the
retrieval path explicitly time-aware. Instead of only ranking text chunks by
surface similarity, it extracts typed financial facts, builds a Fin* temporal
graph, filters candidates by company and period, and reranks evidence through a
TF-IDF + Personalized PageRank blend.

The project also keeps evaluation central: every headline claim is tied to
ECT-QA runs, full `gpt-4.1-mini` LLM judge results, and paired bootstrap 95%
confidence intervals where available.

## What This Adds

- A Fin* temporal graph schema for companies, fiscal periods, metrics, evidence
  chunks, and typed FinFact nodes.
- Deterministic metric extraction for revenue, EPS, free cash flow, gross
  margin, cash/investments, and related financial values.
- Time-filtered retrieval that distinguishes the same company or metric across
  years and quarters.
- Personalized PageRank reranking over the FinFact-FinChunk graph, blended with
  in-scope TF-IDF.
- ECT-QA rule metrics, retrieval metrics, LLM judge, ablations, and incremental
  base/retention/new evaluation.

## Headline Results

### Original Baseline vs. TempoRAG-Fin

ECT-QA `new` / `answerable`, `limit=100`, full LLM judge with
`gpt-4.1-mini`:

| Metric | Original best baseline | TempoRAG-Fin graph | Delta |
| --- | ---: | ---: | ---: |
| judge correct_like | 0.010 | 0.220 | +0.210 |
| answer_correctness | 0.016 | 0.421 | +0.405 |
| evidence_faithfulness | 0.045 | 0.539 | +0.494 |
| temporal_alignment | 0.178 | 0.921 | +0.743 |
| numerical_reasoning | 0.014 | 0.390 | +0.376 |

### Graph Retriever vs. TF-IDF

Inside TempoRAG-Fin on ECT-QA `new` / `answerable`, `limit=100`,
`table_only`:

| Metric | TF-IDF | Graph | Delta | 95% CI |
| --- | ---: | ---: | ---: | --- |
| answer_correctness | 0.329 | 0.421 | +0.092 | [+0.014, +0.168] |
| numerical_reasoning | 0.307 | 0.390 | +0.083 | [+0.011, +0.158] |
| answer_completeness | 0.400 | 0.482 | +0.083 | [+0.007, +0.161] |
| evidence_faithfulness | 0.479 | 0.539 | +0.060 | [-0.024, +0.142] |
| doc_recall@8 | 0.896 | 0.857 | -0.039 | [-0.080, -0.008] |

The graph retriever trades some broad document recall for better
period/metric-specific evidence ordering, which improves answer quality.

## Contribution Map

| Area | Paths | Role |
| --- | --- | --- |
| Financial temporal RAG core | `graphrag_agent/financial/` | FinFact extraction, fiscal-period parsing, temporal retrieval, graph reranking, answer assembly. |
| Graph build path | `graphrag_agent/integrations/build/build_financial_graph.py` | Builds the Fin* Neo4j graph used by the graph retriever. |
| Evaluation harness | `scripts/ectqa_*.py`, `scripts/run_*.sh` | ECT-QA runs, LLM judge, token estimation, ablation matrix, incremental protocol. |
| Tests | `test/test_metric_extractors.py`, `test/gold_value_kind.jsonl` | Deterministic coverage for metric/value-kind extraction. |
| Documentation | `README.md`, `README.zh-CN.md`, `NOTICE`, `docs/*.md` | Architecture, results, evaluation reports, and attribution. |
| Upstream base | `LICENSE`, package layout, project conventions | MIT-licensed foundation from `graph-rag-agent`, retained with attribution. |

## Key Docs

| Doc | Purpose |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, Fin* graph schema, data flow, and run commands. |
| [docs/RESULTS.md](docs/RESULTS.md) | Main result tables, ablations, incremental evaluation, and caveats. |
| [NOTICE](NOTICE) | Upstream attribution and third-party provenance. |

## Quick Start

```bash
conda create -n temporag-fin python==3.10
conda activate temporag-fin
pip install -r requirements.txt
pip install -e .

docker compose up -d
cp .env.example .env
# Fill OPENAI_API_KEY and, if needed, OPENAI_BASE_URL.
```

Build the financial temporal graph:

```bash
PYTHONPATH=. python graphrag_agent/integrations/build/build_financial_graph.py \
  --scenario new \
  --corpus-scope full \
  --wipe
```

Run a small graph-retriever smoke evaluation:

```bash
PYTHONPATH=. python scripts/ectqa_eval.py \
  --scenario new \
  --answer-filter answerable \
  --limit 5 \
  --agents TemporalEvidenceAgent \
  --metadata-filter boost \
  --retriever graph \
  --output-json outputs/smoke_graph.json \
  --quiet
```

Optionally run LLM judge on an existing result:

```bash
PYTHONPATH=. python scripts/ectqa_llm_judge.py \
  --input-json outputs/smoke_graph.json \
  --judge-profile full \
  --judge-model gpt-4.1-mini
```

Run unit tests for the deterministic metric extractor:

```bash
PYTHONPATH=. python test/test_metric_extractors.py
```

## Evaluation Artifacts

The repository keeps compact reports and the main graph-vs-TF-IDF evidence
artifacts under `docs/regression_wp3_limit100_20260526/`. Large process-level
JSON outputs from smoke runs, ablations, incremental sweeps, and LLM judge
backfills are intentionally excluded from git; the summarized numbers are
preserved in [docs/RESULTS.md](docs/RESULTS.md).

## License

This project retains the upstream MIT License. See [LICENSE](LICENSE) and
[NOTICE](NOTICE) for attribution details.
