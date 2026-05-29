#!/usr/bin/env bash
set -euo pipefail

export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false

python_bin=".venv/bin/python"
judge_script="scripts/ectqa_llm_judge.py"

inputs=(
  "docs/incremental_runs/temporal_limit100_20260525/base_old_questions_old_corpus_limit100.json"
  "docs/incremental_runs/temporal_limit100_20260525/retention_old_questions_updated_corpus_limit100.json"
  "docs/incremental_runs/temporal_limit100_20260525/new_questions_updated_corpus_limit100.json"
  "docs/ablation_runs/new_limit100_20260525/new_table_only_limit100.json"
  "docs/ablation_runs/new_limit100_20260525/new_fact_sentences_limit100.json"
  "docs/ablation_runs/new_limit100_20260525/new_pseudoq_table_limit100.json"
  "docs/ablation_runs/new_limit100_20260525/new_fact_sentences_pseudoq_limit100.json"
)

for input_json in "${inputs[@]}"; do
  output_json="${input_json%.json}_llm_judged.json"
  echo "[judge] ${input_json} -> ${output_json}"
  "${python_bin}" "${judge_script}" \
    --input-json "${input_json}" \
    --output-json "${output_json}" \
    --judge-profile full \
    --judge-model gpt-4.1-mini \
    --judge-temperature 0 \
    --judge-max-tokens 700 \
    --judge-max-evidence 5 \
    --judge-evidence-chars 700 \
    --judge-max-answer-chars 1800 \
    --checkpoint-every 10 \
    --quiet
done
