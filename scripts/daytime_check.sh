#!/bin/bash
# daytime_check.sh — every 2 hours check: analyze progress and send tips
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
HOUR=$(TZ="${LOCATION_TZ:-Europe/Moscow}" date +%H)

# Only run between 09:00 and 22:00 local time
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -ge 22 ]; then
    echo "Outside active hours ($HOUR:xx), skipping daytime check."
    exit 0
fi

WEEKDAY=$(LC_TIME=ru_RU.UTF-8 date +%A 2>/dev/null || date +%A)
CURRENT_HOUR=$(TZ="${LOCATION_TZ:-Europe/Moscow}" date +%H:%M)

# Read today's daily file
DAILY_FILE="$VAULT_DIR/daily/$TODAY.md"
DAILY_CONTENT=""
if [ -f "$DAILY_FILE" ]; then
    DAILY_CONTENT=$(cat "$DAILY_FILE")
fi

# Read today's tasks
TASKS_FILE="$VAULT_DIR/tasks/$TODAY.md"
TASKS_CONTENT=""
if [ -f "$TASKS_FILE" ]; then
    TASKS_CONTENT=$(cat "$TASKS_FILE")
fi

# Read weekly goals for context
WEEKLY_GOALS=""
if [ -f "$VAULT_DIR/goals/3-weekly.md" ]; then
    WEEKLY_GOALS=$(head -40 "$VAULT_DIR/goals/3-weekly.md")
fi

# Fetch Oura activity for today (steps, calories, movement goal)
OURA_ACTIVITY=""
if [ "${HEALTH_ENABLED:-false}" = "true" ]; then
    OURA_ACTIVITY=$(claude --print --dangerously-skip-permissions --model claude-haiku-4-5-20251001 \
        --mcp-config "$PROJECT_DIR/mcp-config.json" \
        -p "Call oura_get_daily_activity with start_date='$TODAY' end_date='$TODAY'. Return ONE line in Russian: шаги, active calories, distance, % от цели активности. Example: Активность: 6 420 шагов, 340 ккал, 4.8 км, цель 72%. Return ONLY that line, nothing else." \
        2>&1) || OURA_ACTIVITY=""
    OURA_ACTIVITY=$(echo "$OURA_ACTIVITY" | grep -v '^$' | grep -v 'Here is\|Let me\|Calling\|I will\|Вот' | head -3 || echo "$OURA_ACTIVITY")
fi

ACTIVITY_SECTION=""
if [ -n "$OURA_ACTIVITY" ]; then
    ACTIVITY_SECTION="
=== АКТИВНОСТЬ (OURA) ===
${OURA_ACTIVITY}"
fi

REPORT=$(claude --print --dangerously-skip-permissions --model claude-haiku-4-5-20251001 \
    --mcp-config "$PROJECT_DIR/mcp-config.json" \
    -p "Ты — персональный ассистент d-brain. Сейчас ${CURRENT_HOUR} MSK, ${TODAY} (${WEEKDAY}).

=== ЗАПИСИ ЗА СЕГОДНЯ ===
${DAILY_CONTENT:-[нет записей]}

=== ЗАДАЧИ НА СЕГОДНЯ ===
${TASKS_CONTENT:-[нет задач]}

=== ЦЕЛИ НЕДЕЛИ ===
${WEEKLY_GOALS:-[не загружены]}
${ACTIVITY_SECTION}
=== ЗАДАЧА ===
Сделай короткий (3-5 предложений) дневной чек-ин:
1. Оцени как идёт день исходя из записей и задач
2. Дай 1-2 конкретных совета или запроса — что стоит сделать прямо сейчас
3. Если есть незакрытые задачи или просроченные — мягко напомни
4. Учитывай время дня: утро — мотивация/приоритеты, середина — фокус, вечер — подведение
5. Если есть данные активности — добавь одну строку: сколько шагов и % от цели; если цель < 50% и время > 16:00 — мягко подтолкни к прогулке

CRITICAL OUTPUT FORMAT:
- Return ONLY plain text, NO HTML tags, NO markdown, NO code blocks, NO explanations
- Use emojis for emphasis instead of formatting tags
- Start with: ☀️ Чек-ин ${CURRENT_HOUR}
- Max 10 строк — Telegram краткость важна" \
    2>&1) || true

REPORT_CLEAN=$(clean_claude_output "$REPORT")

if [ -n "$REPORT_CLEAN" ]; then
    send_telegram_silent "$REPORT_CLEAN"
    echo "Daytime check sent at $CURRENT_HOUR"
else
    echo "Empty report, skipping send."
fi
