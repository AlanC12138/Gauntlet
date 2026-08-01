import pytest

from gauntlet.retrieval import BM25Retriever, load_corpus
from gauntlet.universe import load_universe


def test_corpus_loads_with_valid_front_matter():
    corpus = load_corpus()
    assert len(corpus) >= 6
    assert len({d.doc_id for d in corpus}) == len(corpus), "duplicate doc_id"
    for doc in corpus:
        assert doc.body.strip(), f"{doc.doc_id} has an empty body"
        assert doc.category


def test_every_corpus_ticker_exists_in_the_universe():
    """A note referencing a ticker outside the universe would be dead weight the
    retriever could never link to a holding."""
    known = set(load_universe().tickers)
    for doc in load_corpus():
        unknown = set(doc.tickers) - known
        assert not unknown, f"{doc.doc_id} references non-universe tickers {unknown}"


def test_corpus_contains_non_exclusion_notes():
    """Retrieval that only ever returns adverse material trains the agent to flag
    whatever it retrieves — the corpus needs contrary evidence too."""
    corpus = load_corpus()
    assert any(not d.exclusion for d in corpus)
    assert any(d.exclusion for d in corpus)


@pytest.fixture(scope="module")
def retriever():
    return BM25Retriever()


def test_holding_a_ticker_retrieves_its_note(retriever):
    results = retriever.search("sustainability screen", tickers=["PM"], k=5)
    assert "tobacco-and-nicotine" in {r.document.doc_id for r in results}


def test_entity_matches_outrank_lexical_ones(retriever):
    """A note naming a held position is relevant whether or not it shares
    vocabulary with the query."""
    results = retriever.search("coal generation thermal", tickers=["PM"], k=6)
    assert results[0].document.doc_id == "tobacco-and-nicotine"
    assert results[0].matched_on.startswith("entity")


def test_concept_query_retrieves_without_any_ticker(retriever):
    results = retriever.search("thermal coal power generation", tickers=[], k=3)
    assert "thermal-coal-generation" in {r.document.doc_id for r in results}
    assert all(r.matched_on == "lexical" for r in results)


def test_coal_exposure_is_only_discoverable_via_the_corpus(retriever):
    """The universe records DUK and SO as 'Utilities' and nothing more. This is
    the information asymmetry the sustainable mandate's loop-back depends on."""
    universe = load_universe()
    for ticker in ("DUK", "SO"):
        assert universe.by_ticker(ticker).sector == "Utilities"
        assert "coal" not in universe.by_ticker(ticker).name.lower()

    results = retriever.search("net zero transition", tickers=["DUK", "SO"], k=5)
    assert "thermal-coal-generation" in {r.document.doc_id for r in results}


def test_k_bounds_the_result_count(retriever):
    assert len(retriever.search("fossil fuel exclusion", tickers=["XOM"], k=2)) == 2


def test_results_are_deterministic(retriever):
    """Phase 3's precision/recall is meaningless if retrieval drifts between runs."""
    a = retriever.search("weapons exclusion", tickers=["LMT"], k=4)
    b = retriever.search("weapons exclusion", tickers=["LMT"], k=4)
    assert [r.document.doc_id for r in a] == [r.document.doc_id for r in b]
    assert [r.score for r in a] == [r.score for r in b]
