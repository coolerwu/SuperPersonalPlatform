import json
import uuid
from pathlib import Path

from server.domain.portfolio import Holding, HoldingNotFoundError, _now_iso


class PortfolioService:
    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / "portfolio"

    def _data_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def _holdings_path(self) -> Path:
        return self._data_dir() / "holdings.json"

    def _read_holdings(self) -> list[dict]:
        path = self._holdings_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_holdings(self, raw: list[dict]) -> None:
        path = self._holdings_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _to_dict(self, h: Holding) -> dict:
        return {
            "id": h.id,
            "type": h.type,
            "symbol": h.symbol,
            "name": h.name,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "currency": h.currency,
            "notes": h.notes,
            "created_at": h.created_at,
            "updated_at": h.updated_at,
        }

    def _from_dict(self, d: dict) -> Holding:
        return Holding(
            id=d["id"],
            type=d["type"],
            symbol=d["symbol"],
            name=d.get("name", ""),
            quantity=d["quantity"],
            avg_cost=d["avg_cost"],
            currency=d["currency"],
            notes=d.get("notes", ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    def list_holdings(self) -> list[Holding]:
        return [self._from_dict(d) for d in self._read_holdings()]

    def get_holding(self, holding_id: str) -> Holding:
        for d in self._read_holdings():
            if d["id"] == holding_id:
                return self._from_dict(d)
        raise HoldingNotFoundError(f"holding {holding_id} not found")

    def create_holding(
        self,
        type_: str,
        symbol: str,
        name: str,
        quantity: float,
        avg_cost: float,
        currency: str = "CNY",
        notes: str = "",
    ) -> Holding:
        now = _now_iso()
        holding = Holding(
            id=f"h-{uuid.uuid4().hex[:12]}",
            type=type_,
            symbol=symbol.strip().upper(),
            name=name.strip(),
            quantity=quantity,
            avg_cost=avg_cost,
            currency=currency.strip().upper(),
            notes=notes.strip(),
            created_at=now,
            updated_at=now,
        )
        raw = self._read_holdings()
        raw.append(self._to_dict(holding))
        self._write_holdings(raw)
        return holding

    def update_holding(
        self,
        holding_id: str,
        *,
        type_: str | None = None,
        symbol: str | None = None,
        name: str | None = None,
        quantity: float | None = None,
        avg_cost: float | None = None,
        currency: str | None = None,
        notes: str | None = None,
    ) -> Holding:
        raw = self._read_holdings()
        for i, d in enumerate(raw):
            if d["id"] == holding_id:
                if type_ is not None:
                    d["type"] = type_
                if symbol is not None:
                    d["symbol"] = symbol.strip().upper()
                if name is not None:
                    d["name"] = name.strip()
                if quantity is not None:
                    d["quantity"] = quantity
                if avg_cost is not None:
                    d["avg_cost"] = avg_cost
                if currency is not None:
                    d["currency"] = currency.strip().upper()
                if notes is not None:
                    d["notes"] = notes.strip()
                d["updated_at"] = _now_iso()
                raw[i] = d
                self._write_holdings(raw)
                return self._from_dict(d)
        raise HoldingNotFoundError(f"holding {holding_id} not found")

    def delete_holding(self, holding_id: str) -> None:
        raw = self._read_holdings()
        new_raw = [d for d in raw if d["id"] != holding_id]
        if len(new_raw) == len(raw):
            raise HoldingNotFoundError(f"holding {holding_id} not found")
        self._write_holdings(new_raw)

    def portfolio_summary(self) -> dict:
        """Return a text summary of all holdings for AI context."""
        holdings = self.list_holdings()
        if not holdings:
            return {"total_count": 0, "total_cost": 0, "holdings": []}
        total_cost = sum(h.total_cost for h in holdings)
        return {
            "total_count": len(holdings),
            "total_cost": round(total_cost, 2),
            "holdings": [
                {
                    "id": h.id,
                    "type": h.type,
                    "symbol": h.symbol,
                    "name": h.name,
                    "quantity": h.quantity,
                    "avg_cost": h.avg_cost,
                    "total_cost": h.total_cost,
                    "currency": h.currency,
                    "notes": h.notes,
                }
                for h in holdings
            ],
        }
