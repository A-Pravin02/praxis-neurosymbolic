import { useState } from "react";

import type { NeuroSymbolicAnswer, NsLayer, NsRule } from "../lib/types";

const OUTCOME: Record<string, { label: string; tone: string }> = {
  answered: { label: "Determined by company rules", tone: "ok" },
  conditional: { label: "Depends on details not given", tone: "warn" },
  clarification: { label: "Needs clarification", tone: "info" },
  insufficient: { label: "Not covered by company knowledge", tone: "info" },
  denied: { label: "Access refused", tone: "danger" },
  needs_review: { label: "Withheld for review", tone: "danger" },
  // Answered, but from the model's own knowledge. The kicker has to say so: this is the
  // one card on which nothing was checked against a policy, a rule or a record.
  general: { label: "General knowledge · not company-verified", tone: "info" },
};

const LAYER_HINT: Record<NsLayer, string> = {
  NEURAL: "Neural: understanding language by meaning",
  SYMBOLIC: "Symbolic: rules and records, evaluated exactly",
  HYBRID: "Hybrid: neural and symbolic working together",
};


/* -------------------------------------------------------------------------------------
 * The decision card: what an agent reads first.
 *
 * Decision, recommended action, verification and confidence are always visible. Evidence,
 * the rules and "why" are one click away. The audit trace - candidate goals, similarity
 * scores, condition-by-condition rule evaluation - is shown only to support and
 * administrative roles: a customer needs the answer and its reason, not the machinery.
 * ----------------------------------------------------------------------------------- */

const KIND_TONE: Record<string, string> = {
  yes_no: "ok", amount: "ok", recommendation: "ok", list: "ok", record: "info",
  information: "info", needs_information: "warn", clarification: "info",
  not_covered: "info", access_refused: "danger", needs_review: "danger",
};

