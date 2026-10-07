"""Orders pipeline — expects ~800 rows/run. Drift: total -> total_amount."""
from __future__ import annotations

import random
from typing import Any

from pipelines.base import BasePipeline

ORDER_STATUS = ["placed", "shipped", "delivered", "returned", "cancelled"]


class OrdersPipeline(BasePipeline):
    name = "orders"
    expected_rows = 800
    columns = [
        "order_id", "customer_name", "item", "quantity",
        "status", "order_date", "total",
    ]
    critical_columns = ["order_id", "total"]
    drift_column = "total"
    drift_new_name = "total_amount"

    def generate_row(self) -> dict[str, Any]:
        qty = random.randint(1, 6)
        price = round(random.uniform(10.0, 300.0), 2)
        return {
            "order_id": self.faker.uuid4(),
            "customer_name": self.faker.name(),
            "item": self.faker.word().title(),
            "quantity": qty,
            "status": random.choice(ORDER_STATUS),
            "order_date": self.faker.date_this_year().isoformat(),
            "total": round(qty * price, 2),
        }
