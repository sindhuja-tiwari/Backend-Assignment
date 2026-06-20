from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = {
    "txn_id",
    "date",
    "merchant",
    "amount",
    "currency",
    "status",
    "category",
    "account_id",
    "notes",
}

CATEGORY_BY_MERCHANT = {
    "swiggy": "Food",
    "zomato": "Food",
    "amazon": "Shopping",
    "flipkart": "Shopping",
    "myntra": "Shopping",
    "bigbasket": "Shopping",
    "ola": "Transport",
    "uber": "Transport",
    "irctc": "Travel",
    "netflix": "Entertainment",
    "bookmyshow": "Entertainment",
    "airtel": "Utilities",
    "paytm": "Utilities",
    "hdfc atm": "Cash Withdrawal",
}

VALID_STATUSES = {"SUCCESS", "FAILED", "PENDING"}
HIGH_VALUE_THRESHOLD = 10_000


class TransactionProcessingError(ValueError):
    """Raised when the uploaded CSV cannot be processed."""


@dataclass(frozen=True)
class ProcessingResult:
    transactions: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    summary: dict[str, Any]
    quality_report: dict[str, Any]


def read_transactions_csv(file_obj: Any) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_obj)
    except Exception as exc:
        raise TransactionProcessingError("Uploaded file must be a readable CSV.") from exc

    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        ordered = ", ".join(sorted(missing))
        raise TransactionProcessingError(f"CSV is missing required columns: {ordered}")

    return df


def process_transactions(df: pd.DataFrame) -> ProcessingResult:
    raw_count = len(df)
    cleaned, quality_report = clean_transactions(df)
    anomalies = detect_anomalies(cleaned)
    transactions = _records(cleaned)
    summary = build_summary(cleaned, anomalies, raw_count, quality_report)
    return ProcessingResult(transactions, anomalies, summary, quality_report)


def clean_transactions(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    working = df.copy()
    working.columns = [str(column).strip() for column in working.columns]
    working = working[list(REQUIRED_COLUMNS)]

    for column in REQUIRED_COLUMNS:
        working[column] = working[column].astype("string").fillna("").str.strip()

    working["amount"] = working["amount"].map(_parse_amount)
    working["date"] = working["date"].map(_parse_date)
    working["currency"] = working["currency"].replace("", "INR").str.upper()
    working["status"] = working["status"].str.upper()
    working["category"] = working.apply(_normalise_category, axis=1)

    invalid_mask = (
        working["txn_id"].eq("")
        | working["merchant"].eq("")
        | working["account_id"].eq("")
        | working["date"].isna()
        | working["amount"].isna()
    )
    invalid_rows = int(invalid_mask.sum())
    working = working.loc[~invalid_mask].copy()

    duplicate_mask = working.duplicated(subset=["txn_id"], keep="first")
    duplicate_rows = int(duplicate_mask.sum())
    working = working.loc[~duplicate_mask].copy()

    working["status"] = working["status"].where(
        working["status"].isin(VALID_STATUSES), "UNKNOWN"
    )
    working["amount"] = working["amount"].round(2)
    working = working.sort_values(["date", "txn_id"]).reset_index(drop=True)

    report = {
        "raw_rows": int(len(df)),
        "clean_rows": int(len(working)),
        "dropped_invalid_rows": invalid_rows,
        "dropped_duplicate_rows": duplicate_rows,
        "filled_categories": int(
            df["category"].astype("string").fillna("").str.strip().eq("").sum()
        ),
    }
    return working, report


def detect_anomalies(df: pd.DataFrame) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []

    for record in _records(df):
        reasons = []
        notes = str(record.get("notes") or "")

        if float(record["amount"]) >= HIGH_VALUE_THRESHOLD:
            reasons.append("high_value_transaction")
        if record["currency"] != "INR":
            reasons.append("non_inr_currency")
        if "SUSPICIOUS" in notes.upper():
            reasons.append("suspicious_note")
        if record["status"] == "UNKNOWN":
            reasons.append("unknown_status")

        if reasons:
            anomalies.append(
                {
                    "txn_id": record["txn_id"],
                    "date": record["date"],
                    "merchant": record["merchant"],
                    "amount": record["amount"],
                    "currency": record["currency"],
                    "account_id": record["account_id"],
                    "reasons": reasons,
                }
            )

    return anomalies


def build_summary(
    df: pd.DataFrame,
    anomalies: list[dict[str, Any]],
    raw_count: int,
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    successful = df[df["status"].eq("SUCCESS")]
    inr_successful = successful[successful["currency"].eq("INR")]
    month_series = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)

    return {
        "raw_rows": raw_count,
        "clean_rows": int(len(df)),
        "dropped_rows": quality_report["dropped_invalid_rows"]
        + quality_report["dropped_duplicate_rows"],
        "total_success_amount_inr": round(float(inr_successful["amount"].sum()), 2),
        "average_success_amount_inr": round(float(inr_successful["amount"].mean() or 0), 2),
        "anomaly_count": len(anomalies),
        "by_status": _group_count_amount(df, "status"),
        "by_category": _group_count_amount(df, "category"),
        "by_account": _group_count_amount(df, "account_id"),
        "by_month": _group_count_amount(df.assign(month=month_series), "month"),
    }


def dataframe_to_csv(records: list[dict[str, Any]]) -> str:
    output = StringIO()
    pd.DataFrame(records).to_csv(output, index=False)
    return output.getvalue()


def _parse_amount(value: Any) -> float | None:
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> str | None:
    text = str(value).strip()
    if not text:
        return None

    for date_format in ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def _normalise_category(row: pd.Series) -> str:
    category = str(row.get("category") or "").strip()
    if category:
        return category.title()

    merchant = str(row.get("merchant") or "").strip().lower()
    return CATEGORY_BY_MERCHANT.get(merchant, "Uncategorized")


def _group_count_amount(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    grouped = (
        df.groupby(column, dropna=False)
        .agg(count=("txn_id", "count"), total_amount=("amount", "sum"))
        .reset_index()
        .sort_values(["total_amount", "count"], ascending=False)
    )
    grouped["total_amount"] = grouped["total_amount"].round(2)
    return grouped.to_dict(orient="records")


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.to_dict(orient="records")
    for record in records:
        record["amount"] = float(record["amount"])
    return records
