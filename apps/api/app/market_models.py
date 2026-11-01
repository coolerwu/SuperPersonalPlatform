from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.market_db import MarketBase
from app.models import UTCDateTime, utcnow


class MarketCandle(MarketBase):
    __tablename__ = "market_candles"

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    granularity: Mapped[str] = mapped_column(String(10), primary_key=True)
    open_time_ms: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    base_volume: Mapped[float] = mapped_column(Float, default=0.0)
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(20), default="bitget")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class MarketSyncState(MarketBase):
    __tablename__ = "market_sync_state"

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    granularity: Mapped[str] = mapped_column(String(10), primary_key=True)
    oldest_available_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    newest_synced_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
