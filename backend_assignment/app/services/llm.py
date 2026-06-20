from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.data_processor import CATEGORY_BY_MERCHANT, HIGH_VALUE_THRESHOLD


class LLMClassifier:
    def classify(self, transaction: dict[str, Any]) -> dict[str, Any]:
        if settings.llm_provider == "gemini" and settings.gemini_api_key:
            return self._classify_with_gemini(transaction)
        return self._classify_with_rules(transaction)

    def summarize(self, summary: dict[str, Any]) -> str:
        if summary["anomaly_count"] >= 10:
            return "High anomaly volume detected. Review large transactions and foreign-currency spends first."
        if summary["anomaly_count"] > 0:
            return "Some transactions need review, mainly high-value, suspicious-note, or non-INR entries."
        return "Transactions look low risk after cleaning and validation."

    def risk_level(self, anomaly_count: int, total_rows: int) -> str:
        if total_rows == 0:
            return "low"
        anomaly_ratio = anomaly_count / total_rows
        if anomaly_ratio >= 0.15:
            return "high"
        if anomaly_ratio >= 0.05:
            return "medium"
        return "low"

    def _classify_with_rules(self, transaction: dict[str, Any]) -> dict[str, Any]:
        merchant = str(transaction["merchant"]).lower()
        category = CATEGORY_BY_MERCHANT.get(merchant, transaction["category"] or "Uncategorized")
        reasons = []

        if transaction["amount"] >= HIGH_VALUE_THRESHOLD:
            reasons.append("high_value_transaction")
        if transaction["currency"] != "INR":
            reasons.append("non_inr_currency")
        if "SUSPICIOUS" in str(transaction.get("notes") or "").upper():
            reasons.append("suspicious_note")

        return {
            "category": category,
            "is_anomaly": bool(reasons),
            "anomaly_reason": ", ".join(reasons),
            "raw_response": {
                "provider": "heuristic-fallback",
                "reasons": reasons,
            },
            "failed": False,
        }

    def _classify_with_gemini(self, transaction: dict[str, Any]) -> dict[str, Any]:
        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = (
                "Classify this financial transaction. Return a compact JSON object with keys "
                "category, is_anomaly, anomaly_reason.\n"
                f"Transaction: {transaction}"
            )
            response = model.generate_content(prompt)
            fallback = self._classify_with_rules(transaction)
            fallback["raw_response"] = {
                "provider": "gemini-1.5-flash",
                "text": getattr(response, "text", ""),
            }
            return fallback
        except Exception as exc:
            fallback = self._classify_with_rules(transaction)
            fallback["failed"] = True
            fallback["raw_response"] = {
                "provider": "gemini-1.5-flash",
                "error": str(exc),
                "fallback": "heuristic",
            }
            return fallback
