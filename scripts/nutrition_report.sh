#!/bin/bash
# nutrition_report.sh — ночной отчёт о здоровье (еда + Oura)
# Запускается в 00:00 через d-brain-nutrition.timer
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

echo "=== Nutrition/health report for $TODAY ==="

run_nutrition() {
    cd "$PROJECT_DIR" && TODAY="$TODAY" \
        uv run python -m d_brain.pipeline nutrition \
        2>>"$PROJECT_DIR/logs/pipeline-nutrition-$TODAY.log"
}

set +e
REPORT=$(run_nutrition); RC=$?
if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    echo "WARN: nutrition pipeline failed (rc=$RC, len=${#REPORT}) — retrying once"
    sleep 5
    REPORT=$(run_nutrition); RC=$?
fi
set -e

if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    REPORT="🌙 <b>Здоровье за день</b>
<i>Отчёт временно недоступен — сессия не ответила.</i>"
fi

echo "=== Claude output ==="
echo "$REPORT"

REPORT_CLEAN=$(clean_claude_output "$REPORT")
send_telegram "$REPORT_CLEAN"

echo "=== Nutrition report done ==="