export function DecisionCard({
  ns, latencyMs, onChoose, busy,
}: { ns: NeuroSymbolicAnswer; latencyMs: number; onChoose: (text: string) => void; busy: boolean }) {
  const verification = ns.verification;
  const outcome = OUTCOME[ns.outcome] ?? { label: ns.outcome, tone: "neutral" };
  const label = ns.decision_label || outcome.label;
  const tone = ns.outcome === "answered" && ns.decision_label === "No" ? "warn"
    : KIND_TONE[ns.decision_kind ?? ""] ?? outcome.tone;
  const decided = ["answered", "conditional"].includes(ns.outcome);
  const rejected = (ns.claims ?? []).filter((claim) => !claim.confirmed);
  const unverifiedClaims = (ns.trace.grounding.rejected_statements ?? [])
    .filter((r) => r.kind === "unverified claim");
  const version = (ns.policy_versions ?? []).find((v) => !v.assumed);
  const staff = ns.audience === "staff";

  return (
    <section className="decision-card" aria-label="Decision">
      <div className="decision-head">
        <div className={`decision-label ${tone}`}>
          <span className="decision-kicker">{outcome.label}</span>
          <span className="decision-value">{label}</span>
        </div>
        <div className="decision-meta">
          {verification && verification.status !== "not_applicable" && (
            <span className={`badge ${verification.passed ? "ok" : "danger"}`}
              title={verification.checks.map((c) => `${c.passed ? "✓" : "✕"} ${c.check}`).join("\n")}>
              <span className="badge-dot" />
              {verification.passed ? `Verified · ${verification.checks.length} checks` : "Needs review"}
            </span>
          )}
          {decided && <Confidence value={ns.confidence} />}
          <span className="badge neutral" title={ns.trace.pipeline_reason}>{ns.pipeline_label}</span>
          {latencyMs > 0 && <span className="badge neutral">{latencyMs} ms</span>}
        </div>
      </div>

      {ns.recommended_action && (
        <div className="decision-action">
          <span className="decision-action-label">Recommended action</span>
          <p>{ns.recommended_action}</p>
        </div>
      )}

      {(rejected.length > 0 || unverifiedClaims.length > 0) && (
        <div className="decision-callout warn" role="note">
          <strong>Premise checked and rejected.</strong>{" "}
          {rejected.map((claim) => (
            <span key={claim.predicate}>
              The claim of {claim.stated} does not match {claim.source}, which sets {claim.record}.{" "}
            </span>
          ))}
          {unverifiedClaims.length > 0 && <span>A stated approval has no matching record.</span>}
        </div>
      )}

      {ns.knowledge_gap && ns.knowledge_gap.type !== "unclear_request" && (
        <div className="decision-callout info" role="note">
          <strong>Knowledge gap logged.</strong> {ns.knowledge_gap.summary}.
          {ns.knowledge_gap.owner && <span className="ns-muted"> Owner: {ns.knowledge_gap.owner}.</span>}
        </div>
      )}

      <ClarificationOptions ns={ns} onChoose={onChoose} disabled={busy} />

      <div className="decision-sections">
        {(ns.explanation.length > 0 || (ns.rules_applied ?? []).length > 0) && (
          <details className="decision-section">
            <summary>Why this answer?</summary>
            <ul className="decision-why">
              {ns.explanation.map((line, index) => <li key={index}>{line}</li>)}
            </ul>
            {version && (
              <p className="ns-muted">Policy version applied: {version.name} — {version.basis}.</p>
            )}
            {(ns.rules_applied ?? []).length > 0 && (
              <>
                <div className="decision-subhead">Rules applied</div>
                {(ns.rules_applied ?? []).map((rule) => (
                  <div className="rule-summary" key={rule.rule}>
                    <div className="rule-summary-head">
                      <code>{rule.rule}</code> {rule.name}
                      {rule.clauses.map((c) => <span key={c} className="ns-pill">{c}</span>)}
                    </div>
                    {rule.formula && <div className="rule-formula"><code>{rule.formula}</code></div>}
                  </div>
                ))}
              </>
            )}
          </details>
        )}

        {ns.sources.length > 0 && (
          <details className="decision-section">
            <summary>Evidence ({ns.sources.length})</summary>
            {ns.sources.map((source) => (
              <div className="source-row" key={`${source.type}-${source.id}-${source.version ?? ""}`}>
                <span className={`ns-source-kind ${source.type}`}>
                  {source.type === "policy" ? source.id : "record"}
                </span>
                <div>
                  <div className="source-title">
                    {source.title}
                    {source.type === "record" && <span className="ns-muted"> · {source.id}</span>}
                    {source.type === "policy" && source.version && (
                      <span className={`ns-pill${source.in_force === false ? " superseded" : ""}`}>
                        v{source.version}{source.in_force === false ? " · superseded" : ""}
                      </span>
                    )}
                  </div>
                  {source.text && <div className="source-snippet">{source.text}</div>}
                  {source.document && (
                    <div className="source-meta">
                      {source.document}{source.effective ? ` · effective ${source.effective}` : ""}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </details>
        )}

        {verification && verification.checks.length > 0 && (
          <details className="decision-section">
            <summary>Verification</summary>
            <ul className="ns-checks">
              {verification.checks.map((check) => (
                <li key={check.check} className={check.passed ? "pass" : "fail"}>
                  <span aria-hidden="true">{check.passed ? "✓" : "✕"}</span>
                  <strong>{check.check}</strong> — {check.detail}
                </li>
              ))}
            </ul>
          </details>
        )}

        {staff && <HowDetermined ns={ns} />}
      </div>
    </section>
  );
}

function Confidence({ value }: { value: number }) {
  const percent = Math.round(value * 100);
  return (
    <span className="confidence-meter">
      confidence
      <meter className="confidence-gauge" min={0} max={100} low={45} high={70} optimum={100}
        value={percent} aria-label={`Confidence ${percent}%`} />
      {percent}%
    </span>
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


export function HowDetermined({ ns }: { ns: NeuroSymbolicAnswer }) {
  const [showTrace, setShowTrace] = useState(false);
  const trace = ns.trace;
  return (
    <details className="ns-how">
      <summary>Audit trace</summary>

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
