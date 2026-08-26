---
type: note
title: Phase 3: REFLECT
last_accessed: 2026-03-02
relevance: 0.1
tier: archive
---
# Phase 3: REFLECT

Read execute results. Generate HTML report. Update MEMORY. Write observations. Log to daily.

## Input
- `.session/capture.json` — from Phase 1
- `.session/execute.json` — from Phase 2
- `MEMORY.md` — long-term memory
- `.session/handoff.md` — session context
- `.graph/health-history.json` — vault health trend (if exists)

## Task

### 1. Generate HTML report

Use the template from SKILL.md. Include:

- ONE Big Thing (from capture.json)
- Thoughts saved (from execute.json)
- Tasks created (list from execute.json, show content + due, NO external IDs)
- Open tasks count from vault/tasks/ (last 7 days)
- Workload by day
- Vault Health score (from latest health-history.json entry, if exists)
- Top 3 priorities
- Observations (if any)
- Pattern alerts (if any patterns detected in capture.json)
- OBT-alert (if ONE Big Thing not covered N days in a row)

### 2. Log actions to daily

Append to `daily/{DATE}.md`:

```markdown
## HH:MM [text]
d-brain processing

**Tasks created:** N
- "Task content" (p:{priority}, due:{due})

**Thoughts saved:** M
- [[path/to/thought|Title]] — category

**Links created:** K
- [[from]] ↔ [[to]]
```

### 3. Evolve MEMORY.md

Check if any information from today deserves long-term memory:
- New key decisions
- Changes in Active Context
- New patterns/insights

