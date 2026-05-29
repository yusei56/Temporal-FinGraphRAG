#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/bian/projects/graph-rag-agent"
RUN_ID="${LLM_JUDGE_RUN_ID:-$(date '+%Y%m%d_%H%M%S')}"
OUTPUT_DIR="${LLM_JUDGE_OUTPUT_DIR:-$PROJECT_ROOT/docs/llm_judge_runs/$RUN_ID}"

cd "$PROJECT_ROOT"

mkdir -p "$OUTPUT_DIR"
printf '%s\n' "$OUTPUT_DIR" > "$PROJECT_ROOT/docs/llm_judge_runs/latest_run_dir.txt"

export LLM_JUDGE_RUN_ID="$RUN_ID"
export LLM_JUDGE_OUTPUT_DIR="$OUTPUT_DIR"
export LLM_JUDGE_PROFILE="${LLM_JUDGE_PROFILE:-full}"
export LLM_JUDGE_MODEL="${LLM_JUDGE_MODEL:-gpt-4.1-mini}"

setsid -f bash "$PROJECT_ROOT/scripts/run_ectqa_limit100_llm_judge_suite.sh" \
  > "$OUTPUT_DIR/detached.log" 2>&1

printf 'OUTPUT_DIR=%s\n' "$OUTPUT_DIR"
