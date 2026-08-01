"""Screening Agent — qualitative exclusion checks, RAG-grounded.

The counterpart to the compliance engine. Compliance is arithmetic and therefore
deterministic; this is judgment over unstructured text and therefore an LLM —
that split is the architectural claim of the whole project, and it only holds if
each side stays on its own ground.

Grounding is enforced, not requested. Every finding must quote a retrieved
document verbatim, and any excerpt that appears in no retrieved document is
dropped before the report leaves this module. An ungrounded finding is a
hallucination no matter how plausible it reads, and the Synthesis Agent has no
way to tell the difference downstream.
"""

import logging
import re

import anthropic

from gauntlet.config import settings
from gauntlet.models import (
    Allocation,
    Mandate,
    ScreeningFinding,
    ScreeningProposal,
    ScreeningReport,
)
from gauntlet.retrieval import BM25Retriever, RetrievedDocument, Retriever
from gauntlet.usage import LLMUsage, usage_from_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an ESG and controversy screening analyst. You are given a mandate's \
exclusion criteria, a proposed portfolio, and a set of reference notes retrieved \
from a research corpus. Your job is to decide which holdings breach the mandate's \
stated exclusions.

Rules you must follow:

1. Screen against THIS mandate's stated exclusions only. Do not invent exclusions \
the mandate did not ask for. A mandate that excludes fossil fuels says nothing \
about labor practices.

2. Ground every finding in the retrieved notes. The `excerpt` field must be text \
copied VERBATIM from one of the provided notes, and `source` must be that note's \
doc_id. If the notes do not support a concern, do not raise it. A finding you \
cannot quote is a finding you must not make.

3. Distinguish flagging from excluding. Set `recommend_exclude` to true only when \
the holding's core business is the excluded activity. For partial exposure, or \
where the mandate sets a threshold the notes cannot resolve, report the finding \
with `recommend_exclude` false and name the ambiguity in `concern`.

4. Retrieval relevance is not evidence. A note mentioning a ticker does not mean \
that ticker breaches anything — some notes exist precisely to record that a \
holding is NOT an exclusion candidate.

Return an empty findings list if nothing breaches. That is a valid and common \
result, not a failure to look.\
"""

# Normalizes whitespace so a quote that survived reformatting still matches.
_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def _render_exclusions(mandate: Mandate) -> str:
    c = mandate.constraints
    lines = []
    if c.excluded_tickers:
        lines.append(f"- Explicitly excluded tickers: {', '.join(c.excluded_tickers)}")
    if c.excluded_sectors:
        lines.append(f"- Explicitly excluded GICS sectors: {', '.join(c.excluded_sectors)}")
    lines.append(f"- Stated objective (may imply further exclusions): {mandate.objective}")
    return "\n".join(lines)


def _render_documents(retrieved: list[RetrievedDocument]) -> str:
    blocks = []
    for r in retrieved:
        d = r.document
        blocks.append(
            f"### doc_id: {d.doc_id}\n"
            f"category: {d.category}\n"
            f"relates to: {', '.join(d.tickers) or '(general)'}\n"
            f"matched on: {r.matched_on}\n\n"
            f"{d.body}"
        )
    return "\n\n---\n\n".join(blocks)


def _build_prompt(
    mandate: Mandate, allocation: Allocation, retrieved: list[RetrievedDocument]
) -> str:
    holdings = "\n".join(
        f"  {p.ticker:<6} {p.weight:>7.2%}" for p in sorted(
            allocation.positions, key=lambda p: -p.weight
        )
    )
    return "\n".join(
        [
            "# Mandate exclusions",
            _render_exclusions(mandate),
            "",
            "# Proposed holdings",
            holdings,
            "",
            "# Retrieved reference notes",
            "These are the only sources you may cite. Quote them verbatim.",
            "",
            _render_documents(retrieved),
        ]
    )


def _validate_grounding(
    proposal: ScreeningProposal,
    retrieved: list[RetrievedDocument],
    allocation: Allocation,
) -> tuple[list[ScreeningFinding], int]:
    """Drop findings whose excerpt appears in no retrieved document, or that name
    a ticker the portfolio does not hold."""
    bodies = {r.document.doc_id: _normalize(r.document.body) for r in retrieved}
    held = set(allocation.weights)

    kept: list[ScreeningFinding] = []
    dropped = 0
    for finding in proposal.findings:
        if finding.ticker not in held:
            logger.warning(
                "Screening: dropping finding for %s — not a proposed holding", finding.ticker
            )
            dropped += 1
            continue

        excerpt = _normalize(finding.excerpt)
        source_body = bodies.get(finding.source)
        grounded = source_body is not None and excerpt in source_body
        if not grounded:
            # Fall back to any retrieved document — a correct quote attributed to
            # the wrong doc_id is a citation error, not a hallucination.
            grounded = any(excerpt in body for body in bodies.values())
            if grounded:
                logger.warning(
                    "Screening: %s excerpt cited to %r but found in another note",
                    finding.ticker,
                    finding.source,
                )

        if not grounded:
            logger.warning(
                "Screening: dropping ungrounded finding for %s (excerpt not in any "
                "retrieved note)",
                finding.ticker,
            )
            dropped += 1
            continue

        kept.append(finding)
    return kept, dropped


def screen_allocation(
    allocation: Allocation,
    mandate: Mandate,
    retriever: Retriever | None = None,
    client: anthropic.Anthropic | None = None,
) -> tuple[ScreeningReport, LLMUsage]:
    retriever = retriever or BM25Retriever()
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)

    tickers = list(allocation.weights)
    query = " ".join(
        [mandate.objective, *mandate.constraints.excluded_sectors, *mandate.constraints.excluded_tickers]
    )
    retrieved = retriever.search(query, tickers=tickers, k=settings.screening_top_k)
    logger.info(
        "Screening: retrieved %d note(s): %s",
        len(retrieved),
        ", ".join(r.document.doc_id for r in retrieved),
    )

    response = client.messages.parse(
        model=settings.screening_model_id,
        max_tokens=settings.max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_prompt(mandate, allocation, retrieved)}],
        output_format=ScreeningProposal,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Screening Agent request was refused: {response.stop_details}")
    if response.parsed_output is None:
        raise RuntimeError(f"Screening Agent returned unparseable output ({response.stop_reason})")

    findings, dropped = _validate_grounding(response.parsed_output, retrieved, allocation)
    report = ScreeningReport(
        passed=not any(f.recommend_exclude for f in findings),
        findings=findings,
        tickers_screened=tickers,
        ungrounded_findings_dropped=dropped,
    )
    logger.info(
        "Screening: %s (%d finding(s), %d recommending exclusion, %d dropped as ungrounded)",
        "PASS" if report.passed else "FAIL",
        len(findings),
        sum(f.recommend_exclude for f in findings),
        dropped,
    )
    return report, usage_from_response("Screening Agent", settings.screening_model_id, response)
