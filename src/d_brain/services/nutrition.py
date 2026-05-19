"""Nutrition sub-agent: Claude SDK + Supabase for food logging and analysis."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import anthropic
import httpx

logger = logging.getLogger(__name__)

_FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
_FATSECRET_NLP_URL = "https://platform.fatsecret.com/rest/natural/nutrients/v1"
_FATSECRET_IMAGE_URL = "https://platform.fatsecret.com/rest/image-recognition/v1"

_IDENTIFY_FOODS_PROMPT = """Ты распознаёшь еду на фото. Перечисли все блюда и продукты с оценкой порции.

Ответь ТОЛЬКО валидным JSON:
{
  "foods": [
    {"name_ru": "название на русском", "name_en": "english name", "portion": "количество с единицей (г, мл, шт, cup)"}
  ]
}

Правила:
- Будь конкретным: не "мясо", а "куриная грудка жареная"
- Для сложных блюд (суп, салат, паста) — укажи блюдо целиком, не разбивай
- Порцию оценивай реалистично по видимому размеру на тарелке/в посуде
- Если видно несколько блюд — каждое отдельным элементом"""

# ── КБЖУ targets — configured via NUTRITION_* env vars (see .env.example)
# Defaults: Mifflin-St Jeor 80kg/175cm/30y/male, PAL 1.4, deficit ~500 kcal
_DEFAULT_KCAL = 2000
_DEFAULT_PROTEIN = 150.0
_DEFAULT_FAT = 55.0
_DEFAULT_CARBS = 220.0


def _build_system_prompt(
    height_cm: int, weight_kg: float, age: int, gender: str,
    activity: str, goal: str, notes: str,
    kcal: int, protein: float, fat: float, carbs: float,
) -> str:
    return f"""Ты профессиональный нутрициолог-ассистент. Помогаешь конкретному пользователю вести учёт питания.

Профиль пользователя:
- Рост: {height_cm} см, текущий вес: {weight_kg} кг, возраст: {age} лет, {gender}
- Активность: {activity}
- Цель: {goal}
- Суточная норма: {kcal} ккал | Белки: {protein} г | Жиры: {fat} г | Углеводы: {carbs} г
- Ограничения/предпочтения: {notes or "не указаны"}

Твоя задача:
1. Проанализируй все переданные данные о еде (фото и/или текстовые описания)
2. Оцени КБЖУ максимально точно; если порция не указана — дай реалистичную среднюю оценку
3. Определи тип приёма пищи по содержимому
4. Дай короткий профессиональный комментарий об этом приёме пищи
5. Предложи 1-2 конкретных улучшения на следующий раз

Правила:
- Пиши конкретно, без воды
- Учитывай цель пользователя в каждой рекомендации
- При ресторанном блюде или неизвестной порции — давай оценку и кратко поясняй
- Пиши на русском языке
- Отвечай ТОЛЬКО валидным JSON без какого-либо другого текста

