import { useEffect, useRef, useState } from "react";
import { api, type Constraints, type FeasibilityReport, type Mandate } from "../api";

/**
 * Constraint editor with server-computed range locking.
 *
 * The rule this is built around: **the browser never decides what is legal.**
 * The exact same arithmetic backs the compliance engine, and a widget that
 * reimplemented it would drift — which is precisely the failure that shipped an
 * impossible 30% bond floor against an 8% position cap and burned three live
 * iterations before anyone noticed. So every bound here comes from the API.
 *
 * The whole constraint set is posted on each change rather than one field,
 * because the fields are interdependent: raising `max_holdings` widens the legal
 * range for `max_position_weight`, which in turn widens the reachable
 * asset-class floors. Validating a field in isolation would show stale bounds.
 */

const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

interface Props {
  base: Mandate;
  onRun: (constraints: Constraints) => void;
  disabled: boolean;
  runLabel: string;
}

/** A slider bounded by the server's answer, disabled when it has no freedom. */
function BoundedSlider({
  label,
  value,
  range,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  value: number;
  range?: { lower: number | null; upper: number | null; locked: boolean; note: string | null };
  min: number;
  max: number;
  step: number;
  format: (n: number) => string;
  onChange: (n: number) => void;
}) {
  // Fall back to the widest plausible span until the first response arrives, so
  // controls are never silently narrower than the server would allow.
  const lower = range?.lower ?? min;
  const upper = range?.upper ?? max;
  const locked = range?.locked ?? false;
  const outOfRange = value < lower - 1e-9 || value > upper + 1e-9;

  return (
    <div className={`control ${locked ? "locked" : ""}`}>
      <label>
        <span className="control-label">{label}</span>
        <span className={`control-value ${outOfRange ? "bad" : ""}`}>{format(value)}</span>
      </label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={locked}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <p className="control-hint">
        {locked ? (
          <strong>No room left — every other constraint pins this one.</strong>
        ) : (
          <>
            Allowed {format(lower)}–{format(upper)}.
          </>
        )}
        {range?.note && <> {range.note}</>}
      </p>
    </div>
  );
}

export function MandateBuilder({ base, onRun, disabled, runLabel }: Props) {
  const [constraints, setConstraints] = useState<Constraints>(base.constraints);
  const [report, setReport] = useState<FeasibilityReport | null>(null);
  const [checking, setChecking] = useState(false);
  const latest = useRef(0);

  // Reset when the preset changes — the presets are the starting points, and an
  // edit carried across them would silently mix two mandates.
  useEffect(() => {
    setConstraints(base.constraints);
  }, [base]);

  useEffect(() => {
    // Debounced, and risk bounds are requested only after the pause: the cheap
    // arithmetic is worth running eagerly, a backtest is not.
    const seq = ++latest.current;
    setChecking(true);
    const timer = setTimeout(() => {
      api
        .checkFeasibility(constraints, true)
        .then((r) => {
          // Ignore a response overtaken by a newer edit, or a slow risk-bound
          // call would clobber fresher arithmetic.
          if (seq === latest.current) setReport(r);
        })
        .catch(() => undefined)
        .finally(() => {
          if (seq === latest.current) setChecking(false);
        });
    }, 350);
    return () => clearTimeout(timer);
  }, [constraints]);

  const ranges = report?.ranges ?? {};
  const set = (patch: Partial<Constraints>) =>
    setConstraints((c) => ({ ...c, ...patch }));

  const setBand = (bucket: string, patch: { min_weight?: number; max_weight?: number }) =>
    setConstraints((c) => ({
      ...c,
      asset_class_bands: {
        ...c.asset_class_bands,
        [bucket]: { ...c.asset_class_bands[bucket], ...patch },
      },
    }));

  return (
    <section className="panel builder">
      <h2>Constraints</h2>
      <p className="muted">
        Every bound below is computed server-side by the same arithmetic the
        compliance engine uses. A control with no freedom left is disabled rather
        than left to fail at run time.
      </p>

      <div className="controls-grid">
        <BoundedSlider
          label="Max position weight"
          value={constraints.max_position_weight}
          range={ranges["max_position_weight"]}
          min={0.01}
          max={1}
          step={0.005}
          format={pct}
          onChange={(v) => set({ max_position_weight: v })}
        />
        <BoundedSlider
          label="Max sector weight"
          value={constraints.max_sector_weight}
          range={ranges["max_sector_weight"]}
          min={0.05}
          max={1}
          step={0.01}
          format={pct}
          onChange={(v) => set({ max_sector_weight: v })}
        />
        <BoundedSlider
          label="Min holdings"
          value={constraints.min_holdings}
          range={ranges["min_holdings"]}
          min={1}
          max={52}
          step={1}
          format={(n) => `${n}`}
          onChange={(v) => set({ min_holdings: v })}
        />
        <BoundedSlider
          label="Max holdings"
          value={constraints.max_holdings}
          range={ranges["max_holdings"]}
          min={1}
          max={52}
          step={1}
          format={(n) => `${n}`}
          onChange={(v) => set({ max_holdings: v })}
        />

        {Object.entries(constraints.asset_class_bands).map(([bucket, band]) => (
          <BoundedSlider
            key={bucket}
            label={`${bucket.replace("_", " ")} floor`}
            value={band.min_weight}
            range={ranges[`asset_class_bands.${bucket}.min_weight`]}
            min={0}
            max={1}
            step={0.01}
            format={pct}
            onChange={(v) => setBand(bucket, { min_weight: v })}
          />
        ))}

        {/* Post-execution: no numeric bound exists, only evidence of what one
            constructed portfolio achieved. Rendered as a plain slider with the
            witness in the hint — never disabled, because a tighter target is
            unproven rather than impossible. */}
        <BoundedSlider
          label="Max drawdown tolerance"
          value={constraints.max_drawdown_tolerance ?? 0.25}
          range={ranges["max_drawdown_tolerance"]}
          min={0.02}
          max={0.6}
          step={0.005}
          format={(n) => `${(n * 100).toFixed(1)}%`}
          onChange={(v) => set({ max_drawdown_tolerance: v })}
        />
        <BoundedSlider
          label="Max volatility"
          value={constraints.max_volatility ?? 0.15}
          range={ranges["max_volatility"]}
          min={0.02}
          max={0.5}
          step={0.005}
          format={(n) => `${(n * 100).toFixed(1)}%`}
          onChange={(v) => set({ max_volatility: v })}
        />
      </div>

      {report && !report.feasible && (
        <div className="error">
          <strong>These constraints cannot be satisfied.</strong>
          <ul>
            {report.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="builder-actions">
        <button
          onClick={() => onRun(constraints)}
          disabled={disabled || checking || (report !== null && !report.feasible)}
        >
          {runLabel}
        </button>
        <span className="muted">
          {checking
            ? "Checking feasibility…"
            : report?.feasible
              ? "Feasible — every constraint is reachable in this universe."
              : ""}
        </span>
      </div>
    </section>
  );
}
