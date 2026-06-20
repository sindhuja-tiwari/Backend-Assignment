from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, init_db
from app.models import Job, JobSummary, Transaction
from app.services.data_processor import dataframe_to_csv, read_transactions_csv
from app.tasks import process_transaction_job


app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline",
    version="2.0.0",
    description="Async CSV processing with FastAPI, PostgreSQL, Redis, Celery, and LLM-backed classification.",
)


@app.on_event("startup")
def on_startup() -> None:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "transaction-pipeline"}


@app.post("/jobs/upload", status_code=202)
async def upload(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file.")

    try:
        read_transactions_csv(file.file)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        file.file.seek(0)

    job_id = str(uuid4())
    saved_path = _save_upload(job_id, file)

    job = Job(id=job_id, filename=file.filename, status="pending")
    db.add(job)
    db.commit()

    process_transaction_job.delay(job_id, str(saved_path))

    return {
        "job_id": job_id,
        "status": "pending",
        "status_url": f"/jobs/{job_id}/status",
        "results_url": f"/jobs/{job_id}/results",
    }


@app.get("/jobs")
def list_jobs(db: Session = Depends(get_db)) -> dict:
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    return {"jobs": [_job_payload(job) for job in jobs]}


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = _get_job(db, job_id)
    payload = _job_payload(job)

    if job.status == "completed" and job.summary:
        payload["summary"] = {
            "total_spend_inr": job.summary.total_spend_inr,
            "total_spend_usd": job.summary.total_spend_usd,
            "anomaly_count": job.summary.anomaly_count,
            "risk_level": job.summary.risk_level,
            "narrative": job.summary.narrative,
        }
    if job.status == "failed":
        payload["error_message"] = job.error_message

    return payload


@app.get("/jobs/{job_id}/results")
def job_results(
    job_id: str,
    status: str | None = None,
    category: str | None = None,
    account_id: str | None = None,
    anomalies_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    job = _get_job(db, job_id)
    if job.status != "completed":
        return {
            "job_id": job.id,
            "status": job.status,
            "message": "Results are available after the job reaches completed status.",
        }

    query = select(Transaction).where(Transaction.job_id == job.id)
    if status:
        query = query.where(Transaction.status == status.upper())
    if category:
        query = query.where(Transaction.category == category.title())
    if account_id:
        query = query.where(Transaction.account_id == account_id)
    if anomalies_only:
        query = query.where(Transaction.is_anomaly.is_(True))

    all_rows = db.scalars(query.order_by(Transaction.date, Transaction.txn_id)).all()
    page = all_rows[offset : offset + limit]

    return {
        "job": _job_payload(job),
        "summary": _summary_payload(job.summary),
        "total": len(all_rows),
        "limit": limit,
        "offset": offset,
        "transactions": [_transaction_payload(row) for row in page],
    }


@app.get("/jobs/{job_id}/export")
def export_clean_csv(job_id: str, db: Session = Depends(get_db)) -> Response:
    job = _get_job(db, job_id)
    if job.status != "completed":
        raise HTTPException(status_code=409, detail="Job is not completed yet.")

    rows = db.scalars(
        select(Transaction).where(Transaction.job_id == job.id).order_by(Transaction.date)
    ).all()
    csv_body = dataframe_to_csv([_transaction_payload(row) for row in rows])
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}-clean-transactions.csv"'
        },
    )


def _save_upload(job_id: str, file: UploadFile) -> Path:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "transactions.csv").name
    path = settings.upload_dir / f"{job_id}-{safe_name}"
    with path.open("wb") as output:
        output.write(file.file.read())
    return path


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _job_payload(job: Job) -> dict:
    return {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "row_count_raw": job.row_count_raw,
        "row_count_clean": job.row_count_clean,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _summary_payload(summary: JobSummary | None) -> dict | None:
    if summary is None:
        return None
    return {
        "total_spend_inr": summary.total_spend_inr,
        "total_spend_usd": summary.total_spend_usd,
        "top_merchants": summary.top_merchants,
        "anomaly_count": summary.anomaly_count,
        "narrative": summary.narrative,
        "risk_level": summary.risk_level,
        "metrics": summary.metrics,
    }


def _transaction_payload(transaction: Transaction) -> dict:
    return {
        "txn_id": transaction.txn_id,
        "date": transaction.date,
        "merchant": transaction.merchant,
        "amount": transaction.amount,
        "currency": transaction.currency,
        "status": transaction.status,
        "category": transaction.category,
        "account_id": transaction.account_id,
        "notes": transaction.notes,
        "is_anomaly": transaction.is_anomaly,
        "anomaly_reason": transaction.anomaly_reason,
        "llm_category": transaction.llm_category,
        "llm_raw_response": transaction.llm_raw_response,
        "llm_failed": transaction.llm_failed,
    }
