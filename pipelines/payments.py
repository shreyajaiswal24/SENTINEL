"""Payments pipeline — expects ~500 rows/run. Drift: amount -> total_amount."""
from __future__ import annotations

import random
from typing import Any

from pipelines.base import BasePipeline

CURRENCIES = ["USD", "EUR", "GBP", "INR", "BRL"]
METHODS = ["card", "paypal", "bank_transfer", "wallet"]
PAY_STATUS = ["authorized", "captured", "failed", "refunded"]


class PaymentsPipeline(BasePipeline):
    name = "payments"
    expected_rows = 500
    columns = [
        "payment_id", "order_id", "amount", "currency",
        "method", "status", "paid_at",
    ]
    critical_columns = ["payment_id", "amount"]
    drift_column = "amount"
    drift_new_name = "total_amount"

    def generate_row(self) -> dict[str, Any]:
        return {
            "payment_id": self.faker.uuid4(),
            "order_id": self.faker.uuid4(),
            "amount": round(random.uniform(5.0, 1000.0), 2),
            "currency": random.choice(CURRENCIES),
            "method": random.choice(METHODS),
            "status": random.choice(PAY_STATUS),
            "paid_at": self.faker.date_time_this_year().isoformat(),
        }
