#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== d-brain processing for $TODAY ==="

# ── FREE RAM: stop bot during heavy processing ──
BOT_WAS_RUNNING=false
if systemctl is-active --quiet d-brain-bot; then
    BOT_WAS_RUNNING=true
    echo "=== Pausing bot to free RAM ==="
    sudo systemctl stop d-brain-bot || true
fi
# Ensure bot restarts even on failure
trap '
    if [ "$BOT_WAS_RUNNING" = true ]; then
        echo "=== Restarting bot ==="
        sudo systemctl start d-brain-bot || true
    fi
' EXIT

# Pull latest vault changes
echo "=== Pulling latest vault changes ==="
cd "$PROJECT_DIR"
git pull --rebase --autostash || echo "Git pull failed (non-critical)"

# ── ORIENT PHASE: pre-flight checks ──
DAILY_FILE="$VAULT_DIR/daily/$TODAY.md"
HANDOFF_FILE="$VAULT_DIR/.session/handoff.md"
GRAPH_FILE="$VAULT_DIR/.graph/vault-graph.json"
SESSION_DIR="$VAULT_DIR/.session"
mkdir -p "$SESSION_DIR"

# Check daily file exists and has content
if [ ! -f "$DAILY_FILE" ]; then
    echo "ORIENT: daily/$TODAY.md not found — creating empty file"
    echo "# $TODAY" > "$DAILY_FILE"
fi

DAILY_SIZE=$(wc -c < "$DAILY_FILE" 2>/dev/null || echo "0")
if [ "$DAILY_SIZE" -lt 50 ]; then
    echo "ORIENT: daily/$TODAY.md is empty ($DAILY_SIZE bytes) — skipping Claude processing"
    echo "ORIENT: Running graph rebuild only"

    # Still rebuild graph and commit
    cd "$VAULT_DIR"
    uv run .claude/skills/graph-builder/scripts/analyze.py || echo "Graph rebuild failed (non-critical)"
    uv run .claude/skills/agent-memory/scripts/memory-engine.py decay . || echo "Memory decay failed (non-critical)"
    cd "$PROJECT_DIR"

    git add vault/
    git commit -m "chore: process daily $TODAY" || true
    git push || true
    echo "=== Done (empty daily, graph-only) ==="
    exit 0
fi

# Check handoff exists
if [ ! -f "$HANDOFF_FILE" ]; then
    echo "ORIENT: handoff.md not found — creating stub"
    echo -e "---\nupdated: $(date -Iseconds)\n---\n\n## Last Session\n(none)\n\n## Observations" > "$HANDOFF_FILE"
fi

# Check graph freshness (warn if >7 days old)
if [ -f "$GRAPH_FILE" ]; then
    GRAPH_AGE=$(( ($(date +%s) - $(stat -c %Y "$GRAPH_FILE" 2>/dev/null || echo 0)) / 86400 ))
    if [ "$GRAPH_AGE" -gt 7 ]; then
        echo "ORIENT: vault-graph.json is $GRAPH_AGE days old (>7)"
    fi
fi

echo "ORIENT: daily=$DAILY_SIZE bytes, handoff=OK"
# ── END ORIENT PHASE ──

# Find yearly goals file (auto-detect year)
YEARLY_GOALS=$(ls "$VAULT_DIR/goals/1-yearly-"*.md 2>/dev/null | tail -1)
YEARLY_GOALS_NAME=$(basename "$YEARLY_GOALS" 2>/dev/null || echo "1-yearly.md")

# MCP prompt (needed for Phase 2)
MCP_PROMPT="CRITICAL: MCP loads in 10-30 seconds. You are NOT in subprocess — MCP IS running, just initializing.
Algorithm: 1) Call tool. 2) Error? Wait 10 sec. 3) Call again. 4) Wait 20 sec. 5) Call — GUARANTEED to work.
DO NOT say MCP unavailable. It is available. Just wait and call."

CAPTURE_FILE="$SESSION_DIR/capture.json"
EXECUTE_FILE="$SESSION_DIR/execute.json"

cd "$VAULT_DIR"

# ── ORIENT: Log Check ──
LOG_ERRORS=$(journalctl -u d-brain-bot -u d-brain-process -u d-brain-morning \
    --since "24 hours ago" --priority err --no-pager -q 2>/dev/null | tail -20) || LOG_ERRORS=""

# ── 3-PHASE PIPELINE ──
# Phase 1: CAPTURE (classify entries → JSON) — no MCP needed
# Phase 2: EXECUTE (create tasks, save thoughts → JSON) — with MCP/Todoist
# Phase 3: REFLECT (generate HTML report, update MEMORY → HTML) — no MCP needed
# Each phase = fresh Claude context for better quality.

