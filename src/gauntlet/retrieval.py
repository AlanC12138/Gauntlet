"""Retrieval over the ESG corpus.

## Why lexical retrieval and not embeddings

TECH_STACK.md targets pgvector, which arrives with Postgres in Phase 4. For the
current corpus — a handful of short notes — dense embeddings would be
cargo-culting: they add an embedding-provider dependency and a similarity index
to search a body of text small enough to fit in a single prompt, and they make
retrieval non-deterministic across provider model updates. That last point is
disqualifying, because Phase 3 scores screening precision/recall and needs the
retrieval step to be reproducible.

What actually drives quality at this size is **entity linkage**, not semantic
similarity: the question is "which notes bear on the tickers actually held,"
which the corpus answers exactly via front-matter. BM25 over the query text
supplements that for concept matches ("fossil fuel", "weapons") that no ticker
names.

`Retriever` is a Protocol so Phase 4 can substitute a pgvector implementation
without the Screening Agent noticing. If the corpus grows past a few dozen
documents, that swap is the moment to reconsider — not before.
"""

import functools
import logging
import math
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from gauntlet.config import DATA_DIR

logger = logging.getLogger(__name__)

CORPUS_DIR = DATA_DIR / "esg_corpus"

# Standard BM25 parameters. k1 controls term-frequency saturation, b controls
# length normalization.
BM25_K1 = 1.5
BM25_B = 0.75

_TOKEN = re.compile(r"[a-z0-9]{2,}")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Document(BaseModel):
    doc_id: str
    category: str
    tickers: list[str]
    exclusion: bool
    body: str

    def excerpt(self, limit: int = 600) -> str:
        return self.body if len(self.body) <= limit else self.body[:limit].rsplit(" ", 1)[0] + "…"


class RetrievedDocument(BaseModel):
    document: Document
    score: float
    # Why this document surfaced — entity linkage, lexical match, or both.
    matched_on: str


def _parse_front_matter(raw: str, path: Path) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        raise ValueError(f"{path.name} has no front-matter block")
    _, block, body = raw.split("---", 2)

    meta: dict[str, str] = {}
    for line in block.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.strip()


@functools.lru_cache(maxsize=1)
def load_corpus() -> list[Document]:
    documents: list[Document] = []
    for path in sorted(CORPUS_DIR.glob("*.md")):
        if path.name == "README.md":
            continue
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"), path)
        tickers = [
            t.strip() for t in meta.get("tickers", "[]").strip("[]").split(",") if t.strip()
        ]
        documents.append(
            Document(
                doc_id=meta["doc_id"],
                category=meta.get("category", "Uncategorized"),
                tickers=tickers,
                exclusion=meta.get("exclusion", "false").lower() == "true",
                body=body,
            )
        )
    if not documents:
        raise RuntimeError(f"ESG corpus at {CORPUS_DIR} is empty")
    logger.debug("Loaded %d ESG corpus documents", len(documents))
    return documents


class Retriever(Protocol):
    """Phase 4 swaps a pgvector implementation in behind this."""

    def search(
        self, query: str, tickers: list[str] | None = None, k: int = 5
    ) -> list[RetrievedDocument]: ...


class BM25Retriever:
    """Entity-linked retrieval with a BM25 lexical component.

    Documents naming a held ticker are always returned — an exclusion note about
    a position the portfolio actually holds is relevant whether or not the query
    happens to share vocabulary with it. BM25 then adds concept matches on top.
    """

    def __init__(self, documents: list[Document] | None = None) -> None:
        self.documents = documents if documents is not None else load_corpus()
        self._tokens = [_tokenize(d.body) for d in self.documents]
        self._lengths = [len(t) for t in self._tokens]
        self._avg_length = sum(self._lengths) / len(self._lengths)
        self._doc_freq: dict[str, int] = {}
        for tokens in self._tokens:
            for term in set(tokens):
                self._doc_freq[term] = self._doc_freq.get(term, 0) + 1

    def _bm25(self, query_terms: list[str], index: int) -> float:
        tokens = self._tokens[index]
        length = self._lengths[index]
        n_docs = len(self.documents)
        score = 0.0
        for term in query_terms:
            freq = tokens.count(term)
            if freq == 0:
                continue
            df = self._doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denominator = freq + BM25_K1 * (1 - BM25_B + BM25_B * length / self._avg_length)
            score += idf * (freq * (BM25_K1 + 1)) / denominator
        return score

    def search(
        self, query: str, tickers: list[str] | None = None, k: int = 5
    ) -> list[RetrievedDocument]:
        held = set(tickers or [])
        query_terms = _tokenize(query)

        scored: list[RetrievedDocument] = []
        for i, document in enumerate(self.documents):
            entity_hit = bool(held & set(document.tickers))
            lexical = self._bm25(query_terms, i) if query_terms else 0.0

            if entity_hit:
                matched_on = "entity+lexical" if lexical > 0 else "entity"
            elif lexical > 0:
                matched_on = "lexical"
            else:
                continue

            # Entity matches outrank pure lexical hits: a note naming a held
            # position is relevant regardless of shared vocabulary.
            scored.append(
                RetrievedDocument(
                    document=document,
                    score=lexical + (100.0 if entity_hit else 0.0),
                    matched_on=matched_on,
                )
            )

        scored.sort(key=lambda r: (-r.score, r.document.doc_id))
        return scored[:k]
