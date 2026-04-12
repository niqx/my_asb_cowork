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
    -p "Today is $TODAY ($WEEKDAY), time: 12:00 midday. Generate a HEALTH CHECK message.

=== INSTRUCTIONS ===
1. Call Oura MCP tools to get TODAY's data:
   - get_daily_sleep (last night's sleep score, duration, efficiency)
   - get_readiness (recovery score)
   - get_daily_stress (current stress level)
   - get_heart_rate (resting HR, current trends)
2. Read today's daily log from vault (if exists) and yesterday's daily log

${NUTRITION_BLOCK}

=== OUTPUT FORMAT ===
Generate a Telegram message in HTML. NOT a data dump — an insightful check-in.

<b>🫀 Здоровье — полдень</b>

<b>Ночь:</b> One sentence verdict — good/bad sleep, recovered or not. Mention numbers only if something is unusual.

<b>Сейчас:</b> Stress/HRV assessment. If stress is elevated or HRV is low — reference what's in today's notes or calendar.

<b>🍽 Питание:</b> ALWAYS include this block. Show: calories eaten vs goal (e.g. «1200 из 2650 ккал»), key macros status (protein/fat/carbs). Then one sentence correlating food data with Oura readings — e.g. low calories + high fatigue, or good protein intake supporting recovery. End with one concrete food tip for the rest of the day.

<b>Рекомендация:</b> One actionable suggestion combining health state + nutrition + today's tasks.

If stress is high, end with: 'Что сейчас давит? Расскажи.'

RULES:
- Russian language
- HTML formatting only (no markdown)
- Don't just list raw numbers — give insights and correlations
- If Oura data is unavailable, say so briefly and skip those sections
- If no meals logged yet, note it and suggest logging first meal" \
    2>&1) || true
cd "$PROJECT_DIR"

echo "=== Claude output ==="
echo "$REPORT"

REPORT_CLEAN=$(clean_claude_output "$REPORT")
send_telegram "$REPORT_CLEAN"

echo "=== Health check done ==="
