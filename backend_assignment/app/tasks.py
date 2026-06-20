from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.celery_app import celery_app
from app.db import SessionLocal, init_db
from app.models import Job, JobSummary, Transaction
from app.services.data_processor import TransactionProcessingError, process_transactions, read_transactions_csv
from app.services.llm import LLMClassifier


@celery_app.task(name="process_transaction_job")
def process_transaction_job(job_id: str, file_path: str) -> dict:
    init_db()
    db = SessionLocal()
    classifier = LLMClassifier()

    try:
        job = db.get(Job, job_id)
        if not job:
            return {"job_id": job_id, "status": "missing"}

        job.status = "processing"
        db.commit()

        with Path(file_path).open("rb") as uploaded:
            df = read_transactions_csv(uploaded)
        result = process_transactions(df)

        anomaly_lookup = {
            item["txn_id"]: ", ".join(item["reasons"]) for item in result.anomalies
        }

        stored_transactions = []
        for record in result.transactions:
            llm = classifier.classify(record)
            is_anomaly = bool(anomaly_lookup.get(record["txn_id"]) or llm["is_anomaly"])
            anomaly_reason = anomaly_lookup.get(record["txn_id"]) or llm["anomaly_reason"]
            stored_transactions.append(
                Transaction(
                    job_id=job.id,
                    txn_id=record["txn_id"],
                    date=record["date"],
                    merchant=record["merchant"],
                    amount=record["amount"],
                    currency=record["currency"],
                    status=record["status"],
                    category=record["category"],
                    account_id=record["account_id"],
                    notes=record.get("notes") or "",
                    is_anomaly=is_anomaly,
                    anomaly_reason=anomaly_reason,
                    llm_category=llm["category"],
                    llm_raw_response=llm["raw_response"],
                    llm_failed=llm["failed"],
                )
            )

        db.add_all(stored_transactions)

        summary = _build_persisted_summary(result.summary, result.transactions, classifier)
        db.add(
            JobSummary(
                job_id=job.id,
                total_spend_inr=summary["total_spend_inr"],
                total_spend_usd=summary["total_spend_usd"],
                top_merchants=summary["top_merchants"],
                anomaly_count=summary["anomaly_count"],
                narrative=summary["narrative"],
                risk_level=summary["risk_level"],
                metrics=summary["metrics"],
            )
        )

        job.status = "completed"
        job.row_count_raw = result.quality_report["raw_rows"]
        job.row_count_clean = result.quality_report["clean_rows"]
        job.completed_at = datetime.now(timezone.utc)
        db.commit()

        return {"job_id": job.id, "status": job.status}
    except TransactionProcessingError as exc:
        _mark_failed(db, job_id, str(exc))
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
    except Exception as exc:
        _mark_failed(db, job_id, "Unexpected processing error.")
        raise exc
    finally:
        db.close()


def _build_persisted_summary(
    metrics: dict,
    transactions: list[dict],
    classifier: LLMClassifier,
) -> dict:
    frame = pd.DataFrame(transactions)
    successful = frame[frame["status"].eq("SUCCESS")]
    total_spend_inr = float(successful[successful["currency"].eq("INR")]["amount"].sum())
    total_spend_usd = float(successful[successful["currency"].eq("USD")]["amount"].sum())
    top_merchants = (
        successful.groupby("merchant")
        .agg(count=("txn_id", "count"), total_amount=("amount", "sum"))
        .reset_index()
        .sort_values("total_amount", ascending=False)
        .head(5)
        .round({"total_amount": 2})
        .to_dict(orient="records")
    )
    anomaly_count = int(metrics["anomaly_count"])

    return {
        "total_spend_inr": round(total_spend_inr, 2),
        "total_spend_usd": round(total_spend_usd, 2),
        "top_merchants": top_merchants,
        "anomaly_count": anomaly_count,
        "narrative": classifier.summarize(metrics),
        "risk_level": classifier.risk_level(anomaly_count, len(transactions)),
        "metrics": metrics,
    }


def _mark_failed(db, job_id: str, message: str) -> None:
    job = db.get(Job, job_id)
    if job:
        job.status = "failed"
        job.error_message = message
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
