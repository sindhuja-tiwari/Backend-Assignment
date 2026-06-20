from __future__ import annotations

from celery import Celery

from app.config import settings


celery_app = Celery(
    "transaction_pipeline",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    worker_prefetch_multiplier=1,
)