Rules:
- New info REPLACES outdated (don't append duplicates)
- Only write significant changes

### 4. Capture observations

If problems occurred during processing, append to `.session/handoff.md` under `## Observations`:

```markdown
- [friction] 2026-03-02: mcp timeout on todoist — retried 3x
- [pattern] 2026-03-02: daily had only 2 entries — low activity day
```

### 5. Update handoff.md

Update session context:
- Last Session: what was processed
- Key Decisions: if any
- In Progress: incomplete items

## Output Format

Return RAW HTML report (no markdown, no code blocks). Goes directly to Telegram.

Follow the HTML template exactly:
- Only use: `<b>`, `<i>`, `<code>`, `<s>`, `<u>`, `<a>`
- NO: `<div>`, `<br>`, `<table>`, markdown syntax
- Max 4096 characters

### Pattern alerts section (add to report if patterns detected):

```html
<b>🔍 Авто-детекция:</b>
• [doc-heavy] 2 транскрипции встреч → создано 2 follow-up задачи
• [stale-weekly-goal] 3-weekly.md не менялась 5 дней → добавлена задача обновить цели
• [life-decision] ✅ Жизненное решение: [решение] — закрывает [[goal]]
```

### Vault Health section (add to report if data exists):

```html
<b>📊 Vault Health:</b> {score}/100
Orphans: {N} | Broken: {M} | Avg links: {X}
```


### OBT-alert section (add to report if OBT not covered):

**Logic:**
1. Read `one_big_thing` from `.session/capture.json`
2. Collect evidence for each day (today + look back) from TWO sources:
   a. **tasks/{DATE}.md** — all task lines (open or completed)
   b. **daily/{DATE}.md** — all non-url entries; for today use the `entries` array from capture.json (skip entries with `classification: "skip"` or `type: "url"`); for prior days read the file directly, skipping `[url]` lines
3. **Semantic assessment** — read the collected evidence alongside the `one_big_thing` formulation and judge by meaning: does the text demonstrate actual progress on this OBT? Apply the following interpretation rules:
   - Latin/Cyrillic spelling variants are the same entity: XSell = Х-селл = Хселл; Todoist = Тудуист; etc.
   - Synonyms and verbal forms count: «провожу 1-1-ы» = «встречи со стейкхолдерами»; «дипдайв» = «собираю информацию по домену»; «доку» = «зафиксировать»
   - Oblique mention does NOT count: a passing reference to a person's name unrelated to the OBT action, or scheduling a meeting without conducting it, is not evidence of OBT progress
   - Genuine progress examples: conducting a 1-1 meeting, writing/updating a document related to OBT, completing an OBT-linked task, recording a reflection that describes OBT work done
4. A day is "OBT-covered" if the semantic assessment finds genuine progress in EITHER source
5. Count N = consecutive OBT-uncovered days ending today (today + look back through daily logs)
6. If N ≥ 1 — add alert to report; if N = 0 — omit the alert entirely

```html
<b>⚠️ OBT не отмечен {N}-й день подряд</b>
OBT: «{one_big_thing}»
→ Перенести слот? Предлагаю: <b>{suggested_day}</b>
```

Where `suggested_day` = closest upcoming weekday with low workload (from execute.json workload map).

**Важно:** движение по OBT в дневнике важнее формального тега в задаче. Если записи дня семантически описывают OBT-работу — день закрыт (N=0), алерт не выводится.

**Если `sick_day == true` в capture.json:** заменить блок ⚠️ overdue-алерта в разделе Process goals на:
```html
<i>overdue (день болезни — перенесено на следующий рабочий день)</i>
```

### No-work-records alert section (add to report if N≥3 consecutive days without work entries):

**Logic:**
1. Read `categories` from `.session/capture.json` for today
2. Read `categories` from daily files for the previous 2 days (`daily/YYYY-MM-DD.md`) — check if capture.json was cached, otherwise infer from daily entry types
3. A day counts as "non-work" if all its entries fall into non-work categories (e.g. `news`, `personal`, `health`, `entertainment`) — i.e. no entries with work categories (`work`, `task`, `idea`, `meeting`, `project`)
4. Count N = number of consecutive non-work days ending today (today + look back)
5. If N ≥ 3 — add alert to report:

```html
<b>🔔 {N} дней без рабочих записей</b>
→ Предлагаю обновить weekly goal и добавить задачи
```

### Process goals escalation section

**Logic:**
1. Read `process_goals` count from `.session/execute.json` (tasks created with goal type = process)
2. Read `overdue` count from `.session/execute.json` or `stats` field in `.session/capture.json`
3. If `overdue > 0` — show task IDs inline and add "Перенести" suggestion:

```html
<b>🎯 Process goals:</b> {N} выполнено
⚠️ Просроченные: {overdue} — <code>{id1}, {id2}</code>
→ <b>Перенести</b> на ближайший рабочий день?
```


### 6. Адаптация тона отчёта по типу дня

**Правило 1 — Выходной день (суббота или воскресенье):**
Определи день недели по дате из `.session/capture.json` (поле `date`).
Если день — суббота (`weekday == 5`) или воскресенье (`weekday == 6`):
- Убрать из отчёта task-секции: «Tasks created», «Open tasks», «Process goals», «OBT-alert»
- Добавить в конец отчёта блок восстановления:

```html
<b>🌿 Выходной день</b>
Хорошего отдыха! На этой неделе: <i>{краткий итог из capture.json}</i>
→ В понедельник: <b>{первый приоритет из goals/3-weekly.md}</b>
```

**Правило 2 — Воскресный отчёт с weekly review:**
Если день — воскресенье И в capture.json или daily есть записи с типом `weekly review` / `итоги недели` (ключевые слова: «итоги недели», «weekly review», «неделя завершена»):
- Определи номер недели N (ISO week number от даты)
- Добавить метку в заголовок отчёта: `W{N} ✅ завершена`

Пример заголовка:
```html
<b>📋 d-brain отчёт — {DATE} · W{N} ✅ завершена</b>
```

### 7. Update agent_notes.md

Scan all input for signals to improve the agent. Write to `vault/agent/agent_notes.md`.

Create the file with header if it doesn't exist:
```
# Agent Notes — идеи и проблемы для улучшения
```

For each finding, append to today's date section (create if needed: `## YYYY-MM-DD`):

**Раздел "⚠️ Проблемы из рефлексии"** — if user mentioned:
- Bot couldn't find something, failed to respond, gave wrong result
- Format: `- \`[ ]\` **[рефлексия]** description <!-- id: r-YYYYMMDD-NNN -->`

**Раздел "🔄 Идеи агента"** — if user mentioned:
- Would be convenient to automate, want a new command, should remember X
- Also: patterns you noticed (repetitive actions, friction points)
- Format: `- \`[ ]\` **[паттерн]** description <!-- id: a-YYYYMMDD-NNN -->`

**Раздел "🔴 Системные ошибки"** — if LOG_ERRORS is not empty:
- Add brief one-line error summary (only new errors, check if today's date already has entry)
- Format: `- \`[ ]\` **[лог]** error description <!-- id: e-YYYYMMDD-NNN -->`

Rules:
- Only add entries if there's real signal (don't add empty sections)
- NNN = 3-digit sequence within same date (001, 002, ...)
- If agent_notes.md doesn't have today's date section yet — create it

## CRITICAL

- Output is RAW HTML only
- No markdown syntax anywhere
- All HTML tags must be properly closed
