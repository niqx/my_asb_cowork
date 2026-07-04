#!/bin/bash
# nutrition_report.sh — еженедельный отчёт о здоровье (Oura + еда за неделю)
# Запускается в вс 21:00 через d-brain-nutrition.timer
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

# Отчёт запускается в воскресенье 21:00 и охватывает текущую неделю пн–вс.
# Сегодня (воскресенье) входит в диапазон.
WEEK_END="$TODAY"
WEEK_START=$(date -d "$TODAY - 6 days" +%Y-%m-%d)

echo "=== Weekly health report for $WEEK_START – $WEEK_END ==="

run_health_weekly() {
    cd "$PROJECT_DIR" && TODAY="$TODAY" WEEK_START="$WEEK_START" WEEK_END="$WEEK_END" \
        uv run python -m d_brain.pipeline health-weekly \
        2>>"$PROJECT_DIR/logs/pipeline-health-weekly-$WEEK_END.log"
}

set +e
REPORT=$(run_health_weekly); RC=$?
if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    echo "WARN: health-weekly pipeline failed (rc=$RC, len=${#REPORT}) — retrying once"
    sleep 5
    REPORT=$(run_health_weekly); RC=$?
fi
set -e

if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    REPORT="🌙 <b>Здоровье за неделю</b>
<i>Отчёт временно недоступен — сессия не ответила.</i>"
fi

echo "=== Claude output ==="
echo "$REPORT"

REPORT_CLEAN=$(clean_claude_output "$REPORT")
send_telegram "$REPORT_CLEAN"

echo "=== Weekly health report done ==="
