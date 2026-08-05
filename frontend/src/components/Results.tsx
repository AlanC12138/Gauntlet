import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { RunReport } from "../api";

const pct = (n: number) => `${(n * 100).toFixed(2)}%`;

// Recharts types a tooltip value as possibly-undefined, so formatters take the
// widened type and narrow here rather than each call site asserting.
const asNumber = (v: unknown) => (typeof v === "number" ? v : Number(v ?? 0));
const money = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/**
 * Downsample for display only.
 *
 * 3,270 points is more than a chart has pixels for, and handing all of them to
 * SVG costs a visible amount of main-thread time on every hover. Every metric
 * shown alongside is computed server-side over the *full* series — this thins
 * what is drawn, never what is measured.
 */
function thin<T>(series: T[], target = 400): T[] {
  if (series.length <= target) return series;
  const step = Math.ceil(series.length / target);
  const out = series.filter((_, i) => i % step === 0);
  const last = series[series.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="metric" title={hint}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}

export function Results({ report }: { report: RunReport }) {
  const { backtest, allocation, compliance, screening, verdict } = report;
  const m = backtest.metrics;

  const equity = thin(
    backtest.equity_curve.map(([date, value]) => ({ date, value })),
  );
  const drawdown = thin(
    backtest.drawdown_curve.map(([date, value]) => ({ date, value: value * 100 })),
  );
  const positions = [...allocation.positions].sort((a, b) => b.weight - a.weight);
  const sectors = Object.entries(report.sector_exposures)
    .map(([sector, weight]) => ({ sector, weight: weight * 100 }))
    .sort((a, b) => b.weight - a.weight);

  return (
    <div className="results">
      <section className={`verdict ${report.approved ? "good" : "warn"}`}>
        <h2>{report.approved ? "Approved" : "Not approved"}</h2>
        <p className="reasoning">{verdict.memo ?? verdict.reasoning}</p>
        {report.iterations.length > 1 && (
          <p className="muted">
            Took {report.iterations.length} iterations — the Synthesis Agent sent the
            allocation back {report.iterations.length - 1}{" "}
            {report.iterations.length === 2 ? "time" : "times"} before approving.
          </p>
        )}
      </section>

      <section className="metrics">
        <Metric label="Final value" value={money(backtest.final_value)} />
        <Metric label="CAGR" value={pct(m.cagr)} hint="Compound annual growth rate" />
        <Metric label="Max drawdown" value={pct(m.max_drawdown)} hint="Peak to trough" />
        <Metric label="Volatility" value={pct(m.annualized_volatility)} hint="Annualized" />
        <Metric label="Sharpe" value={m.sharpe_ratio.toFixed(2)} />
        <Metric label="Sortino" value={m.sortino_ratio.toFixed(2)} />
      </section>

      <section className="panel">
        <h3>Equity curve</h3>
        <p className="muted">
          {backtest.start} to {backtest.end}, {backtest.rebalance} rebalancing, starting
          from {money(backtest.initial_capital)}. Historical measurement, not a forecast.
        </p>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={equity} margin={{ left: 8, right: 8, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={60} />
            <YAxis
              tick={{ fontSize: 11 }}
              width={70}
              tickFormatter={(v) => `$${(v / 1_000_000).toFixed(1)}M`}
            />
            <Tooltip formatter={(v) => money(asNumber(v))} />
            <Line type="monotone" dataKey="value" stroke="#2563eb" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="panel">
        <h3>Drawdown</h3>
        <p className="muted">
          Measured from the running peak, which is why it is the constraint the
          Allocation Agent cannot satisfy just by reading its instructions.
        </p>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={drawdown} margin={{ left: 8, right: 8, top: 8 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={60} />
            <YAxis tick={{ fontSize: 11 }} width={50} tickFormatter={(v) => `${v.toFixed(0)}%`} />
            <Tooltip formatter={(v) => `${asNumber(v).toFixed(2)}%`} />
            <Area type="monotone" dataKey="value" stroke="#dc2626" fill="#fecaca" />
          </AreaChart>
        </ResponsiveContainer>
      </section>

      <section className="panel">
        <h3>Sector exposure</h3>
        <ResponsiveContainer width="100%" height={Math.max(180, sectors.length * 26)}>
          <BarChart data={sectors} layout="vertical" margin={{ left: 100, right: 24 }}>
            <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
            <YAxis type="category" dataKey="sector" tick={{ fontSize: 11 }} width={100} />
            <Tooltip formatter={(v) => `${asNumber(v).toFixed(2)}%`} />
            <Bar dataKey="weight" radius={[0, 3, 3, 0]}>
              {sectors.map((s) => (
                <Cell key={s.sector} fill="#2563eb" />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </section>

      <section className="panel">
        <h3>Holdings ({positions.length})</h3>
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th className="num">Weight</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.ticker}>
                <td className="ticker">{p.ticker}</td>
                <td className="num">{pct(p.weight)}</td>
                <td className="rationale">{p.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h3>Compliance — deterministic, binding</h3>
        <p className="muted">
          No LLM touches this. If it reports a violation the verdict is `revise`
          regardless of what the Synthesis Agent decided.
        </p>
        {compliance.passed ? (
          <p className="good">Passed all {compliance.checks_run.length} checks.</p>
        ) : (
          <ul className="violations">
            {compliance.violations.map((v) => (
              <li key={v.rule}>
                <code>{v.rule}</code> {v.message}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h3>Screening — RAG-grounded, advisory</h3>
        <p className="muted">
          Every finding must quote a retrieved document verbatim; ungrounded ones are
          dropped before the supervisor sees them
          {screening.ungrounded_findings_dropped > 0 &&
            ` (${screening.ungrounded_findings_dropped} dropped this run)`}
          .
        </p>
        {screening.findings.length === 0 ? (
          <p className="good">No findings across {screening.tickers_screened.length} tickers.</p>
        ) : (
          <ul className="findings">
            {screening.findings.map((f, i) => (
              <li key={i} className={f.recommend_exclusion ? "warn" : ""}>
                <strong>{f.ticker}</strong> — {f.concern}
                <blockquote>
                  “{f.excerpt}” <cite>{f.doc_id}</cite>
                </blockquote>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
