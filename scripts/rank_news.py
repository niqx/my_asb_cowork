#!/usr/bin/env python3
"""Output ranked news digest as HTML for Telegram morning briefing.

Reads morning-news.json, picks the best article per source (by score),
sorts sources by score descending, outputs a compact HTML Telegram message.
Exit without output if no data or all scores below MIN_SCORE.
"""
import json, os, sys
from pathlib import Path

_PROJECT = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent.parent))
_VAULT   = Path(os.environ.get("VAULT_DIR", _PROJECT / "vault"))
MORNING_NEWS = _VAULT / ".session" / "morning-news.json"

MIN_SCORE = 5  # skip sources whose best article scores below this


def main() -> None:
    if not MORNING_NEWS.exists():
        sys.exit(0)

    try:
        data = json.loads(MORNING_NEWS.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)

    articles = data.get("articles", [])
    if not articles:
        sys.exit(0)

    # Best article per source (highest score wins)
    by_source: dict = {}
    for art in articles:
        src = art.get("source", "—")
        score = art.get("score", 5)
        if src not in by_source or score > by_source[src].get("score", 0):
            by_source[src] = art

    # Sort by score descending, drop low-score sources
    ranked = sorted(by_source.values(), key=lambda a: a.get("score", 5), reverse=True)
    ranked = [a for a in ranked if a.get("score", 5) >= MIN_SCORE]

    if not ranked:
        sys.exit(0)

    medals = ["🥇", "🥈", "🥉"]
    lines = ["📊 <b>Топ новостей по источникам:</b>"]

    for i, art in enumerate(ranked):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        source = art.get("source", "—")
        title  = art.get("title_ru") or art.get("title", "Без названия")
        url    = art.get("url", "")
        score  = art.get("score", 5)
        summary = art.get("summary", "")

        title_link = f'<a href="{url}">{title}</a>' if url else title
        lines.append(f"\n{medal} <b>{source}</b> <i>({score}/10)</i>")
        lines.append(f"  {title_link}")
        if summary:
            lines.append(f"  ↳ {summary}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
