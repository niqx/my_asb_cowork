#!/bin/bash
# insights.sh — еженедельная сводка рабочих инсайтов (вс 19:00)
# Запускается ПЕРЕД недельной рефлексией (вс 20:00), чтобы инсайты
# стали входными данными для рефлексии, а не отдельным отчётом.
set -e

source "$(dirname "$0")/common.sh"
init

# ISO week id for the current week (e.g. 2026-W39)
YEAR=$(date +%Y)
WEEK=$(date +%V)
WEEK_ID="${YEAR}-W${WEEK}"

INSIGHTS_DIR="$HOME/.dbrain/work/insights"
INSIGHTS_FILE="$INSIGHTS_DIR/${WEEK_ID}.md"

mkdir -p "$INSIGHTS_DIR"

echo "=== Work insights for $WEEK_ID ==="

run_insights() {
    cd "$PROJECT_DIR" && TODAY="$TODAY" \
        uv run python -m d_brain.pipeline work-insights \
        2>>"$PROJECT_DIR/logs/pipeline-insights-${TODAY}.log"
}

set +e
REPORT=$(run_insights); RC=$?
if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    echo "WARN: work-insights pipeline failed (rc=$RC, len=${#REPORT}) — retrying once"
    sleep 5
    REPORT=$(run_insights); RC=$?
fi
set -e

if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 30 ]; then
    REPORT="💡 <b>Инсайты недели</b>
<i>Отчёт временно недоступен — сессия не ответила.</i>"
fi

echo "=== Claude output ==="
echo "$REPORT"

REPORT_CLEAN=$(clean_claude_output "$REPORT")

# Сохранить в файл для недельной рефлексии (plain text, теги сохраняем)
{
    echo "# Рабочие инсайты ${WEEK_ID}"
    echo ""
    echo "$REPORT_CLEAN"
} > "$INSIGHTS_FILE"
echo "=== Insights saved to $INSIGHTS_FILE ==="

send_telegram "$REPORT_CLEAN"

echo "=== Work insights done ==="
