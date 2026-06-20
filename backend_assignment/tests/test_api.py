import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_transaction_pipeline.db"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["UPLOAD_DIR"] = "./test_uploads"

from fastapi.testclient import TestClient

from app.db import Base, engine, init_db
from app.main import app


client = TestClient(app)
CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "transactions.csv"


def setup_function():
    Base.metadata.drop_all(bind=engine)
    init_db()


def test_health_check():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_creates_completed_job_when_tasks_are_eager():
    with CSV_PATH.open("rb") as csv_file:
        response = client.post(
            "/jobs/upload",
            files={"file": ("transactions.csv", csv_file, "text/csv")},
        )

    assert response.status_code == 202
    job_id = response.json()["job_id"]

    status = client.get(f"/jobs/{job_id}/status")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "completed"
    assert body["row_count_raw"] == 91
    assert body["row_count_clean"] == 89
    assert body["summary"]["anomaly_count"] == 17


def test_results_include_transactions_summary_and_llm_fields():
    with CSV_PATH.open("rb") as csv_file:
        upload = client.post(
            "/jobs/upload",
            files={"file": ("transactions.csv", csv_file, "text/csv")},
        )
    job_id = upload.json()["job_id"]

    response = client.get(
        f"/jobs/{job_id}/results",
        params={"anomalies_only": True, "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 17
    assert body["summary"]["risk_level"] == "high"
    assert body["transactions"][0]["is_anomaly"] is True
    assert "llm_category" in body["transactions"][0]


def test_export_returns_clean_csv():
    with CSV_PATH.open("rb") as csv_file:
        upload = client.post(
            "/jobs/upload",
            files={"file": ("transactions.csv", csv_file, "text/csv")},
        )
    job_id = upload.json()["job_id"]

    response = client.get(f"/jobs/{job_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "TXN001" in response.text
    assert "Missing txn_id" not in response.text
