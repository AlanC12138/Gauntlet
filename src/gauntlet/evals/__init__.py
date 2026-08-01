"""The eval harness — the quantifiable artifact and the regression guard.

Two suites, measuring genuinely different kinds of correctness:

* **Backtest math** (`backtest_math.py`) — numeric correctness. Every metric is
  checked against an independently written reference implementation and against
  cases whose answers are derivable by hand. The question is "is the arithmetic
  right", and the expected score is exact agreement.

* **Compliance conformance** (`compliance.py`) — classification accuracy over
  labeled violation cases. The question is "does the rules engine flag what the
  mandate says it should", scored as per-rule precision and recall.

Keeping them separate is deliberate. Collapsing both into one headline number
would misrepresent both: a backtester needs numeric agreement to many decimal
places, a classifier needs precision and recall, and neither reduces to the other.
"""

from gauntlet.evals.models import EvalReport, EvalSuiteResult

__all__ = ["EvalReport", "EvalSuiteResult"]
