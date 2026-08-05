"""The corpus store, and its contrast with the price store.

Price bars are observations and never change; corpus notes are authored and can
legitimately be edited. These tests pin that difference, plus the property that
makes the store swap safe: both read paths must return the same corpus.
"""

from gauntlet.db import corpus as repo
from gauntlet.db import get_session
from gauntlet.retrieval import Document, load_corpus_from_files


def _doc(doc_id: str, body: str = "text", tickers: list[str] | None = None) -> Document:
    return Document(
        doc_id=doc_id,
        category="Test",
        tickers=tickers or [],
        exclusion=False,
        body=body,
    )


def test_stored_corpus_matches_the_files(clean_db):
    """The swap is only safe if the database copy is the files, exactly."""
    from_files = load_corpus_from_files()

    with get_session() as session:
        repo.upsert_documents(session, from_files)
    with get_session() as session:
        from_db = repo.load_documents(session)

    assert {d.doc_id for d in from_db} == {d.doc_id for d in from_files}
    assert sorted(from_db, key=lambda d: d.doc_id) == sorted(
        from_files, key=lambda d: d.doc_id
    )


def test_edits_are_applied_unlike_price_bars(clean_db):
    """An edited note must overwrite — the opposite of the append-only price path."""
    with get_session() as session:
        repo.upsert_documents(session, [_doc("a", body="original")])
    with get_session() as session:
        repo.upsert_documents(session, [_doc("a", body="corrected")])
    with get_session() as session:
        stored = repo.load_documents(session)

    assert len(stored) == 1
    assert stored[0].body == "corrected"


def test_deleted_documents_are_removed_from_the_read_path(clean_db):
    """A note deleted in git must not stay live, or a finding could cite a
    document that no longer exists."""
    with get_session() as session:
        repo.upsert_documents(session, [_doc("a"), _doc("b")])
    with get_session() as session:
        removed = repo.delete_absent(session, ["a"])
    with get_session() as session:
        remaining = [d.doc_id for d in repo.load_documents(session)]

    assert removed == 1
    assert remaining == ["a"]


def test_load_is_ordered_so_screening_context_is_reproducible(clean_db):
    with get_session() as session:
        repo.upsert_documents(session, [_doc("c"), _doc("a"), _doc("b")])
    with get_session() as session:
        assert [d.doc_id for d in repo.load_documents(session)] == ["a", "b", "c"]


def test_tickers_survive_the_round_trip(clean_db):
    with get_session() as session:
        repo.upsert_documents(session, [_doc("a", tickers=["XOM", "CVX"])])
    with get_session() as session:
        assert repo.load_documents(session)[0].tickers == ["XOM", "CVX"]


def test_retrieval_gives_the_same_answer_from_either_source(clean_db):
    """End-to-end parity: same query, same ranking, whichever path loaded it."""
    from gauntlet.retrieval import BM25Retriever

    from_files = load_corpus_from_files()
    with get_session() as session:
        repo.upsert_documents(session, from_files)
    with get_session() as session:
        from_db = repo.load_documents(session)

    query = "fossil fuel extraction and thermal coal"
    tickers = ["XOM", "CVX", "SPY"]
    file_hits = BM25Retriever(from_files).search(query, tickers=tickers, k=5)
    db_hits = BM25Retriever(from_db).search(query, tickers=tickers, k=5)

    assert [h.document.doc_id for h in file_hits] == [h.document.doc_id for h in db_hits]
    assert [round(h.score, 12) for h in file_hits] == [round(h.score, 12) for h in db_hits]
