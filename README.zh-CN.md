# Temporal-FinGraphRAG

> 面向财报电话会问答的金融时序 GraphRAG。在 ECT-QA 上，相比原始
> `graph-rag-agent` baseline，`temporal_alignment` 从 **0.178 提升到
> 0.921**；在系统内部，Graph retriever 相比 TF-IDF 在
> `answer_correctness` 和 `numerical_reasoning` 上取得 95% CI 显著提升。

> 本项目基于开源项目 [graph-rag-agent](https://github.com/1517005260/graph-rag-agent) 二次开发，原项目采用 MIT License，Copyright 2025 GLK。本仓库保留上游 License 与 NOTICE，并聚焦展示新增的金融时序 RAG、ECT-QA 评估与实验报告部分。

![Temporal-FinGraphRAG architecture](docs/assets/architecture.svg)

## 为什么做这个项目

金融财报电话会问答的难点很具体：同一家公司会在每个财季反复报告同一类指标，而且一句话里经常同时包含当前值、去年同期值、环比/同比变化和管理层解释。普通文本检索可以找到相关 transcript，但仍然容易混淆年份、财季、指标和数值类型。

Temporal-FinGraphRAG 将任务收敛到 ECT-QA 财报问答，并把检索链路显式改造成时间感知流程。它不是只按文本相似度排序 chunk，而是先抽取金融指标事实，构建 Fin* 时序图谱，再按公司和财季过滤候选证据，最后用 TF-IDF + Personalized PageRank 做证据重排。

项目同时强调评估闭环：README 中的核心结果均对应 ECT-QA `limit=100` 实验、`gpt-4.1-mini` full LLM judge，以及可用的 paired bootstrap 95% confidence interval。

## 核心能力

- 构建金融时序图谱：将公司、财季、财务指标、证据 chunk 和 FinFact 事实节点组织成 Neo4j 图结构。
- 识别金融指标事实：抽取 revenue、EPS、free cash flow、cash and investments、gross margin 等指标及其数值类型。
- 时间感知检索：区分同一公司在不同 year/quarter 下的事实，减少跨期证据污染。
- PPR 图扩散重排：在 FinFact-FinChunk 图上执行 Personalized PageRank，并与词法检索结果融合。
- 完整评估闭环：包含 ECT-QA 规则评估、检索指标、LLM Judge、消融实验和增量评估协议。

## 主要结果

### 原始 baseline vs. Temporal-FinGraphRAG

ECT-QA `new` / `answerable` / `limit=100`，full LLM Judge，judge model 为 `gpt-4.1-mini`：

| 指标 | 原始 baseline 最优值 | Temporal-FinGraphRAG | 提升 |
| --- | ---: | ---: | ---: |
| judge correct_like | 0.010 | 0.220 | +0.210 |
| answer_correctness | 0.016 | 0.421 | +0.405 |
| evidence_faithfulness | 0.045 | 0.539 | +0.494 |
| temporal_alignment | 0.178 | 0.921 | +0.743 |
| numerical_reasoning | 0.014 | 0.390 | +0.376 |

### Graph retriever vs. TF-IDF

在改造后的系统内部，ECT-QA `new` / `answerable` / `limit=100`，`table_only`：

| 指标 | TF-IDF | Graph | 提升 | 95% CI |
| --- | ---: | ---: | ---: | --- |
| answer_correctness | 0.329 | 0.421 | +0.092 | [+0.014, +0.168] |
| numerical_reasoning | 0.307 | 0.390 | +0.083 | [+0.011, +0.158] |
| answer_completeness | 0.400 | 0.482 | +0.083 | [+0.007, +0.161] |
| evidence_faithfulness | 0.479 | 0.539 | +0.060 | [-0.024, +0.142] |
| doc_recall@8 | 0.896 | 0.857 | -0.039 | [-0.080, -0.008] |

结果说明：图检索牺牲了一部分宽泛文档召回，但改善了指标和时间范围相关证据的排序，从而提升答案正确性、数值推理和完整性。

## 贡献范围

| 模块 | 路径 | 说明 |
| --- | --- | --- |
| 金融时序 RAG 核心 | `graphrag_agent/financial/` | FinFact 抽取、财季解析、时间检索、图重排、答案生成。 |
| 图谱构建 | `graphrag_agent/integrations/build/build_financial_graph.py` | 构建金融时序 Neo4j 图谱。 |
| 评估脚本 | `scripts/ectqa_*.py` | ECT-QA 评估、LLM Judge、消融实验、增量评估。 |
| 单元测试 | `test/test_metric_extractors.py` | 金融指标抽取的确定性测试。 |
| 文档与实验记录 | `docs/` | 架构、结果、正式评估报告、case study 和关键实验产物。 |

## 快速开始

创建环境并安装依赖：

```bash
conda create -n temporag-fin python==3.10
conda activate temporag-fin
pip install -r requirements.txt
pip install -e .
```

启动 Neo4j：

```bash
docker compose up -d
```

配置环境变量：

```bash
cp .env.example .env
```

然后在 `.env` 中填写：

```bash
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
```

如果使用中转 API，将 `OPENAI_BASE_URL` 改成对应地址即可。

## 构建金融时序图谱

```bash
PYTHONPATH=. python graphrag_agent/integrations/build/build_financial_graph.py \
  --scenario new \
  --corpus-scope full \
  --wipe
```

## 运行小规模评估

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

## 运行 LLM Judge

```bash
PYTHONPATH=. python scripts/ectqa_llm_judge.py \
  --input-json outputs/smoke_graph.json \
  --judge-profile full \
  --judge-model gpt-4.1-mini
```

## 运行测试

```bash
PYTHONPATH=. python test/test_metric_extractors.py
```

## 重要文档

| 文档 | 内容 |
| --- | --- |
| `docs/ARCHITECTURE.md` | 系统架构、图谱 schema、数据流和运行命令。 |
| `docs/RESULTS.md` | 主实验结果、消融实验、增量评估和 caveats。 |
| `docs/formal_eval_report_20260525.md` | 正式评估报告。 |
| `NOTICE` | 上游项目来源和许可证说明。 |

## 评估产物说明

仓库中保留了压缩后的核心报告和关键结果文件。大规模过程性 JSON、临时 smoke 输出、完整 LLM Judge backfill 等文件没有放入 GitHub，相关数字已经固化在 `docs/RESULTS.md` 中。

当前保留的 headline artifact 位于：

```text
docs/regression_wp3_limit100_20260526/
```

## License

本项目保留上游 MIT License。详细归属说明见 `LICENSE` 和 `NOTICE`。
