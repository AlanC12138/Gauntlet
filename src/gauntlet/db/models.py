"""SQLAlchemy schema.

Two shapes live here, and the difference is deliberate:

* **Relational columns** for anything queried, filtered, or aggregated —
  ticker/date on price bars, approval status and cost on runs. These get indexes
  and real types.

* **JSONB payloads** for the full Pydantic trees (`RunReport`, `EvalReport`).
  A run carries two equity curves of 3,270 points each; normalising that into
  rows would be a lot of schema for data only ever read back whole. Storing the
  validated model as JSON keeps the Pydantic contract as the single source of
  truth and leaves the columns for the fields worth querying.

The price-bar table is the one place the shape was fixed long before this phase:
`data/prices.py` has written `(ticker, date, adj_close, fetched_at)` in long
format since Phase 1 specifically so this migration would be mechanical.
"""

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PriceBar(Base):
    """Append-only. A bar that has been observed is never updated.

    That is the property that makes historical runs reproducible: a backtest re-run
    months later sees the same prices even if the vendor has since revised them.
    Enforced by convention in the repository (insert-if-absent, never upsert)
    rather than by a database trigger — a trigger would also block the legitimate
    case of correcting a genuinely corrupt load.
    """

    __tablename__ = "price_bars"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    adj_close: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Every read is "these tickers over this window", so date leads the
        # secondary index to let the range scan do the work.
        Index("ix_price_bars_date_ticker", "date", "ticker"),
    )


class MandateRecord(Base):
    """A mandate as submitted. Stored whole because a client-built mandate is
    arbitrary — the demo presets are only five of infinitely many."""

    __tablename__ = "mandates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    risk_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    runs: Mapped[list["RunRecord"]] = relationship(back_populates="mandate")


class RunRecord(Base):
    """One trip through the propose/verify/govern loop.

    `status` tracks the queued job, not the verdict: a run can complete
    successfully and still be unapproved, which is a normal outcome rather than
    a failure. Those are separate columns for exactly that reason.
    """

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mandate_id: Mapped[int | None] = mapped_column(ForeignKey("mandates.id"), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The full RunReport. Read back whole; never queried into.
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    mandate: Mapped[MandateRecord | None] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_runs_created_at", "created_at"),)


class EvalRunRecord(Base):
    """A stored `gauntlet eval` execution.

    Kept so the harness becomes a trend rather than a snapshot — the point of a
    regression guard is noticing the run where a number moved.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suite: Mapped[str] = mapped_column(String(32), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_eval_runs_suite_created", "suite", "created_at"),)


class EsgDocument(Base):
    """The screening corpus.

    Moved into Postgres for one source of truth, but retrieval stays lexical.
    TECH_STACK.md targets pgvector; at eight short notes, dense embeddings would
    add an embedding-provider dependency and non-determinism across provider
    model updates to search a corpus that fits in a single prompt — and Phase 3
    scores screening reproducibility. `retrieval.Retriever` is the swap point if
    the corpus ever outgrows that.
    """

    __tablename__ = "esg_documents"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    tickers: Mapped[list] = mapped_column(JSONB, nullable=False)
    exclusion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
