from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    """
    Root resolution order:
    1) CRAWL_SERVER_ROOT in environment or .env
    2) fallback: this file location -> <repo>/src/common/paths.py -> parents[2] == <repo>
    """
    # まずは .env を読み込む（どこから実行しても <repo>/.env を拾えるようにする）
    fallback_root = Path(__file__).resolve().parents[2]  # <Crawl_Server>
    load_dotenv(fallback_root / ".env", override=False)

    env_root = os.getenv("CRAWL_SERVER_ROOT", "").strip()
    if env_root:
        p = Path(env_root).expanduser().resolve()
        return p

    return fallback_root


def data_dir() -> Path:
    return repo_root() / "data"


def prompts_dir() -> Path:
    return repo_root() / "prompts"