# ── Phase 1: CAPTURE ──
echo "=== Phase 1: CAPTURE ==="
CAPTURE=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 \
    -p "Today is $TODAY. Read .claude/skills/dbrain-processor/phases/capture.md and execute Phase 1.
Read daily/$TODAY.md, goals/3-weekly.md, goals/2-monthly.md, goals/$YEARLY_GOALS_NAME.
Classify each entry. Return ONLY JSON." \
    2>&1) || true

# Save raw output for debugging
echo "$CAPTURE" > "$SESSION_DIR/capture.raw.log"

# Extract JSON using robust parser
echo "$CAPTURE" | python3 "$SCRIPTS_DIR/extract_json.py" > "$CAPTURE_FILE" 2>/dev/null \
    || echo '{"error": "capture failed"}' > "$CAPTURE_FILE"

echo "Capture saved: $(wc -c < "$CAPTURE_FILE") bytes"

# Check if capture produced valid entries
if grep -q '"error"' "$CAPTURE_FILE"; then
    echo "WARN: Capture phase had issues, falling back to monolith mode"
    # Fallback to monolith processing (same as old process.sh)
    REPORT=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 \
        --mcp-config "$PROJECT_DIR/mcp-config.json" \
        -p "Today is $TODAY. TIME: 23:00. EVENING DAILY PROCESSING.

USE ONLY: dbrain-processor skill. Output template: Обработка за {DATE}
DO NOT use morning-briefer skill. DO NOT generate morning briefing.

TASK: Process todays voice/text entries -> classify -> create Todoist tasks -> save thoughts -> generate evening HTML report.$([ "${HEALTH_ENABLED:-false}" = "true" ] && echo " Also call Oura MCP (get_stress, get_daily_sleep, get_readiness) to include health context in the report — correlate stress peaks with events from daily notes.")

$MCP_PROMPT" \
        2>&1) || true
else
    # ── Phase 2: EXECUTE ──
    # Uses mcp-cli (Bash) for Todoist — no --mcp-config needed
    echo "=== Phase 2: EXECUTE ==="
    EXECUTE=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 \
        -p "Today is $TODAY. Read .claude/skills/dbrain-processor/phases/execute.md and execute Phase 2.
Read .session/capture.json for input data.
Create tasks in Todoist via mcp-cli (Bash tool), save thoughts, build links. Return ONLY JSON.

CRITICAL: Use Bash tool to call mcp-cli for Todoist operations.
Example: mcp-cli call todoist find-tasks-by-date '{\"startDate\": \"today\"}'
mcp-cli may take 10-30 sec on first call (server startup). Retry 3x on error." \
        2>&1) || true

    # Save raw output for debugging
    echo "$EXECUTE" > "$SESSION_DIR/execute.raw.log"

    echo "$EXECUTE" | python3 "$SCRIPTS_DIR/extract_json.py" > "$EXECUTE_FILE" 2>/dev/null \
        || echo '{"error": "execute failed"}' > "$EXECUTE_FILE"

    echo "Execute saved: $(wc -c < "$EXECUTE_FILE") bytes"

    # ── Phase 3: REFLECT ──
    # -- Pre-REFLECT: Fetch Oura full-day health context --
    OURA_CONTEXT=""
    if [ "${HEALTH_ENABLED:-false}" = "true" ]; then
        echo "=== Fetching Oura health context ==="
        OURA_PROMPT="Today is $TODAY. Call ALL of the following Oura MCP tools and return a compact plain text summary in Russian (no HTML, no markdown):
1. get_daily_sleep — ночной сон: score, duration, efficiency, deep/REM/light breakdown
2. get_readiness — readiness score + key contributors (HRV balance, recovery index, body temperature)
3. get_stress — stress level timeline over the day, peak stress moments
4. get_heart_rate — resting HR, any notable spikes or drops
5. get_daily_activity — шаги, active calories, distance, movement goal progress
Format: one line per metric category, values in brackets. Example: Сон: [score=78] 7ч 20мин, эффективность 88%, глубокий 1ч 10мин. Keep each line concise."
        OURA_CONTEXT=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 --mcp-config "$PROJECT_DIR/mcp-config.json" -p "$OURA_PROMPT" 2>&1) || OURA_CONTEXT=""
        # Strip any preamble Claude might add
        OURA_CONTEXT=$(echo "$OURA_CONTEXT" | grep -v '^$' | grep -v 'Here is\|Вот\|Let me\|I will\|Calling' | head -20 || echo "$OURA_CONTEXT")
    fi

    # -- Pre-REFLECT: Fetch nutrition context from Supabase --
    NUTRITION_CONTEXT=""
    if [ "${NUTRITION_ENABLED:-true}" = "true" ] && [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_KEY:-}" ]; then
        echo "=== Fetching nutrition context ==="
        NUTRITION_CONTEXT=$(uv run python "$SCRIPTS_DIR/nutrition_context.py" 2>/dev/null || echo "")
    fi

    HEALTH_SECTION=""
    if [ -n "$OURA_CONTEXT" ]; then
        HEALTH_SECTION="
