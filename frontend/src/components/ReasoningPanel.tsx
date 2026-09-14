import { useState } from "react";

import type { NeuroSymbolicAnswer, NsLayer, NsRule } from "../lib/types";

const OUTCOME: Record<string, { label: string; tone: string }> = {
  answered: { label: "Determined by company rules", tone: "ok" },
  conditional: { label: "Depends on details not given", tone: "warn" },
  clarification: { label: "Needs clarification", tone: "info" },
  insufficient: { label: "Not covered by company knowledge", tone: "info" },
  denied: { label: "Access refused", tone: "danger" },
};

const LAYER_HINT: Record<NsLayer, string> = {
  NEURAL: "Neural: understanding language by meaning",
  SYMBOLIC: "Symbolic: rules and records, evaluated exactly",
  HYBRID: "Hybrid: neural and symbolic working together",
};

export function NsStatus({ ns, latencyMs }: { ns: NeuroSymbolicAnswer; latencyMs: number }) {
  const outcome = OUTCOME[ns.outcome] ?? { label: ns.outcome, tone: "neutral" };
  const showConfidence = ["answered", "conditional"].includes(ns.outcome);
  const colour = ns.confidence >= 0.7 ? "var(--ok)" : ns.confidence >= 0.45 ? "var(--warn)" : "var(--danger)";
  return (
    <div className="turn-status">
      <span className={`badge ${outcome.tone}`}>
        <span className="badge-dot" /> {outcome.label}
      </span>
      <span className="badge pipeline-badge" title={ns.trace.pipeline_reason}>
        Pipeline {ns.pipeline} · {ns.pipeline_label}
      </span>
      {showConfidence && (
        <span className="confidence-meter">
          confidence
          <span className="confidence-track">
            <span className="confidence-fill"
              style={{ width: `${Math.round(ns.confidence * 100)}%`, background: colour }} />
          </span>
          {(ns.confidence * 100).toFixed(0)}%
        </span>
      )}
      {latencyMs > 0 && <span className="badge neutral">{latencyMs} ms</span>}
    </div>
  );
}

