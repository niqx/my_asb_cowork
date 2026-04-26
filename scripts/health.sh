#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

# ── Toggle check ──
if [ "${HEALTH_ENABLED:-false}" != "true" ]; then
    echo "Health module disabled (HEALTH_ENABLED!=true) — exiting"
    exit 0
fi

WEEKDAY=$(LC_TIME=ru_RU.UTF-8 date +%A 2>/dev/null || date +%A)

echo "=== Health check for $TODAY ==="

# ── FREE RAM: stop bot during heavy processing ──
BOT_WAS_RUNNING=false
if systemctl is-active --quiet d-brain-bot; then
    BOT_WAS_RUNNING=true
    echo "=== Pausing bot to free RAM ==="
    sudo systemctl stop d-brain-bot || true
fi
trap '
    if [ "$BOT_WAS_RUNNING" = true ]; then
        echo "=== Restarting bot ==="
        sudo systemctl start d-brain-bot || true
    fi
' EXIT

# ── Fetch nutrition context from Supabase ──
NUTRITION_CONTEXT=""
if [ "${NUTRITION_ENABLED:-true}" = "true" ] && [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
    echo "=== Fetching nutrition context ==="
    NUTRITION_CONTEXT=$(cd "$PROJECT_DIR" && uv run python scripts/nutrition_context.py 2>/dev/null || echo "")
fi

NUTRITION_BLOCK=""
if [ -n "$NUTRITION_CONTEXT" ]; then
    NUTRITION_BLOCK="=== ПИТАНИЕ (данные из трекера) ===
${NUTRITION_CONTEXT}"
fi

cd "$VAULT_DIR"
REPORT=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 \
    --mcp-config "$PROJECT_DIR/mcp-config.json" \
    -p "Today is $TODAY ($WEEKDAY), time: 23:00 evening. Generate an END-OF-DAY HEALTH SUMMARY message.

=== INSTRUCTIONS ===
1. Call Oura MCP tools to get TODAY's data:
   - get_daily_sleep (last night's sleep score, duration, efficiency)
   - get_readiness (recovery score)
   - get_daily_stress (full day stress timeline)
   - get_heart_rate (resting HR, daily trends)
   - get_daily_activity (steps, active calories, movement goal)
2. Read today's daily log from vault (if exists)

${NUTRITION_BLOCK}

=== OUTPUT FORMAT ===
Generate a Telegram message in HTML. NOT a data dump — an insightful end-of-day summary.

<b>🫀 Здоровье — итог дня</b>

<b>Ночь:</b> One sentence verdict — good/bad sleep, recovered or not. Mention numbers only if something is unusual.

<b>День:</b> Stress and activity summary. Correlate stress peaks with what happened today (from daily notes). Steps vs goal.

<b>🍽 Питание:</b> ALWAYS include this block. Show: total calories vs goal, key macros status (protein/fat/carbs). One sentence on how nutrition correlated with energy and stress today.

<b>Итог:</b> One sentence overall assessment of the day — recovered, stressed, active, or resting. One tip for tonight (sleep hygiene, wind-down).

RULES:
- Russian language
- HTML formatting only (no markdown)
- Don't just list raw numbers — give insights and correlations
- If Oura data is unavailable, say so briefly and skip those sections
- Focus on END-OF-DAY perspective, not midday check-in" \
    2>&1) || true
cd "$PROJECT_DIR"

echo "=== Claude output ==="
echo "$REPORT"

REPORT_CLEAN=$(clean_claude_output "$REPORT")
send_telegram "$REPORT_CLEAN"

echo "=== Health check done ==="
