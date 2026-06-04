---
type: note
title: Phase 2: EXECUTE
last_accessed: 2026-05-17
relevance: 0.73
tier: warm
---
# Phase 2: EXECUTE

Read capture.json from Phase 1. Save tasks locally to vault, save thoughts, build links.

## Input
- `.session/capture.json` — output from Phase 1

## Task Storage

**Задачи хранятся в vault/tasks/, НЕ в Todoist.**
Файл: `tasks/YYYY-MM-DD.md` (создать если не существует)

Формат строки задачи:
```
- [ ] Содержание задачи <!-- p:{priority} due:{due} goal:{goal_alignment} -->
```

Priorities: 1=критично, 2=высокий, 3=средний, 4=низкий

## Task

### 1. Save tasks to vault

For each entry with `classification: "task"`, append to `tasks/{DATE}.md`.

If file doesn't exist, create with header `# Tasks — {DATE}`.

Append:
```markdown
- [ ] {task_content} <!-- p:{task_priority} due:{task_due} goal:{goal_alignment} -->
```

Record saved tasks in output JSON as `tasks_created` (with `path` field).

### 2. Handle patterns from capture.json

For each pattern with `suggested_tasks`, append to `tasks/{DATE}.md`:
```markdown
- [ ] {suggested_task_content} <!-- p:{priority} due:{due} pattern:{pattern_type} -->
```

### 3. OBT escalation (N≥2)

1. Read `one_big_thing` from `.session/capture.json`
2. Scan `tasks/{DATE}.md` and `tasks/{YESTERDAY}.md` for tasks matching OBT keywords
3. Count N = consecutive days without OBT-linked task
4. If N ≥ 2 — append to `tasks/{DATE}.md`:
```markdown
- [ ] Слот для OBT: {one_big_thing} <!-- p:2 due:tomorrow obt:escalated -->
```

Record under `obt_slot_task` in output JSON.

### 4. Save thoughts

For each entry with classification idea/reflection/learning/project:
- Create file in `thoughts/{category}/YYYY-MM-DD-slug.md`
- Include frontmatter with description field (retrieval filter, ~150 chars)
- Add wiki-links to related entities
- Add typed relationships in Related section:
  ```markdown
  ## Related
  - [[thoughts/ideas/some-note|Title]] — context: discussed during processing
  ```

### 5. Build links

For all created/updated files:
- Search for related notes in vault
- Add wiki-links with context phrases

### 6. Check open tasks workload

Read `tasks/` files for last 7 days, count open `- [ ]` items per day.

## Output Format

Print ONLY valid JSON:

```json
{
  "tasks_created": [
    {"path": "tasks/2026-05-17.md", "content": "Follow-up task", "priority": 2, "due": "tomorrow"}
  ],
  "thoughts_saved": [
    {"path": "thoughts/ideas/2026-05-17-layered-memory.md", "title": "AI agents need layered memory", "category": "ideas"}
  ],
  "links_created": [
    {"from": "thoughts/ideas/2026-05-17-layered-memory.md", "to": "goals/3-weekly.md", "context": "supports weekly focus"}
  ],
  "workload": {
    "open_tasks_7d": 12,
    "by_day": {"2026-05-17": 3, "2026-05-16": 5}
  },
  "observations": [],
  "pattern_tasks_created": [
    {"pattern": "doc-heavy", "content": "Follow-up: ...", "path": "tasks/2026-05-17.md"}
  ],
  "obt_slot_task": {"path": "tasks/2026-05-17.md", "content": "Слот для OBT: ...", "due": "tomorrow"}
}
```
