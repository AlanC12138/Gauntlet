"""Result contracts for the eval harness.

Structured rather than printed so Phase 4 can persist runs to Postgres and the
README's headline numbers can be regenerated rather than hand-copied.
"""

from pydantic import BaseModel, Field


class CaseResult(BaseModel):
    """One evaluated case."""

    name: str
    passed: bool
    detail: str = ""


class MetricAgreement(BaseModel):
    """How closely one computed metric tracked its reference value."""

    metric: str
    cases: int
    max_absolute_error: float
    tolerance: float

    @property
    def passed(self) -> bool:
        return self.max_absolute_error <= self.tolerance


class RuleScore(BaseModel):
    """Precision/recall for one compliance rule.

    Counted per (case, rule): a true positive is a case where the rule should
    fire and did. Cases where a rule neither should nor did fire are true
    negatives and are not counted into precision or recall, but they are what
    catches an engine that flags everything.
    """

    rule: str
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 1.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def perfect(self) -> bool:
        return self.false_positives == 0 and self.false_negatives == 0


class EvalSuiteResult(BaseModel):
    suite: str
    passed: bool
    summary: str
    cases: list[CaseResult] = Field(default_factory=list)
    agreements: list[MetricAgreement] = Field(default_factory=list)
    rule_scores: list[RuleScore] = Field(default_factory=list)

    @property
    def failures(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.passed]


class EvalReport(BaseModel):
    suites: list[EvalSuiteResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.suites)