Формат ответа (строго JSON):
{{
  "meal_type": "завтрак" | "обед" | "ужин" | "перекус",
  "description": "краткое описание съеденного (1-2 предложения)",
  "calories": <целое число>,
  "protein": <дробное, 1 знак>,
  "fat": <дробное, 1 знак>,
  "carbs": <дробное, 1 знак>,
  "fiber": <дробное, 1 знак>,
  "comment": "профессиональная оценка приёма пищи (2-3 предложения)",
  "recommendation": "1-2 конкретных совета на следующий раз"
}}"""


@dataclass
class MealAnalysis:
    meal_type: str
    description: str
    calories: int
    protein: float
    fat: float
    carbs: float
    fiber: float
    comment: str
    recommendation: str
    meal_id: str = ""


class NutritionService:
    """Nutrition analysis via Anthropic SDK + data persistence via Supabase."""

    def __init__(
        self,
        anthropic_api_key: str,
        supabase_url: str,
        supabase_key: str,
        height_cm: int = 175,
        weight_kg: float = 80.0,
        age: int = 30,
        gender: str = "мужчина",
        activity: str = "умеренная активность",
        goal: str = "поддерживать вес",
        notes: str = "",
        daily_kcal: int = _DEFAULT_KCAL,
        daily_protein: float = _DEFAULT_PROTEIN,
        daily_fat: float = _DEFAULT_FAT,
        daily_carbs: float = _DEFAULT_CARBS,
        fatsecret_client_id: str = "",
        fatsecret_client_secret: str = "",
    ) -> None:
        self._claude = anthropic.AsyncAnthropic(api_key=anthropic_api_key or None)
        self._supabase_url = supabase_url
        self._supabase_key = supabase_key
        self._fatsecret_client_id = fatsecret_client_id
        self._fatsecret_client_secret = fatsecret_client_secret
        self._fatsecret_token: str = ""
        self._fatsecret_token_expires: float = 0.0
        self._daily_kcal = daily_kcal
        self._daily_protein = daily_protein
        self._daily_fat = daily_fat
        self._daily_carbs = daily_carbs
        self._system_prompt = _build_system_prompt(
            height_cm, weight_kg, age, gender,
            activity, goal, notes,
            daily_kcal, daily_protein, daily_fat, daily_carbs,
        )
        self._db: Any = None  # lazy init

    def _get_db(self) -> Any:
        if self._db is None:
            from supabase import create_client
            self._db = create_client(self._supabase_url, self._supabase_key)
        return self._db

    # ─────────────────────────── public API ───────────────────────────

    async def analyze_meal(
        self,
        user_id: int,
        photo_bytes_list: list[bytes],
        texts: list[str],
        oura_steps: int = 0,
        oura_active_calories: int = 0,
    ) -> MealAnalysis:
        """Analyze a meal from photos and/or text, save to Supabase, return analysis."""
        analysis = await self._call_claude(photo_bytes_list, texts, oura_steps)
        meal_id = await asyncio.to_thread(
            self._save_meal, user_id, analysis, texts, oura_steps, oura_active_calories
        )
        analysis.meal_id = meal_id
        await asyncio.to_thread(self._update_daily_summary, user_id, date.today())
        return analysis

    async def log_weight(self, user_id: int, weight_kg: float, note: str = "") -> None:
        """Log a body weight measurement."""
        await asyncio.to_thread(self._insert_weight, user_id, weight_kg, note)

    async def get_today_progress(self, user_id: int) -> dict[str, Any]:
        """Return today's totals vs goals."""
        return await asyncio.to_thread(self._fetch_today_progress, user_id)

    async def get_weekly_data(self, user_id: int, days: int = 7) -> list[dict[str, Any]]:
        """Return last N days of daily_summary rows."""
        return await asyncio.to_thread(self._fetch_weekly, user_id, days)

    async def get_recent_meals(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        """Return the last N meal records."""
        return await asyncio.to_thread(self._fetch_recent_meals, user_id, limit)

    async def get_nutrition_offenders(self, user_id: int, days: int = 7) -> list[dict[str, Any]]:
        """Return products that appeared ≥2 times as calorie offenders in the last N days."""
        return await asyncio.to_thread(self._fetch_calorie_offenders, user_id, days)

    def format_offenders_block(self, offenders: list[dict[str, Any]]) -> str:
        """Format a report block for repeated calorie offenders."""
        if not offenders:
            return ""
        lines = []
        for item in offenders:
            lines.append(f"⚠️ Паттерн: {item['product']} {item['count']} раз за 7 дней {item['trend']}")
        return "\n".join(lines)

    async def ensure_tables(self) -> None:
        """Create Supabase tables if they don't exist yet (idempotent)."""
        await asyncio.to_thread(self._create_tables)

    # ─────────────────────────── Claude call ───────────────────────────

    async def _call_claude(
        self,
        photo_bytes_list: list[bytes],
        texts: list[str],
        oura_steps: int,
    ) -> MealAnalysis:
        if self._fatsecret_client_id and self._fatsecret_client_secret:
            try:
                if photo_bytes_list:
                    return await self._call_with_fatsecret_image(photo_bytes_list, texts, oura_steps)
                else:
                    return await self._call_with_fatsecret_nlp(texts, oura_steps)
            except Exception as exc:
                logger.warning("FatSecret pipeline failed (%s), falling back to direct Claude", exc)
        return await self._call_claude_direct(photo_bytes_list, texts, oura_steps)

    async def _identify_foods_claude(self, photo_bytes_list: list[bytes], texts: list[str]) -> list[dict[str, str]]:
        """Ask Claude to identify food items and portions (no КБЖУ calculation)."""
        content: list[Any] = []
        for photo_bytes in photo_bytes_list:
            b64 = base64.standard_b64encode(photo_bytes).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        user_hint = ""
        if texts:
            user_hint = "\n\nПользователь добавил: " + " ".join(texts)
        content.append({"type": "text", "text": _IDENTIFY_FOODS_PROMPT + user_hint})

        response = await self._claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return data.get("foods", [])

    async def _get_fatsecret_token(self) -> str:
        """Get OAuth2 Bearer token from FatSecret, caching until expiry."""
        if self._fatsecret_token and time.monotonic() < self._fatsecret_token_expires:
            return self._fatsecret_token
        credentials = base64.standard_b64encode(
            f"{self._fatsecret_client_id}:{self._fatsecret_client_secret}".encode()
        ).decode()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _FATSECRET_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content="grant_type=client_credentials&scope=premier",
            )
            resp.raise_for_status()
            data = resp.json()
        self._fatsecret_token = data["access_token"]
        self._fatsecret_token_expires = time.monotonic() + data.get("expires_in", 86400) - 60
        return self._fatsecret_token

    async def _call_with_fatsecret_image(
        self,
        photo_bytes_list: list[bytes],
        texts: list[str],
        oura_steps: int,
    ) -> MealAnalysis:
        """Photo pipeline: FatSecret Image Recognition → КБЖУ → Claude commentary."""
        token = await self._get_fatsecret_token()
        photo_b64 = base64.standard_b64encode(photo_bytes_list[0]).decode()
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                _FATSECRET_IMAGE_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"image_b64": photo_b64},
            )
            resp.raise_for_status()
            fs_data = resp.json()

        food_response = fs_data.get("food_response", [])
        if isinstance(food_response, dict):
            food_response = [food_response]
        if not food_response:
            return await self._call_claude_direct(photo_bytes_list, texts, oura_steps)

        lines = []
        total_kcal = total_protein = total_fat = total_carbs = 0.0
        for item in food_response:
            name = item.get("name", "?")
            eaten = item.get("eaten", {})
            kcal = float(eaten.get("calories", 0))
            protein = float(eaten.get("protein", 0))
            fat = float(eaten.get("fat", 0))
            carbs = float(eaten.get("carbohydrate", 0))
            serving = eaten.get("standard_serving_size", "")
            lines.append(f"- {name} ({serving}): {kcal:.0f} ккал, Б {protein:.1f}г, Ж {fat:.1f}г, У {carbs:.1f}г")
            total_kcal += kcal
            total_protein += protein
            total_fat += fat
            total_carbs += carbs

        nutrition_block = "\n".join(lines)
        nutrition_block += (
            f"\n\nИтого из FatSecret Image Recognition: {total_kcal:.0f} ккал | "
            f"Б {total_protein:.1f}г | Ж {total_fat:.1f}г | У {total_carbs:.1f}г"
        )
        return await self._call_claude_validate(photo_bytes_list, texts, oura_steps, nutrition_block)

    async def _call_with_fatsecret_nlp(
        self,
        texts: list[str],
        oura_steps: int,
    ) -> MealAnalysis:
        """Text pipeline: Claude identifies foods (EN) → FatSecret NLP → Claude commentary."""
        foods = await self._identify_foods_claude([], texts)
        if not foods:
            return await self._call_claude_direct([], texts, oura_steps)

        query = ", ".join(f"{item['portion']} {item['name_en']}" for item in foods)
        token = await self._get_fatsecret_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _FATSECRET_NLP_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"user_input": query},
            )
            resp.raise_for_status()
            fs_data = resp.json()

        foods_data = fs_data.get("foods", {})
        food_list = foods_data.get("food", [])
        if isinstance(food_list, dict):
            food_list = [food_list]
        if not food_list:
            return await self._call_claude_direct([], texts, oura_steps)

        lines = []
        total_kcal = total_protein = total_fat = total_carbs = 0.0
        for item in food_list:
            kcal = float(item.get("calories", 0))
            protein = float(item.get("protein", 0))
            fat = float(item.get("fat", 0))
            carbs = float(item.get("carbohydrate", 0))
            serving = item.get("serving_description", item.get("metric_serving_amount", ""))
            name = item.get("food_name", "?")
            lines.append(f"- {name} ({serving}): {kcal:.0f} ккал, Б {protein:.1f}г, Ж {fat:.1f}г, У {carbs:.1f}г")
            total_kcal += kcal
            total_protein += protein
            total_fat += fat
            total_carbs += carbs

        totals = foods_data.get("total_nutritional_contents", {})
        if totals:
            total_kcal = float(totals.get("calories", total_kcal))
            total_protein = float(totals.get("protein", total_protein))
            total_fat = float(totals.get("fat", total_fat))
            total_carbs = float(totals.get("carbohydrate", total_carbs))

        nutrition_block = "\n".join(lines)
        nutrition_block += (
            f"\n\nИтого из FatSecret NLP: {total_kcal:.0f} ккал | "
            f"Б {total_protein:.1f}г | Ж {total_fat:.1f}г | У {total_carbs:.1f}г"
        )
        return await self._call_claude_validate([], texts, oura_steps, nutrition_block)

    async def _call_claude_validate(
        self,
        photo_bytes_list: list[bytes],
        texts: list[str],
        oura_steps: int,
        nutrition_block: str,
    ) -> MealAnalysis:
        """Final Claude call: validate FatSecret data + add commentary."""
        content: list[Any] = []
        for photo_bytes in photo_bytes_list:
            b64 = base64.standard_b64encode(photo_bytes).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })

        text_parts = [
            f"Данные о питательности из базы FatSecret:\n{nutrition_block}",
            "Сверь эти данные с фото/описанием (скорректируй порции при необходимости, отклонение ±20% допустимо). "
            "Ответь JSON с итоговыми КБЖУ и своим комментарием.",
        ]
        if texts:
            text_parts.insert(1, "Описание от пользователя:\n" + "\n".join(texts))
        if oura_steps:
            text_parts.append(f"Данные активности сегодня: {oura_steps} шагов.")
        content.append({"type": "text", "text": "\n\n".join(text_parts)})

        response = await self._claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=self._system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        return self._parse_meal_analysis(response.content[0].text)

    async def _call_claude_direct(
        self,
        photo_bytes_list: list[bytes],
        texts: list[str],
        oura_steps: int,
    ) -> MealAnalysis:
        """Fallback: single-pass Claude analysis without Nutritionix."""
        content: list[Any] = []

        for photo_bytes in photo_bytes_list:
            b64 = base64.standard_b64encode(photo_bytes).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })

        text_parts = []
        if texts:
            text_parts.append("Описание от пользователя:\n" + "\n".join(texts))
        if oura_steps:
            text_parts.append(f"Данные активности сегодня: {oura_steps} шагов.")
        text_parts.append("Проанализируй этот приём пищи и ответь JSON.")
        content.append({"type": "text", "text": "\n\n".join(text_parts)})

        response = await self._claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=self._system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        return self._parse_meal_analysis(response.content[0].text)

    @staticmethod
    def _parse_meal_analysis(raw: str) -> MealAnalysis:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        if isinstance(data, list):
            data = data[0]
        return MealAnalysis(
            meal_type=data.get("meal_type", "перекус"),
            description=data.get("description", ""),
            calories=int(data.get("calories", 0)),
            protein=float(data.get("protein", 0)),
            fat=float(data.get("fat", 0)),
            carbs=float(data.get("carbs", 0)),
            fiber=float(data.get("fiber", 0)),
            comment=data.get("comment", ""),
            recommendation=data.get("recommendation", ""),
        )

    # ─────────────────────────── Supabase writes ───────────────────────────

    def _save_meal(
        self,
        user_id: int,
        analysis: MealAnalysis,
        raw_texts: list[str],
        oura_steps: int,
        oura_active_calories: int,
    ) -> str:
        db = self._get_db()
        row = {
            "user_id": user_id,
            "logged_at": datetime.utcnow().isoformat(),
            "meal_type": analysis.meal_type,
            "description": analysis.description,
            "calories": analysis.calories,
            "protein": analysis.protein,
            "fat": analysis.fat,
            "carbs": analysis.carbs,
            "fiber": analysis.fiber,
            "nutritionist_comment": analysis.comment,
            "recommendation": analysis.recommendation,
            "raw_input": "\n".join(raw_texts) if raw_texts else "",
            "oura_steps_day": oura_steps,
            "oura_active_calories_day": oura_active_calories,
        }
        result = db.table("meals").insert(row).execute()
        rows = result.data
        return str(rows[0]["id"]) if rows else ""

    def _update_daily_summary(self, user_id: int, today: date) -> None:
        db = self._get_db()
        result = (
            db.table("meals")
            .select("calories,protein,fat,carbs,fiber")
            .eq("user_id", user_id)
            .gte("logged_at", today.isoformat())
            .lt("logged_at", f"{today.year}-{today.month:02d}-{today.day + 1:02d}")
            .execute()
        )
        meals = result.data or []
        totals = {
            "total_calories": sum(m.get("calories", 0) for m in meals),
            "total_protein": round(sum(m.get("protein", 0) for m in meals), 1),
            "total_fat": round(sum(m.get("fat", 0) for m in meals), 1),
            "total_carbs": round(sum(m.get("carbs", 0) for m in meals), 1),
            "meal_count": len(meals),
            "goal_calories": self._daily_kcal,
            "goal_protein": self._daily_protein,
            "goal_fat": self._daily_fat,
            "goal_carbs": self._daily_carbs,
        }
        db.table("daily_summary").upsert(
            {"user_id": user_id, "date": today.isoformat(), **totals},
            on_conflict="user_id,date",
        ).execute()

    def _insert_weight(self, user_id: int, weight_kg: float, note: str) -> None:
        db = self._get_db()
        db.table("weight_log").insert({
            "user_id": user_id,
            "logged_at": datetime.utcnow().isoformat(),
            "weight_kg": weight_kg,
            "note": note,
        }).execute()

    # ─────────────────────────── Supabase reads ───────────────────────────

    def _fetch_today_progress(self, user_id: int) -> dict[str, Any]:
        db = self._get_db()
        today = date.today().isoformat()
        result = (
            db.table("daily_summary")
            .select("*")
            .eq("user_id", user_id)
            .eq("date", today)
            .execute()
        )
        rows = result.data or []
        if rows:
            return rows[0]
        return {
            "total_calories": 0, "total_protein": 0, "total_fat": 0, "total_carbs": 0,
            "goal_calories": self._daily_kcal, "goal_protein": self._daily_protein,
            "goal_fat": self._daily_fat, "goal_carbs": self._daily_carbs, "meal_count": 0,
        }

    def _fetch_weekly(self, user_id: int, days: int) -> list[dict[str, Any]]:
        from datetime import timedelta
        db = self._get_db()
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        result = (
            db.table("daily_summary")
            .select("date,total_calories,total_protein,total_fat,total_carbs,goal_calories,meal_count")
            .eq("user_id", user_id)
            .gte("date", since)
            .order("date")
            .execute()
        )
        return result.data or []

    def _fetch_calorie_offenders(self, user_id: int, days: int) -> list[dict[str, Any]]:
        from collections import Counter
        from datetime import timedelta
        db = self._get_db()
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        per_meal_threshold = self._daily_kcal // 3
        result = (
            db.table("meals")
            .select("logged_at,description,calories")
            .eq("user_id", user_id)
            .gte("logged_at", since)
            .order("logged_at")
            .execute()
        )
        meals = result.data or []
        offender_meals = [m for m in meals if (m.get("calories") or 0) > per_meal_threshold]
        counts: Counter = Counter(
            m.get("description", "").lower().strip() for m in offender_meals
        )
        mid_str = (date.today() - timedelta(days=days // 2)).isoformat()
        first_half = [m for m in offender_meals if (m.get("logged_at") or "") < mid_str]
        second_half = [m for m in offender_meals if (m.get("logged_at") or "") >= mid_str]
        first_counts: Counter = Counter(
            m.get("description", "").lower().strip() for m in first_half
        )
        second_counts: Counter = Counter(
            m.get("description", "").lower().strip() for m in second_half
        )
        result_list = []
        for product, count in counts.items():
            if count >= 2 and product:
                f = first_counts.get(product, 0)
                s = second_counts.get(product, 0)
                trend = "↑" if s > f else ("↓" if s < f else "→")
                result_list.append({"product": product, "count": count, "trend": trend})
        result_list.sort(key=lambda x: x["count"], reverse=True)
        return result_list

    def _fetch_recent_meals(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        db = self._get_db()
        result = (
            db.table("meals")
            .select("logged_at,meal_type,description,calories,protein,fat,carbs,nutritionist_comment,recommendation")
            .eq("user_id", user_id)
            .order("logged_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ─────────────────────────── table setup ───────────────────────────

    def _create_tables(self) -> None:
        """Create tables via Supabase SQL RPC (runs once on first start)."""
        db = self._get_db()
        sql = """
        CREATE TABLE IF NOT EXISTS meals (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            user_id BIGINT NOT NULL,
            logged_at TIMESTAMPTZ DEFAULT now(),
            meal_type TEXT, description TEXT,
            calories INTEGER DEFAULT 0, protein NUMERIC(6,1) DEFAULT 0,
            fat NUMERIC(6,1) DEFAULT 0, carbs NUMERIC(6,1) DEFAULT 0,
            fiber NUMERIC(6,1) DEFAULT 0, nutritionist_comment TEXT,
            recommendation TEXT, raw_input TEXT,
            oura_steps_day INTEGER DEFAULT 0, oura_active_calories_day INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS daily_summary (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            user_id BIGINT NOT NULL, date DATE NOT NULL,
            total_calories INTEGER DEFAULT 0, total_protein NUMERIC(6,1) DEFAULT 0,
            total_fat NUMERIC(6,1) DEFAULT 0, total_carbs NUMERIC(6,1) DEFAULT 0,
            goal_calories INTEGER DEFAULT 2000, goal_protein NUMERIC(6,1) DEFAULT 150,
            goal_fat NUMERIC(6,1) DEFAULT 55, goal_carbs NUMERIC(6,1) DEFAULT 220,
            meal_count INTEGER DEFAULT 0, nutritionist_daily_comment TEXT,
            UNIQUE(user_id, date)
        );
        CREATE TABLE IF NOT EXISTS weight_log (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            user_id BIGINT NOT NULL, logged_at TIMESTAMPTZ DEFAULT now(),
            weight_kg NUMERIC(5,1) NOT NULL, note TEXT DEFAULT ''
        );
        """
        try:
            db.rpc("exec_sql", {"sql": sql}).execute()
        except Exception:
            logger.warning("Could not auto-create tables via RPC. Create them manually.")


def get_nutrition_service() -> NutritionService:
    """Return a NutritionService instance using settings from env."""
    from d_brain.config import get_settings
    s = get_settings()
    return NutritionService(
        anthropic_api_key=s.anthropic_api_key,
        supabase_url=s.supabase_url,
        supabase_key=s.supabase_key,
        height_cm=s.nutrition_height_cm,
        weight_kg=s.nutrition_weight_kg,
        age=s.nutrition_age,
        gender=s.nutrition_gender,
        activity=s.nutrition_activity,
        goal=s.nutrition_goal,
        notes=s.nutrition_notes,
        daily_kcal=s.nutrition_daily_kcal,
        daily_protein=s.nutrition_daily_protein,
        daily_fat=s.nutrition_daily_fat,
        daily_carbs=s.nutrition_daily_carbs,
        fatsecret_client_id=s.fatsecret_client_id,
        fatsecret_client_secret=s.fatsecret_client_secret,
    )
