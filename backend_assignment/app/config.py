from __future__ import annotations

import os
from pathlib import Path


class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./transaction_insights.db",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    celery_task_always_eager: bool = os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "/tmp/transaction_uploads"))
    llm_provider: str = os.getenv("LLM_PROVIDER", "heuristic")
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")


settings = Settings()
