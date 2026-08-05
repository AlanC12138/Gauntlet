"""ESG corpus repository.

Worth contrasting with `db/prices.py`, because the two make opposite choices for
the same reason. Price bars are *observations*: once seen they must never change,
so writes are insert-if-absent and a revised vendor price is discarded. Corpus
notes are *authored*: fixing a typo in an ESG note is a legitimate edit, so writes
upsert on `doc_id`.

The markdown files under `data/esg_corpus/` remain the authoring source of truth —
they are in git, so a change to what the Screening Agent reads shows up in a diff
and a review, which is the right place to catch it. This table is the read path
that a deployed worker uses without a checkout, populated by `gauntlet
import-corpus`.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from gauntlet.db.models import EsgDocument
from gauntlet.retrieval import Document


def upsert_documents(session: Session, documents: list[Document]) -> int:
    """Write documents, overwriting any existing row with the same doc_id."""
    if not documents:
        return 0

    rows = [d.model_dump() for d in documents]
    stmt = insert(EsgDocument).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["doc_id"],
        set_={
            "category": stmt.excluded.category,
            "tickers": stmt.excluded.tickers,
            "exclusion": stmt.excluded.exclusion,
            "body": stmt.excluded.body,
        },
    )
    session.execute(stmt)
    return len(rows)


def load_documents(session: Session) -> list[Document]:
    """Every stored document, ordered by doc_id.

    Ordering is not cosmetic: BM25 scores can tie across these short notes, and
    an unordered read would make the Screening Agent's context vary between
    identical runs — which Phase 3 measures.
    """
    stmt = select(EsgDocument).order_by(EsgDocument.doc_id)
    return [
        Document(
            doc_id=row.doc_id,
            category=row.category,
            tickers=list(row.tickers),
            exclusion=row.exclusion,
            body=row.body,
        )
        for row in session.execute(stmt).scalars()
    ]


def delete_absent(session: Session, keep: list[str]) -> int:
    """Remove stored documents no longer present in the source directory.

    Without this, deleting a note from git would leave it live in the read path —
    a screening finding could then cite a document that no longer exists.
    """
    stmt = select(EsgDocument).where(EsgDocument.doc_id.notin_(keep))
    stale = list(session.execute(stmt).scalars())
    for row in stale:
        session.delete(row)
    return len(stale)
