#!/bin/bash
# remind_night.sh — 23:00 reminder: log food and write daily reflection
# Processing starts at 23:30, so this gives 30 minutes to complete entries

source "$(dirname "$0")/common.sh"
init

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

# Fetch nutrition summary if available
NUTRITION=""
if [ "${NUTRITION_ENABLED:-true}" = "true" ] && [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
    NUTRITION=$(uv run python "$SCRIPTS_DIR/nutrition_context.py" 2>/dev/null || echo "")
fi

if [ -n "$NUTRITION" ]; then
    NUTRITION_BLOCK="
📊 <b>Питание сегодня:</b>
<pre>$(echo "$NUTRITION" | head -4)</pre>"
else
    NUTRITION_BLOCK=""
fi

MESSAGE="🌙 <b>Время закрыть день!</b>
${NUTRITION_BLOCK}
Через 30 минут (в 23:30) запущу обработку дня.

📝 Успей прислать:
• <b>Еду</b> — что ел сегодня, что не записал
• <b>Рефлексию</b> — как прошёл день, мысли, итоги

Чем полнее записи — тем точнее ночной отчёт 💬"

send_telegram "$MESSAGE"

echo "Night reminder sent at $(date)"