export function ClarificationOptions({
  ns, onChoose, disabled,
}: { ns: NeuroSymbolicAnswer; onChoose: (text: string) => void; disabled: boolean }) {
  if (!ns.clarification?.options.length) return null;
  const isRecord = ns.clarification.options.some((o) => /^[A-Z]{3}-\d+$/.test(o.id));
  return (
    <div className="ns-options" role="group" aria-label={ns.clarification.question}>
      {ns.clarification.options.map((option) => (
        <button key={option.id} className="ns-option" disabled={disabled}
          onClick={() => onChoose(isRecord
            ? `${ns.trace.question.replace(/[?.!]\s*$/, "")} — I mean ${option.id}`
            : option.label)}>
          <span className="ns-option-label">{option.label}</span>
          {(option.detail || isRecord) && (
            <span className="ns-option-detail">{[isRecord ? option.id : "", option.detail]
              .filter(Boolean).join(" · ")}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export function KnowledgeUsed({ ns }: { ns: NeuroSymbolicAnswer }) {
  if (!ns.sources.length) return null;
  return (
    <details className="sources">
      <summary>Sources &amp; knowledge used ({ns.sources.length})</summary>
      {ns.sources.map((source) => (
        <div className="source-row" key={`${source.type}-${source.id}`}>
          <span className={`ns-source-kind ${source.type}`}>
            {source.type === "policy" ? source.id : "record"}
          </span>
          <div>
            <div className="source-title">
              {source.title}
              {source.type === "record" && <span className="ns-muted"> · {source.id}</span>}
            </div>
            {source.text && <div className="source-snippet">{source.text}</div>}
            {source.document && <div className="source-meta">{source.document}</div>}
          </div>
        </div>
      ))}
    </details>
  );
}

export function HowDetermined({ ns }: { ns: NeuroSymbolicAnswer }) {
  const [showTrace, setShowTrace] = useState(false);
  const trace = ns.trace;
  return (
    <details className="ns-how">
      <summary>How was this determined?</summary>

      <ol className="ns-steps">
        {ns.explanation.map((step, index) => <li key={index}>{step}</li>)}
      </ol>

      <div className="ns-why">
        <div className="ns-why-label">Why pipeline {trace.selected_pipeline} ({trace.pipeline_label})</div>
        <p>{trace.pipeline_reason}</p>
        {trace.why_symbolic && (
          <p className="ns-muted"><strong>Why symbolic reasoning:</strong> {trace.why_symbolic}</p>
        )}
      </div>

      <div className="ns-pipeline" aria-label="Pipeline stages">
        {trace.components.map((component, index) => (
          <span key={index} className={`ns-stage ${component.layer.toLowerCase()}`}
            title={LAYER_HINT[component.layer]}>
            <span className="ns-stage-layer">{component.layer}</span>
            {component.stage}
          </span>
        ))}
      </div>

      <button className="text-button ns-trace-toggle" onClick={() => setShowTrace((v) => !v)}
        aria-expanded={showTrace}>
        {showTrace ? "Hide" : "Show"} the full reasoning trace
      </button>
      {showTrace && <FullTrace ns={ns} />}
    </details>
  );
}

function FullTrace({ ns }: { ns: NeuroSymbolicAnswer }) {
  const t = ns.trace;
  const interp = t.neural_interpretation;
  const top = interp.intent_candidates.slice(0, 4);
  const maxScore = Math.max(...top.map((c) => c.score), 0.01);
  return (
    <div className="ns-trace">
      <section>
        <h4><span className="ns-layer-tag neural">NEURAL</span> Understanding</h4>
        <p className="ns-muted">
          Encoder <code>{interp.encoder}</code>
          {!interp.semantic_understanding && " — degraded: lexical fallback, paraphrases not understood"}
          {" · "}request type <strong>{interp.speech_act.label}</strong>
        </p>
        <table className="ns-table">
          <thead><tr><th>Candidate goal</th><th>Kind</th><th>Similarity</th></tr></thead>
          <tbody>
            {top.map((candidate) => (
              <tr key={candidate.name}>
                <td><code>{candidate.name}</code></td>
                <td>{candidate.goal_kind}</td>
                <td>
                  <span className="ns-bar"><span style={{ width: `${(candidate.score / maxScore) * 100}%` }} /></span>
                  {candidate.score.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(t.entities.length > 0 || interp.stated_inputs.length > 0) && (
          <ul className="ns-list">
            {t.entities.map((entity, index) => (
              <li key={`e${index}`}>
                <span className="ns-pill">{entity.type}</span> “{entity.text}”
                {entity.id && <> → <code>{entity.id}</code></>} <span className="ns-muted">({entity.method})</span>
              </li>
            ))}
            {interp.stated_inputs.map((input, index) => (
              <li key={`i${index}`}>
                <span className="ns-pill">stated</span> <code>{input.predicate}</code> = <code>{String(input.value)}</code>
                <span className="ns-muted"> from “{input.evidence}” ({input.method}, {input.confidence.toFixed(2)})</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {t.symbolic_facts.length > 0 && (
        <section>
          <h4><span className="ns-layer-tag symbolic">SYMBOLIC</span> Facts</h4>
          <table className="ns-table">
            <thead><tr><th>Fact</th><th>Value</th><th>Source</th></tr></thead>
            <tbody>
              {t.symbolic_facts.map((fact, index) => (
                <tr key={index}>
                  <td><code>{fact.predicate}({fact.subject})</code></td>
                  <td>{fact.value}</td>
                  <td><span className={`ns-pill ${fact.source}`}>{fact.source}{fact.rule ? ` · ${fact.rule}` : ""}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {t.rules_used.length > 0 && (
        <section>
          <h4><span className="ns-layer-tag symbolic">SYMBOLIC</span> Rules applied</h4>
          {t.rules_used.map((rule) => <RuleCard key={rule.rule} rule={rule} />)}
        </section>
      )}

      {t.rules_considered.length > 0 && (
        <section>
          <h4><span className="ns-layer-tag symbolic">SYMBOLIC</span> Rules that did not apply</h4>
          {t.rules_considered.map((rule) => <RuleCard key={rule.rule} rule={rule} />)}
        </section>
      )}

      {t.neural_symbolic_loop.length > 0 && (
        <section>
          <h4><span className="ns-layer-tag hybrid">HYBRID</span> Neural-symbolic loop</h4>
          <ul className="ns-list">
            {t.neural_symbolic_loop.map((step) => (
              <li key={step.iteration}>
                Round {step.iteration}: the rules needed <code>{step.symbolic_needed.join(", ")}</code>;
                the neural layer looked again and {step.accepted.length
                  ? <>found <code>{step.accepted.join(", ")}</code></>
                  : "found nothing stated"}.
              </li>
            ))}
          </ul>
        </section>
      )}

      {(t.grounding.rejected_statements?.length ?? 0) > 0 && (
        <section>
          <h4><span className="ns-layer-tag hybrid">HYBRID</span> Statements checked against records</h4>
          <ul className="ns-list">
            {t.grounding.rejected_statements!.map((r, index) => (
              <li key={index}>You said <code>{r.predicate}</code> = {r.stated}; the record shows {r.record}. The record was used.</li>
            ))}
          </ul>
        </section>
      )}

      {t.verification.checks && t.verification.checks.length > 0 && (
        <section>
          <h4><span className="ns-layer-tag hybrid">HYBRID</span> Verification</h4>
          <ul className="ns-checks">
            {t.verification.checks.map((check) => (
              <li key={check.check} className={check.passed ? "pass" : "fail"}>
                <span aria-hidden="true">{check.passed ? "✓" : "✕"}</span>
                <strong>{check.check}</strong> — {check.detail}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function RuleCard({ rule }: { rule: NsRule }) {
  return (
    <div className={`ns-rule ${rule.status ?? ""}`}>
      <div className="ns-rule-head">
        <code>{rule.rule}</code> {rule.name}
        {rule.clauses.map((clause) => <span key={clause} className="ns-pill">{clause}</span>)}
        {rule.status && <span className={`ns-pill status-${rule.status}`}>{rule.status}</span>}
      </div>
      {rule.because && <div className="ns-rule-because">{rule.because}</div>}
      {rule.failed?.note && <div className="ns-rule-failed">Stopped at: {rule.failed.note}</div>}
      {rule.conditions && rule.conditions.length > 0 && (
        <ul className="ns-conditions">
          {rule.conditions.map((condition) => (
            <li key={condition.index} className={condition.status}>
              <span aria-hidden="true">
                {condition.status === "satisfied" ? "✓" : condition.status === "violated" ? "✕" : "?"}
              </span>
              <code>{condition.condition}</code>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
