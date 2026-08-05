/**
 * Typed client for the Gauntlet API.
 *
 * The types here mirror the Pydantic models in `src/gauntlet/models.py`. They
 * are hand-written rather than generated: the surface is small, and a generator
 * would add a build step to keep in sync for eight interfaces.
 */

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface Position {
  ticker: string;
  weight: number;
  rationale: string;
}

export interface Allocation {
  strategy_summary: string;
  positions: Position[];
}

export interface PerformanceMetrics {
  total_return: number;
  cagr: number;
  annualized_volatility: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  calmar_ratio: number;
  trading_days: number;
  years: number;
}

export interface BacktestResult {
  start: string;
  end: string;
  initial_capital: number;
  final_value: number;
  rebalance: string;
  metrics: PerformanceMetrics;
  calendar_year_returns: Record<string, number>;
  equity_curve: [string, number][];
  drawdown_curve: [string, number][];
}

export interface Violation {
  rule: string;
  message: string;
}

export interface ComplianceReport {
  passed: boolean;
  violations: Violation[];
  checks_run: string[];
  sector_exposures: Record<string, number>;
}

export interface ScreeningFinding {
  ticker: string;
  concern: string;
  excerpt: string;
  doc_id: string;
  recommend_exclusion: boolean;
}

export interface ScreeningReport {
  passed: boolean;
  findings: ScreeningFinding[];
  tickers_screened: string[];
  ungrounded_findings_dropped: number;
}

export interface SynthesisVerdict {
  decision: "approve" | "revise";
  reasoning: string;
  revision_feedback: string | null;
  memo: string | null;
}

export interface Iteration {
  index: number;
  allocation: Allocation;
  backtest: BacktestResult;
  compliance: ComplianceReport;
  screening: ScreeningReport;
  verdict: SynthesisVerdict;
}

export interface Mandate {
  name: string;
  risk_profile: string;
  objective: string;
  start: string;
  end: string;
  initial_capital: number;
  rebalance: string;
  constraints: Constraints;
}

export interface AssetClassBand {
  min_weight: number;
  max_weight: number;
}

export interface Constraints {
  max_position_weight: number;
  max_sector_weight: number;
  min_holdings: number;
  max_holdings: number;
  excluded_sectors: string[];
  excluded_tickers: string[];
  /** Keyed by bucket: "equity" | "fixed_income" | "commodity". A map, not a
   *  list — the key *is* the bucket name. */
  asset_class_bands: Record<string, AssetClassBand>;
  max_drawdown_tolerance: number | null;
  max_volatility: number | null;
}

/** A demo mandate plus the key `startRun` needs. Three of the five share a
 *  `risk_profile`, so the key is the only safe identifier. */
export interface MandateOption {
  key: string;
  mandate: Mandate;
}

export interface RunReport {
  mandate: Mandate;
  allocation: Allocation;
  backtest: BacktestResult;
  sector_exposures: Record<string, number>;
  compliance: ComplianceReport;
  screening: ScreeningReport;
  verdict: SynthesisVerdict;
  iterations: Iteration[];
  malformed_proposals: string[];
  approved: boolean;
}

export interface RunSummary {
  run_id: string;
  /** Job lifecycle. Distinct from `approved`, which is the verdict — a run can
   *  complete cleanly and be unapproved, which is the governance loop working. */
  status: "queued" | "running" | "completed" | "failed";
  approved: boolean | null;
  iterations: number | null;
  cost_usd: number | null;
  total_tokens: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface RunStatus extends RunSummary {
  error: string | null;
  report: RunReport | null;
}

export interface ProgressEvent {
  run_id: string;
  seq: number;
  stage: string;
  status: string;
  detail: string;
  iteration: number;
  at: string;
}

export interface FieldRange {
  lower: number | null;
  upper: number | null;
  /** No freedom left — render a disabled control, not a slider pinned at one value. */
  locked: boolean;
  note: string | null;
}

export interface FeasibilityReport {
  feasible: boolean;
  problems: string[];
  /** Keyed by field path, e.g. "max_holdings" or
   *  "asset_class_bands.fixed_income.min_weight". */
  ranges: Record<string, FieldRange>;
}

class ApiError extends Error {
  // Assigned in the body rather than as a parameter property: the tsconfig sets
  // `erasableSyntaxOnly`, which rejects syntax that emits runtime code.
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Bearer token for write routes. Null when auth is disabled or signed out. */
export function authHeader(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    // 503 means a store is down and the client should retry; surfacing the
    // status lets callers distinguish that from a genuine 4xx.
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(body.detail ?? "Request failed", response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  base: BASE,

  listMandates: () => request<MandateOption[]>("/api/mandates"),

  /**
   * The whole constraint set is posted, not one field — the fields are
   * interdependent (raising max_holdings widens the legal max_position_weight,
   * which widens the reachable asset-class floors), so a per-field check would
   * report stale bounds.
   *
   * `includeRisk` additionally backtests a defensive portfolio to report what
   * drawdown is demonstrably reachable. Opt-in: it turns a sub-millisecond call
   * into a few hundred milliseconds, so it is debounced rather than run on every
   * keystroke.
   */
  checkFeasibility: (constraints: Constraints, includeRisk = false) =>
    request<FeasibilityReport>(
      `/api/constraints/feasibility?include_risk=${includeRisk}`,
      { method: "POST", body: JSON.stringify(constraints) },
    ),

  /** The only write route, and the only one requiring a token — a run spends
   *  real money, whereas everything readable here is the demo. */
  startRun: (body: { mandate_name?: string; mandate?: Mandate }, token: string | null) =>
    request<{ run_id: string; status: string }>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
      headers: authHeader(token),
    }),

  authConfig: () => request<{ enabled: boolean }>("/api/auth"),

  getRun: (runId: string) => request<RunStatus>(`/api/runs/${runId}`),

  listRuns: () => request<RunSummary[]>("/api/runs"),
};

export { ApiError };