OURA HEALTH DATA (полный день):
${OURA_CONTEXT}

Add a health section to the HTML report (after tasks section):
<b>🫀 Здоровье за день:</b>
Структура: 2-3 строки.
Строка 1: сон — одним предложением, только если что-то важное (плохой сон, рекорд, необычное).
Строка 2: активность + стресс — корреляция с событиями дня из capture.json. Когда был пик стресса и что происходило в это время?
Строка 3 (если нужна): один практичный вывод для завтра на основе данных.
Не перечисляй цифры — давай ВЫВОДЫ."
    fi

    NUTRITION_SECTION=""
    if [ -n "$NUTRITION_CONTEXT" ]; then
        NUTRITION_SECTION="
ПИТАНИЕ ЗА ДЕНЬ (данные из трекера):
${NUTRITION_CONTEXT}

Add a nutrition section to the HTML report (after health section):
<b>🍽 Питание за день:</b>
Итог: X/Y ккал (Z%). Белки/жиры/углеводы — выполнено или нет.
Один вывод — что хорошо, что изменить завтра.
Связь с здоровьем: если есть Oura данные — связать питание с энергией/стрессом сегодня."
    fi

    echo "=== Phase 3: REFLECT ==="
    REPORT=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 \
        -p "Today is $TODAY. Read .claude/skills/dbrain-processor/phases/reflect.md and execute Phase 3.
Read .session/capture.json and .session/execute.json for input data.
Read MEMORY.md, .session/handoff.md.
Generate HTML report, update MEMORY, record observations.

SYSTEM LOGS (last 24h, may be empty):
${LOG_ERRORS}

AGENT NOTES TASK: Scan the input text for:
1. User complaints about the bot (not found, failed, broken, did not save) - add to vault/agent/agent_notes.md section "Проблемы из рефлексии" with id: r-{DATE}-NNN
2. Ideas for new automations (would be convenient, want a command, should automate) - add to "Идеи агента" with id: a-{DATE}-NNN
3. If LOG_ERRORS is not empty - add brief error summary to "Системные ошибки" with id: e-{DATE}-NNN (skip if already logged today)
4. If you notice friction patterns or repetitive actions - add 1-2 ideas to "Идеи агента"

Format for each agent_notes.md entry:
- \`[ ]\` **[source]** description <!-- id: X-YYYYMMDD-NNN -->

FORMATTING RULES (mandatory for Telegram report):
- Tasks: ONLY name + priority + due date. NEVER include task ID (like abc123xyz).
- Thoughts: read each saved file H1 heading, show title in RUSSIAN. NO [[wikilink]] syntax.
- New links: plain note names without [[ ]] brackets.
${HEALTH_SECTION}
${NUTRITION_SECTION}

Return ONLY RAW HTML (for Telegram)." \
        2>&1) || true
fi

cd "$PROJECT_DIR"

echo "=== Claude output ==="
echo "$REPORT"
echo "===================="

REPORT_CLEAN=$(clean_claude_output "$REPORT")

# Rebuild vault graph
echo "=== Rebuilding vault graph ==="
cd "$VAULT_DIR"
uv run .claude/skills/graph-builder/scripts/analyze.py || echo "Graph rebuild failed (non-critical)"

# Memory decay (update relevance scores and tiers)
echo "=== Memory decay ==="
uv run .claude/skills/agent-memory/scripts/memory-engine.py decay . || echo "Memory decay failed (non-critical)"

# Regenerate MOC indexes (reflections, ideas, learnings, business, projects)
echo "=== Regenerating MOC ==="
uv run .claude/skills/vault-health/scripts/generate_moc.py || echo "MOC generation failed (non-critical)"
cd "$PROJECT_DIR"

# Git commit (only vault/)
git add vault/
git commit -m "chore: process daily $TODAY" || true
git push || true

# Send to Telegram
send_telegram "$REPORT_CLEAN"

echo "=== Done ==="
