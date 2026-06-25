"""Work memory storage management for ~/.dbrain/work/.

Handles paths, directory creation, attachment saving, and read helpers
for the work context feature (raw, digest, index, commitments).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum characters passed to Claude for digest generation.
# Raw file is always saved in full; this limit only applies to the prompt.
MAX_DIGEST_CHARS = 50_000


class WorkMemory:
    """Manages persistent work context storage under base_dir (~/.dbrain/work)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            from d_brain.config import get_settings
            base_dir = get_settings().work_dir
        self.base_dir = Path(base_dir).expanduser().resolve()

    # ── Directory helpers ────────────────────────────────────────────────────

    def ensure_dirs(self) -> None:
        """Create all required top-level subdirectories."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("raw", "digest", "attachments"):
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    def month_dir(self, subdir: str) -> Path:
        """Return and create the YYYY-MM subdirectory under *subdir*."""
        month = date.today().strftime("%Y-%m")
        d = self.base_dir / subdir / month
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Path helpers ─────────────────────────────────────────────────────────

    def raw_path(self, slug: str) -> Path:
        """Absolute path for a raw content file."""
        today = date.today().isoformat()
        return self.month_dir("raw") / f"{today}-{slug}.md"

    def digest_path(self, slug: str) -> Path:
        """Absolute path for a digest file."""
        today = date.today().isoformat()
        return self.month_dir("digest") / f"{today}-{slug}.md"

    @property
    def index_path(self) -> Path:
        return self.base_dir / "index.md"

    @property
    def commitments_path(self) -> Path:
        return self.base_dir / "commitments.md"

    # ── Write helpers ─────────────────────────────────────────────────────────

    def save_attachment(self, data: bytes, ext: str, message_id: int) -> Path:
        """Save attachment bytes; return absolute path."""
        today = date.today().isoformat()
        filename = f"{today}-{message_id}.{ext.lstrip('.')}"
        path = self.month_dir("attachments") / filename
        path.write_bytes(data)
        logger.debug("Work attachment saved: %s", path)
        return path

    # ── Read helpers ──────────────────────────────────────────────────────────

    def list_digests(self) -> list[Path]:
        """List all digest files sorted by name (chronological)."""
        digest_root = self.base_dir / "digest"
        if not digest_root.exists():
            return []
        return sorted(digest_root.rglob("*.md"))

    def read_index(self) -> str:
        """Read index.md content (empty string if missing)."""
        if not self.index_path.exists():
            return ""
        return self.index_path.read_text(encoding="utf-8")

    def read_commitments(self) -> str:
        """Read commitments.md content (empty string if missing)."""
        if not self.commitments_path.exists():
            return ""
        return self.commitments_path.read_text(encoding="utf-8")
