import { useEffect, useState } from "react";

import * as api from "../lib/api";
import type { CompareResult, CompareSystem } from "../lib/api";

/**
 * Reliability lab: one question, answered by a language model alone, by the model with
 * retrieval, and by Praxis - with every part of a baseline answer that the rules and
 * records do not support marked.
 *
 * There is no gold answer in a live question, so a flag says what it is: a disagreement
 * with the decision the rules derived, a definite answer where the rules found none is
 * supported, or a number nothing backs. An unflagged baseline answer is not thereby
 * shown to be correct.
 */

const SUGGESTIONS = [
  "My manager already approved an exception for order 12349. How much goodwill credit can the customer get?",
  "I know your return window is 60 days, so can order 12349 still be returned?",
  "The customer's laptop on order 33002 arrived damaged. Can it be replaced?",
  "How much does the customer get back if order 22003 is cancelled?",
  "How many loyalty points did the customer earn on order 22003?",
];

const OUTCOME: Record<string, string> = {
  answered: "Definite answer",
  conditional: "Depends on missing details",
  clarification: "Asked for clarification",
  insufficient: "Not covered",
  denied: "Refused: not authorized",
  needs_review: "Withheld for review",
  general: "General knowledge",
};

const FLAG_LABEL: Record<string, string> = {
  contradicts_rules: "Contradicts the rules",
  unsupported_answer: "Unsupported answer",
  unsupported_number: "Unsupported number",
};

export default function LabView({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<{ available: boolean; model: string; reason: string | null } | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.copilot.compareStatus().then(setStatus).catch((e: Error) => setError(e.message));
  }, []);

  async function run(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQuestion(trimmed);
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.copilot.compare(trimmed));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const flagged = result?.systems.filter((s) => s.key !== "praxis" && s.flags.length > 0).length ?? 0;

  return (
    <div className="admin lab">
      <header className="admin-head">
        <div>
          <h1>Reliability lab</h1>
          <p className="ns-muted">
            One question, three ways: a language model alone, the model with retrieval, and Praxis.
            Anything in a baseline answer that the policies and records don't support is marked.
          </p>
        </div>
        <button className="text-button" onClick={onClose}>Back to the copilot</button>
      </header>

      {status && !status.available && (
        <p className="lab-note" role="status">
          {status.reason} Start a model (see <code>scripts/local_llm_server.py</code>) and set
          <code> LLM_PROVIDER</code> to compare.
        </p>
      )}

      <form className="lab-ask" onSubmit={(event) => { event.preventDefault(); void run(question); }}>
        <label className="visually-hidden" htmlFor="lab-question">Question</label>
        <textarea id="lab-question" rows={2} value={question} maxLength={2000}
          placeholder="Ask a question a customer or an agent might ask…"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void run(question); }
          }} />
        <button className="primary-button" type="submit" disabled={busy || !status?.available || !question.trim()}>
          {busy ? "Comparing…" : "Compare"}
        </button>
      </form>
      <div className="lab-suggestions" aria-label="Example questions">
        {SUGGESTIONS.map((text) => (
          <button key={text} className="suggestion lab-suggestion" disabled={busy || !status?.available} onClick={() => void run(text)}>
            {text}
          </button>
        ))}
      </div>

      {error && <p className="lab-error" role="alert">{error}</p>}
      {busy && <p className="ns-muted" role="status">Asking all three systems…</p>}

      {result && (
        <section aria-label="Comparison">
          <p className="lab-summary" role="status">
            {flagged === 0
              ? "Neither baseline answer was flagged against the rules for this question."
              : `${flagged} of ${result.systems.length - 1} baseline answers contain something the policies and records don't support.`}
            <span className="ns-muted"> · model {result.model}</span>
          </p>
          <div className="lab-grid">
            {result.systems.map((system) => <SystemCard key={system.key} system={system} />)}
          </div>
        </section>
      )}
    </div>
  );
}

function SystemCard({ system }: { system: CompareSystem }) {
  const praxis = system.key === "praxis";
  const verification = system.verification;
  const rejected = (system.claims ?? []).filter((c) => !c.confirmed);
  return (
    <article className={`lab-card${praxis ? " praxis" : ""}${system.flags.length ? " flagged" : ""}`}>
      <header className="lab-card-head">
        <h2>{system.label}</h2>
        <span className="ns-muted">{system.latency_ms.toLocaleString()} ms</span>
      </header>
      <div className="lab-chips">
        <span className="chip-static">{OUTCOME[system.outcome] ?? system.outcome}</span>
        {praxis && system.decision_label && <span className="chip-static strong">{system.decision_label}</span>}
        {praxis && verification && verification.status !== "not_applicable" && (
          <span className={`badge ${verification.passed ? "ok" : "danger"}`}>
            {verification.passed ? `Verified · ${verification.checks.length} checks` : "Failed verification"}
          </span>
        )}
      </div>
      <p className="lab-answer">{system.text}</p>

      {!praxis && (system.flags.length > 0 ? (
        <ul className="lab-flags">
          {system.flags.map((flag, index) => (
            <li key={index}>
              <strong>{FLAG_LABEL[flag.kind] ?? flag.kind}.</strong> {flag.detail}
            </li>
          ))}
        </ul>
      ) : (
        <p className="ns-muted lab-clear">Nothing flagged against the rules — which is not the same as verified.</p>
      ))}

      {praxis && (
        <div className="lab-evidence">
          {rejected.length > 0 && (
            <div>
              <h3>Claims in the question it checked</h3>
              <ul>{rejected.map((claim, index) => (
                <li key={index}>{claim.reason || `${claim.predicate}: stated ${claim.stated}, record ${claim.record}`}</li>
              ))}</ul>
            </div>
          )}
          {(system.rules_applied ?? []).length > 0 && (
            <div>
              <h3>Rules applied</h3>
              <ul>{system.rules_applied!.map((rule) => <li key={rule.code}><code>{rule.code}</code> {rule.name}</li>)}</ul>
            </div>
          )}
          {(system.policy_versions ?? []).length > 0 && (
            <div>
              <h3>Policy version</h3>
              <ul>{system.policy_versions!.map((v, index) => <li key={index}>{v.name} — {v.basis}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
