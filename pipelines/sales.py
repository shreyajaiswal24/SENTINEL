"""Sales pipeline — expects ~1000 rows/run. Drift: amount -> total_amount."""
from __future__ import annotations

import random
from typing import Any

from pipelines.base import BasePipeline

CATEGORIES = ["Electronics", "Apparel", "Home", "Toys", "Grocery", "Beauty"]
REGIONS = ["North", "South", "East", "West", "Central"]


class SalesPipeline(BasePipeline):
    name = "sales"
    expected_rows = 1000
    columns = [
        "order_id", "customer", "product", "category",
        "quantity", "unit_price", "amount", "region", "sale_date",
    ]
    critical_columns = ["order_id", "amount"]
    drift_column = "amount"
    drift_new_name = "total_amount"

    def generate_row(self) -> dict[str, Any]:
        qty = random.randint(1, 10)
        unit_price = round(random.uniform(5.0, 500.0), 2)
        return {
            "order_id": self.faker.uuid4(),
            "customer": self.faker.name(),
            "product": self.faker.word().title(),
            "category": random.choice(CATEGORIES),
            "quantity": qty,
            "unit_price": unit_price,
            "amount": round(qty * unit_price, 2),
            "region": random.choice(REGIONS),
            "sale_date": self.faker.date_this_year().isoformat(),
        }
