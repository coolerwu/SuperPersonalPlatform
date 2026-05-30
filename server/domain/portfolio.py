from dataclasses import dataclass
from datetime import datetime, timezone


class HoldingNotFoundError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Holding:
    id: str
    type: str  # "stock" | "fund" | "crypto"
    symbol: str
    name: str
    quantity: float
    avg_cost: float  # per-unit average cost in currency
    currency: str  # "CNY" | "USD" | "HKD"
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("holding id is required")
        if self.type not in ("stock", "fund", "crypto"):
            raise ValueError(f"invalid holding type: {self.type}")
        if not self.symbol.strip():
            raise ValueError("holding symbol is required")
        if self.quantity <= 0:
            raise ValueError("holding quantity must be positive")
        if self.avg_cost < 0:
            raise ValueError("holding avg_cost cannot be negative")
        if not self.currency.strip():
            raise ValueError("holding currency is required")

    @property
    def total_cost(self) -> float:
        """Total cost basis = quantity * avg_cost."""
        return round(self.quantity * self.avg_cost, 2)
