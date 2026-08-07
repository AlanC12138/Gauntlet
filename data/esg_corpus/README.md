# ESG Screening Corpus

The retrieval corpus for the Screening Agent. Every document is a **reference
note written for this project**, not a news article, filing, or third-party
research report. Nothing here is a real document and nothing should be cited as
one.

## What these documents are

Each note describes a **well-established ESG exclusion category** — the kinds of
screens that appear in real institutional mandates — and identifies which
universe constituents fall under it **by stated line of business**. Philip Morris
International is identified as a tobacco manufacturer because that is its
business; Exxon Mobil as a hydrocarbon producer because that is its business.

Deliberately **not** here: invented controversies, fabricated incidents,
made-up regulatory actions, or quotes attributed to real people. Beyond being
irresponsible to write about real public companies, invented specifics would
make terrible eval ground truth — the Phase 3 harness needs labels that a
reviewer can check, and "PM manufactures tobacco" is checkable in a way that a
fictional scandal is not.

## Why a static corpus

Phase 3 measures the Screening Agent's precision and recall against labeled
cases. Live web search would make that number irreproducible: the same code
would score differently week to week as search results drift, and a regression
would be indistinguishable from the internet changing. These documents are
committed to git, so a screening result is reproducible and any change to it is
reviewable in a diff.

Phase 4 moved this corpus into Postgres, but **not** into pgvector — that was the
original plan and building it out turned into cargo-culting. At eight short notes,
dense embeddings would add an embedding-provider dependency and a similarity index
to search a body of text that fits in a single prompt, and would make retrieval
non-deterministic across provider model updates. That last point is disqualifying,
because Phase 3 scores screening reproducibility. Retrieval stays lexical: BM25
over the query text, plus entity linkage on the front-matter tickers, which is
what actually drives quality at this size.

These files remain the authoring source of truth — they are in git, so a change to
what the Screening Agent reads shows up in a diff and a review. `gauntlet
import-corpus` syncs them into the `esg_documents` table, which is the read path a
worker uses without a checkout. Writes upsert on `doc_id`, unlike the append-only
price cache: an ESG note is authored text and correcting one is legitimate, where
a price bar is an observation that must never change.

The `Retriever` protocol in `gauntlet/retrieval.py` remains the swap point if the
corpus ever outgrows lexical search.

## Format

Each `.md` file carries a YAML front-matter block:

```yaml
---
doc_id: tobacco-and-nicotine
category: Tobacco
tickers: [PM]
exclusion: true
---
```

`tickers` lists universe constituents the note bears on. `exclusion: false`
marks notes that are contextual or favorable — the corpus needs those so the
Screening Agent has to discriminate rather than flag everything it retrieves.

## Deliberate omission

There are **no ESG labels in `data/universe.json`**. The Screening Agent reads
that file, so putting the answers in its input would make both the agent and its
precision/recall numbers meaningless. The universe says what each instrument
*is*; this corpus is what the agent has to reason over.
