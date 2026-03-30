#!/usr/bin/env python3
"""Fetch today's nutrition summary from Supabase.

Used by health.sh and process.sh to inject food context into Claude prompts.
Outputs plain text (no HTML/markdown) for embedding in prompts.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def main() -> None:
    from d_brain.config import get_settings
    from d_brain.services.nutrition import get_nutrition_service

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        return

    user_id = settings.allowed_user_ids[0] if settings.allowed_user_ids else 0

    svc = get_nutrition_service()
    progress = await svc.get_today_progress(user_id)
    meals = await svc.get_recent_meals(user_id, limit=8)

    kcal = progress.get("total_calories", 0)
    goal = progress.get("goal_calories", 2000)
    prot = progress.get("total_protein", 0)
    fat  = progress.get("total_fat", 0)
    carb = progress.get("total_carbs", 0)
    cnt  = progress.get("meal_count", 0)
    left = max(0, goal - kcal)
    pct  = round(kcal / goal * 100) if goal else 0

    lines = [
        f"Питание за сегодня ({cnt} приёмов пищи):",
        f"  Калории: {kcal}/{goal} ккал ({pct}% нормы, осталось {left} ккал)",
        f"  КБЖУ: Белки {prot}г / Жиры {fat}г / Углеводы {carb}г",
    ]

    if meals:
        lines.append("  Приёмы пищи:")
        for m in meals:
            ts = (m.get("logged_at") or "")[:16].replace("T", " ")
            mtype = m.get("meal_type", "")
            desc  = (m.get("description") or "")[:70]
            kcal_m = m.get("calories", 0)
            lines.append(f"    {ts} [{mtype}] {desc} — {kcal_m} ккал")
    else:
        lines.append("  Приёмов пищи сегодня нет.")

    print("\n".join(lines))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"nutrition context unavailable: {e}", file=sys.stderr)
