from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_layout() -> dict[str, Path]:
    repo_root = Path(__file__).resolve().parents[2]
    layout_path = repo_root / "aiinvest.layout.json"
    defaults = {
        "project_dir": "PRJCT",
        "reports_dir": "RPRTS",
        "database_dir": "DTB",
        "llm_dir": "LLM",
        "backup_dir": "BCKP",
    }
    if layout_path.exists():
        try:
            raw = json.loads(layout_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                defaults.update({k: str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()})
        except Exception:
            pass

    return {
        "repo_root": repo_root,
        "layout_path": layout_path,
        "project_dir": repo_root / defaults["project_dir"],
        "reports_dir": repo_root / defaults["reports_dir"],
        "database_dir": repo_root / defaults["database_dir"],
        "llm_dir": repo_root / defaults["llm_dir"],
        "backup_dir": repo_root / defaults["backup_dir"],
    }
