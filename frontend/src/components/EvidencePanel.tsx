import { useEffect, useState } from "react";

import type { AssistantTurn, Citation, FactView } from "../lib/types";

interface Props {
  turn: AssistantTurn | null;
  focusCitation: number | null;
  onClose: () => void;
}

type Tab = "sources" | "reasoning" | "claims" | "trace";

const KIND_CLASS: Record<string, string> = {
  DIRECT: "direct",
  INFERRED: "inferred",
  MODEL_GENERATED: "model",
};

function renderValue(object: FactView["object"]): string {
  const value = object?.value;
  const unit = object?.unit ?? "";
  const rendered =
    typeof value === "number" && Number.isInteger(value) ? String(value) : String(value);
  return unit ? `${rendered} ${unit}` : rendered;
}

export default function EvidencePanel({ turn, focusCitation, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("sources");

  useEffect(() => {
    if (focusCitation !== null) setTab("sources");
  }, [focusCitation]);

  useEffect(() => {
    if (focusCitation === null) return;
    const node = document.getElementById(`citation-${focusCitation}`);
    node?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusCitation, tab, turn]);

  if (!turn) {
    return (
      <aside className="evidence-panel">
        <div className="panel-header">
          <h2>Evidence</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close panel">
            ✕
          </button>
        </div>
        <p className="panel-empty">
          Ask something and the sources, facts and reasoning behind the answer appear
          here.
        </p>
      </aside>
    );
  }

  const { citations, reasoning, claims, stages, policy_decision: policy } = turn;
  const quarantined = citations.filter((c) => c.quarantined);

  return (
    <aside className="evidence-panel">
      <div className="panel-header">
        <h2>Why this answer</h2>
        <button className="icon-button" onClick={onClose} aria-label="Close panel">
          ✕
        </button>
      </div>

      <div className="panel-tabs">
        {([
          ["sources", `Sources (${citations.length})`],
          ["reasoning", `Reasoning (${reasoning.inferences.length})`],
          ["claims", `Claims (${claims.length})`],
          ["trace", "Trace"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            className={`panel-tab${tab === key ? " active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="panel-body">
        {tab === "sources" && (
          <>
            {quarantined.length > 0 && (
              <div className="card quarantined">
                <div className="card-head">
                  <span className="badge warn">
                    <span className="badge-dot" /> {quarantined.length} quarantined
                  </span>
                </div>
                <p className="card-text">
                  These passages contain instruction-like text. They are shown as
                  evidence but cannot direct the assistant, and cannot be the only
                  support for a claim.
                </p>
              </div>
            )}
            {citations.length === 0 && (
              <p className="panel-empty">No documents were retrieved for this answer.</p>
            )}
            {citations.map((citation) => (
              <SourceCard key={citation.index} citation={citation}
                          highlighted={citation.index === focusCitation} />
            ))}
          </>
        )}

        {tab === "reasoning" && (
          <>
            {reasoning.conclusion && (
              <div className="panel-group">
                <h3>Conclusion</h3>
                <div className="card">
                  <div className="fact-triple">{reasoning.conclusion}</div>
                </div>
              </div>
            )}

            {reasoning.contradictions.length > 0 && (
              <div className="panel-group">
                <h3>Conflicts</h3>
                {reasoning.contradictions.map((conflict, index) => (
                  <div
                    key={index}
                    className={`card ${conflict.resolved ? "resolved" : "conflict"}`}
                  >
                    <div className="card-head">
                      <span className="card-title">
                        {conflict.subject}.{conflict.predicate}
                      </span>
                      <span className={`badge ${conflict.resolved ? "warn" : "danger"}`}>
                        {conflict.resolved ? "superseded" : "unresolved"}
                      </span>
                    </div>
                    <div className="card-text">
                      Recorded values: {conflict.values.map(String).join(", ")}
                    </div>
                    {conflict.resolution && (
                      <div className="card-meta">{conflict.resolution}</div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div className="panel-group">
              <h3>Inference steps</h3>
              {reasoning.inferences.length === 0 && (
                <p className="panel-empty">
                  No rules were applied; this answer came from retrieval alone.
                </p>
              )}
              {reasoning.inferences.map((step) => (
                <div className="step" key={`${step.rule_code}-${step.step}`}>
                  <span className="step-number">{step.step}</span>
                  <div>
                    <div className="step-rule">
                      {step.rule_code}
                      {step.rule_name && step.rule_name !== step.rule_code
                        ? ` — ${step.rule_name}`
                        : ""}
                    </div>
                    <div className="step-conclusion">
                      {step.sentence || step.conclusion}
                    </div>
                    {step.premises.length > 0 && (
                      <div className="step-premises">
                        because {step.premises.join("; ")}
                      </div>
                    )}
                    {step.rule_text && (
                      <div className="card-meta">
                        <code>{step.rule_text}</code>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="panel-group">
              <h3>Facts used</h3>
              {reasoning.facts.length === 0 && (
                <p className="panel-empty">No structured facts applied.</p>
              )}
              {reasoning.facts.map((fact, index) => (
                <div className="fact-row" key={`${fact.subject}-${fact.predicate}-${index}`}>
                  <span className={`kind-tag ${KIND_CLASS[fact.kind] ?? "direct"}`}>
                    {fact.kind === "MODEL_GENERATED" ? "MODEL" : fact.kind}
                  </span>
                  <span className="fact-triple">
                    {fact.subject}.{fact.predicate} = {renderValue(fact.object)}
                  </span>
                  {fact.citation_index !== null && (
                    <span className="citation-chip">{fact.citation_index}</span>
                  )}
                </div>
              ))}
            </div>

            {reasoning.rules_considered.length > 0 && (
              <div className="panel-group">
                <h3>Rules considered</h3>
                <div className="card-meta">
                  {reasoning.rules_considered.join(", ")}
                </div>
              </div>
            )}
          </>
        )}

        {tab === "claims" && (
          <>
            {claims.length === 0 && <p className="panel-empty">No claims were made.</p>}
            {claims.map((claim, index) => (
              <div className="card" key={index}>
                <div className="card-head">
                  <span className={`kind-tag ${KIND_CLASS[claim.kind] ?? "direct"}`}>
                    {claim.kind === "MODEL_GENERATED" ? "MODEL" : claim.kind}
                  </span>
                  <span className={`badge ${statusTone(claim.status)}`}>
                    {claim.status}
                  </span>
                </div>
                <div className="card-text">{claim.text}</div>
                <div className="card-meta">
                  <span>support {claim.support_score.toFixed(2)}</span>
                  {claim.citation_indexes.length > 0 && (
                    <span>
                      cites {claim.citation_indexes.map((i) => `[${i}]`).join(" ")}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </>
        )}

        {tab === "trace" && (
          <>
            <div className="panel-group">
              <h3>Decision</h3>
              <dl className="kv">
                <dt>Route</dt>
                <dd>{turn.route}</dd>
                <dt>Gate</dt>
                <dd>{turn.gate_decision}</dd>
                <dt>Verification</dt>
                <dd>{turn.verification_status}</dd>
                <dt>Confidence</dt>
                <dd>{(turn.confidence * 100).toFixed(0)}%</dd>
                <dt>Pipeline</dt>
                <dd>{turn.pipeline_mode}</dd>
                <dt>Latency</dt>
                <dd>{turn.latency_ms} ms</dd>
              </dl>
            </div>

            {policy && (
              <div className="panel-group">
                <h3>Policy decision</h3>
                <dl className="kv">
                  <dt>Decision</dt>
                  <dd>{policy.decision}</dd>
                  <dt>Reason</dt>
                  <dd>{policy.reason}</dd>
                  <dt>Policy</dt>
                  <dd>{policy.matched_policy ?? "none matched"}</dd>
                </dl>
              </div>
            )}

            <div className="panel-group">
              <h3>Stage timings</h3>
              {stages.map((stage) => {
                const max = Math.max(1, ...stages.map((s) => s.ms));
                return (
                  <div className="timing-bar" key={stage.name}>
                    <span className="timing-name">{stage.name}</span>
                    <span className="timing-track">
                      <span
                        className="timing-fill"
                        style={{ width: `${Math.round((stage.ms / max) * 100)}%` }}
                      />
                    </span>
                    <span className="timing-value">{stage.ms} ms</span>
                  </div>
                );
              })}
            </div>

            <p className="card-meta">
              This trace is the auditable record: the plan, the evidence, the
              derivation and the gate's inputs. It is not the model's private
              reasoning, which the system never exposes.
            </p>
          </>
        )}
      </div>
    </aside>
  );
}

function statusTone(status: string): string {
  if (status === "verified") return "ok";
  if (status === "supported") return "info";
  if (status === "contradicted") return "danger";
  if (status === "weak") return "warn";
  return "neutral";
}

function SourceCard({ citation, highlighted }: { citation: Citation; highlighted: boolean }) {
  return (
    <div
      id={`citation-${citation.index}`}
      className={`card${citation.quarantined ? " quarantined" : ""}`}
      style={highlighted ? { outline: "2px solid var(--accent)" } : undefined}
    >
      <div className="card-head">
        <span className={`citation-chip${citation.quarantined ? " quarantined" : ""}`}>
          {citation.index}
        </span>
        <span className="card-title">{citation.document_title}</span>
      </div>
      {citation.section && <div className="card-meta">{citation.section}</div>}
      <div className="card-text">{citation.snippet}</div>
      <div className="card-meta">
        <span>relevance {citation.score.toFixed(2)}</span>
        {citation.page !== null && <span>page {citation.page}</span>}
        {citation.quarantined && <span>quarantined — treated as inert data</span>}
      </div>
    </div>
  );
}
