# Backend-Assignment
# AI-Powered Transaction Processing Pipeline

FastAPI backend for the Backend + DevOps internship assignment. It accepts a dirty transaction CSV, creates an async processing job, stores cleaned transactions in PostgreSQL, uses a Redis-backed Celery worker, classifies transactions with an LLM adapter, and exposes polling/result APIs.

The project runs with one command:

```bash
docker compose up --build
```

API docs will be available at:

```text
http://localhost:8000/docs
```

## Stack

- FastAPI
- PostgreSQL
- Redis
- Celery worker
- SQLAlchemy
- Pandas
- Gemini adapter with a heuristic fallback, so reviewers can run without paid keys
- Docker Compose

## Run

```bash
docker compose up --build
```

Optional Gemini mode:

```bash
LLM_PROVIDER=gemini GEMINI_API_KEY=your_key docker compose up --build
```

Without those variables, the app uses a deterministic local fallback that returns `llm_category`, `llm_raw_response`, and `llm_failed` for every transaction.

## Example Flow

Upload a CSV:

```bash
curl -F "file=@data/transactions.csv" http://localhost:8000/jobs/upload
```

Response:

```json
{
  "job_id": "replace-with-returned-id",
  "status": "pending",
  "status_url": "/jobs/replace-with-returned-id/status",
  "results_url": "/jobs/replace-with-returned-id/results"
}
```

Poll status:

```bash
curl http://localhost:8000/jobs/replace-with-returned-id/status
```

Fetch results:

```bash
curl http://localhost:8000/jobs/replace-with-returned-id/results
```

Fetch anomalies only:

```bash
curl "http://localhost:8000/jobs/replace-with-returned-id/results?anomalies_only=true"
```

Export cleaned CSV:

```bash
curl -o clean.csv http://localhost:8000/jobs/replace-with-returned-id/export
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Health check |
| `POST` | `/jobs/upload` | Validate CSV, create DB job, enqueue worker task |
| `GET` | `/jobs` | List submitted jobs |
| `GET` | `/jobs/{job_id}/status` | Poll pending, processing, completed, failed |
| `GET` | `/jobs/{job_id}/results` | Get summary and cleaned transactions |
| `GET` | `/jobs/{job_id}/export` | Download cleaned CSV |

## Data Model

- `jobs`: job metadata, status, raw/clean row counts, timestamps, errors
- `transactions`: cleaned transaction rows with anomaly and LLM fields
- `job_summaries`: total spend, top merchants, anomaly count, narrative, risk level, detailed metrics

## Processing Rules

- Parses `DD-MM-YYYY`, `YYYY/MM/DD`, and `YYYY-MM-DD`
- Normalizes currency/status casing
- Converts amounts such as `$89.99` to numeric values
- Fills missing categories from merchant mapping
- Drops rows with missing IDs or invalid required fields
- Drops duplicate `txn_id` rows
- Flags high-value transactions, non-INR transactions, suspicious notes, and unknown statuses


