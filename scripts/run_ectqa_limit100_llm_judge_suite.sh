#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/bian/projects/graph-rag-agent"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
RUN_ID="${LLM_JUDGE_RUN_ID:-$(date '+%Y%m%d_%H%M%S')}"
OUTPUT_DIR="${LLM_JUDGE_OUTPUT_DIR:-$PROJECT_ROOT/docs/llm_judge_runs/$RUN_ID}"
LOG_FILE="$OUTPUT_DIR/ectqa_llm_judge_limit100_suite.log"

cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT"
export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false

mkdir -p "$OUTPUT_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

run_judge() {
  local name="$1"
  local input_json="$2"
  local output_json="$3"
  local run_log="$4"
  shift 4

  log "START $name"
  log "input_json=$input_json"
  log "output_json=$output_json"

  set +e
  "$PYTHON" "$PROJECT_ROOT/scripts/ectqa_llm_judge.py" \
    --input-json "$input_json" \
    --output-json "$output_json" \
    "$@" > "$run_log" 2>&1
  local status=$?
  set -e

  if [[ "$status" -ne 0 ]]; then
    log "FAILED $name status=$status"
    log "See log: $run_log"
    exit "$status"
  fi

  log "FINISHED $name"
  "$PYTHON" - "$output_json" <<'PY' | tee -a "$LOG_FILE"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
overall = data.get("overall", {})
print(f"summary_file={path}")
print(f"overall_llm_judge={overall.get('llm_judge')}")
for agent, metrics in data.get("agents", {}).items():
    print(f"agent={agent} llm_judge={metrics.get('llm_judge')}")
PY
}

EXTRA_ARGS=()
if [[ -n "${LLM_JUDGE_LIMIT_ROWS:-}" ]]; then
  EXTRA_ARGS+=(--limit-rows "$LLM_JUDGE_LIMIT_ROWS")
fi
if [[ -n "${LLM_JUDGE_AGENTS:-}" ]]; then
  EXTRA_ARGS+=(--agents "$LLM_JUDGE_AGENTS")
fi
if [[ -n "${LLM_JUDGE_PROFILE:-}" ]]; then
  EXTRA_ARGS+=(--judge-profile "$LLM_JUDGE_PROFILE")
fi
if [[ -n "${LLM_JUDGE_MODEL:-}" ]]; then
  EXTRA_ARGS+=(--judge-model "$LLM_JUDGE_MODEL")
fi
if [[ "${LLM_JUDGE_ONLY_RULE_ERRORS:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--only-rule-errors)
fi

log "ECT-QA limit=100 LLM judge suite started"
log "output_dir=$OUTPUT_DIR"
log "Set LLM_JUDGE_LIMIT_ROWS to run a cheaper sample, or leave unset for full files."
log "Set LLM_JUDGE_PROFILE=full to include all judge dimensions; default is focused."
log "Default judge model is gpt-4.1-mini; set LLM_JUDGE_MODEL to override."

run_judge \
  "answerable_baseline_off_limit100_llm_judge" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100.json" \
  "$OUTPUT_DIR/ectqa_eval_full_answerable_limit100_llm_judged.json" \
  "$OUTPUT_DIR/ectqa_eval_full_answerable_limit100_llm_judge.log" \
  "${EXTRA_ARGS[@]}"

run_judge \
  "answerable_metadata_boost_limit100_llm_judge" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json" \
  "$OUTPUT_DIR/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judged.json" \
  "$OUTPUT_DIR/ectqa_eval_full_answerable_limit100_metadata_boost_llm_judge.log" \
  "${EXTRA_ARGS[@]}"

run_judge \
  "unanswerable_metadata_boost_refusal_guard_limit100_llm_judge" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_unanswerable_limit100_refusal_guard.json" \
  "$OUTPUT_DIR/ectqa_eval_full_unanswerable_limit100_refusal_guard_llm_judged.json" \
  "$OUTPUT_DIR/ectqa_eval_full_unanswerable_limit100_refusal_guard_llm_judge.log" \
  "${EXTRA_ARGS[@]}"

log "ECT-QA limit=100 LLM judge suite completed"
