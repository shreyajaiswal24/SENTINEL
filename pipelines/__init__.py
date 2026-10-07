from pipelines.sales import SalesPipeline
from pipelines.orders import OrdersPipeline
from pipelines.payments import PaymentsPipeline

PIPELINES = {
    "sales": SalesPipeline,
    "orders": OrdersPipeline,
    "payments": PaymentsPipeline,
}

__all__ = ["SalesPipeline", "OrdersPipeline", "PaymentsPipeline", "PIPELINES"]
