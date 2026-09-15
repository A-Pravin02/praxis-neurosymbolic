import { useCallback, useEffect, useMemo, useState } from "react";

import * as api from "../lib/api";
import type { CopilotEvaluationRun, CopilotValidation } from "../lib/api";
import type { NeuroSymbolicAnswer } from "../lib/types";
import { DecisionCard } from "./ReasoningPanel";

/**
 * Knowledge administration and reliability analytics for the Support Decision Copilot.
 *
 * The enterprise changes its policies, rules and records here - without changing code.
 * Every edit is validated against the whole knowledge pack on the server before it is
 * written, and every write is snapshotted so it can be restored.
 */

type Tab = "overview" | "reliability" | "gaps" | "review" | "knowledge" | "graph" | "tester" | "access";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "reliability", label: "Reliability" },
  { id: "gaps", label: "Knowledge gaps" },
  { id: "review", label: "Review queue" },
  { id: "knowledge", label: "Policies & rules" },
  { id: "graph", label: "Knowledge graph" },
  { id: "tester", label: "Rule tester" },
  { id: "access", label: "Access" },
];

export default function AdminView({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="admin">
      <header className="admin-head">
        <div>
          <h1>Knowledge &amp; reliability</h1>
          <p className="ns-muted">Policies, rules and records the copilot reasons over — and how well it does.</p>
        </div>
        <button className="text-button" onClick={onClose}>Back to the copilot</button>
      </header>
      <div className="admin-tabs" role="tablist" aria-label="Administration sections">
        {TABS.map((item) => (
          <button key={item.id} role="tab" id={`tab-${item.id}`} aria-selected={tab === item.id}
            aria-controls={`panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1}
            className={`admin-tab${tab === item.id ? " active" : ""}`}
            onClick={() => setTab(item.id)}
            onKeyDown={(event) => {
              const index = TABS.findIndex((t) => t.id === tab);
              const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
              const next = step ? TABS[(index + step + TABS.length) % TABS.length] : undefined;
              if (next) setTab(next.id);
            }}>
            {item.label}
          </button>
        ))}
      </div>
      <div className="admin-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "overview" && <Overview />}
        {tab === "reliability" && <Reliability />}
        {tab === "gaps" && <Gaps />}
        {tab === "review" && <Review />}
        {tab === "knowledge" && <KnowledgeFiles />}
        {tab === "graph" && <Graph />}
        {tab === "tester" && <RuleTester />}
        {tab === "access" && <Access />}
      </div>
    </div>
  );
}

// -- shared ---------------------------------------------------------------------------------

function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    setError(null);
    load().then(setData).catch((caught) =>
      setError(caught instanceof api.RequestError ? caught.message : "Could not load this section."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, reload };
}

function Loading({ error }: { error: string | null }) {
  return error ? <div className="error-banner" role="alert">{error}</div>
    : <p className="ns-muted">Loading…</p>;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

const pct = (value: unknown) => typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
const num = (value: unknown) => typeof value === "number" ? String(value) : "—";

// -- overview -------------------------------------------------------------------------------

function Overview() {
  const { data, error } = useLoad(() => api.copilot.overview());
  const analytics = useLoad(() => api.copilot.analytics());
  if (!data) return <Loading error={error} />;
  const a = analytics.data ?? {};
  return (
    <>
      <div className="stat-grid">
        <Stat label="Policy documents" value={num(data.counts.policies)} hint="including superseded versions" />
        <Stat label="Executable rules" value={num(data.counts.rules)} />
        <Stat label="Decision goals" value={num(data.counts.goals)} />
        <Stat label="Knowledge-graph entities" value={num(data.counts.entities)} />
      </div>
      <h3>Live usage</h3>
      <div className="stat-grid">
        <Stat label="Decisions served" value={num(a.turns)} hint="assistant turns in this workspace" />
        <Stat label="Released answers verified" value={pct(a.verified_rate)} />
        <Stat label="Withheld for review" value={pct(a.withheld_rate)} />
        <Stat label="Needed clarification" value={pct(a.clarification_rate)} />
        <Stat label="Not covered (gaps)" value={pct(a.not_covered_rate)} />
        <Stat label="Escalated for approval" value={pct(a.approval_escalation_rate)} />
        <Stat label="False premises rejected" value={num(a.claims_rejected)} />
        <Stat label="Median decision latency" value={a.median_latency_ms ? `${a.median_latency_ms} ms` : "—"} />
      </div>
      <p className="ns-muted">
        Knowledge revision <code>{data.revision}</code> · records as of {data.as_of}. Usage figures are
        measured from this workspace's own traffic; benchmark figures are on the Reliability tab.
        Handle-time and first-contact-resolution need a pilot with real agents — see the pilot
        protocol in the documentation.
      </p>
      <h3>What the copilot can decide</h3>
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Goal</th><th>About</th><th>Kind</th><th>Description</th></tr></thead>
          <tbody>
            {data.goals.map((goal) => (
              <tr key={goal.name}><td><code>{goal.name}</code></td><td>{goal.subject}</td>
                <td>{goal.kind}</td><td>{goal.description}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// -- reliability ------------------------------------------------------------------------------

const RELIABILITY_METRICS: [string, string][] = [
  ["final_answer_accuracy", "Final answer accuracy"],
  ["decision_accuracy", "Decision accuracy"],
  ["calculation_accuracy", "Calculation accuracy"],
  ["policy_compliance", "Policy compliance"],
  ["hallucination_rate", "Hallucination rate (lower is better)"],
  ["unsupported_answer_rate", "Unsupported-answer rate (lower is better)"],
];

function Reliability() {
  const { data, error } = useLoad(() => api.copilot.evaluation());
  const [selected, setSelected] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  if (!data.runs.length) return <p className="ns-muted">No benchmark runs are recorded yet.</p>;
  const copilotRuns = data.runs.filter((r) => r.split === "copilot");
  const run = data.runs.find((r) => r.file === selected)
    ?? copilotRuns[copilotRuns.length - 1] ?? data.runs[data.runs.length - 1];
  if (!run) return null;
  return (
    <>
      <label className="field-inline">
        Benchmark run{" "}
        <select value={run.file} onChange={(event) => setSelected(event.target.value)}>
          {data.runs.map((r) => <option key={r.file} value={r.file}>{r.label}</option>)}
        </select>
      </label>
      <RunTable run={run} />
    </>
  );
}

function RunTable({ run }: { run: CopilotEvaluationRun }) {
  const systems = Object.keys(run.systems);
  const categories = useMemo(() => Array.from(new Set(
    systems.flatMap((s) => Object.keys(run.by_category[s] ?? {})))).sort(), [run, systems]);
  return (
    <>
      <p className="ns-muted">
        {run.cases} questions · split <code>{run.split}</code>
        {run.frozen ? " · written and committed before the first run" : " · post-hoc: run after changes informed by earlier results"}
        {run.llm_model ? ` · model ${run.llm_model}` : ""}
      </p>
      {run.note && <p className="ns-muted">{run.note}</p>}
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Metric</th>{systems.map((s) => <th key={s}>{s}</th>)}</tr></thead>
          <tbody>
            {RELIABILITY_METRICS.map(([key, label]) => (
              <tr key={key}><td>{label}</td>
                {systems.map((s) => <td key={s}>{pct(run.systems[s]?.[key])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.entries(run.not_run).map(([name, reason]) => (
        <p key={name} className="ns-muted"><code>{name}</code> not run: {reason}.</p>
      ))}
      <h3>Final answer accuracy by category</h3>
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Category</th>{systems.map((s) => <th key={s}>{s}</th>)}</tr></thead>
          <tbody>
            {categories.map((category) => (
              <tr key={category}><td>{category.replace(/_/g, " ")}</td>
                {systems.map((s) => <td key={s}>{pct(run.by_category[s]?.[category])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// -- gaps and review -----------------------------------------------------------------------------

function Gaps() {
  const { data, error } = useLoad(() => api.copilot.gaps());
  if (!data) return <Loading error={error} />;
  if (!data.length) return <p className="ns-muted">No knowledge gaps have been recorded.</p>;
  return (
    <div className="gap-list">
      {data.map((gap) => (
        <article key={`${gap.type}-${gap.summary}`} className="gap">
          <div className="gap-head">
            <span className="ns-pill">{gap.type.replace(/_/g, " ")}</span>
            <strong>{gap.summary}</strong>
            <span className="badge neutral">{gap.count}×</span>
          </div>
          <p>{gap.detail}</p>
          {gap.owner && <p className="ns-muted">Owner: {gap.owner}</p>}
          {gap.examples.length > 0 && (
            <ul className="ns-list">{gap.examples.map((e) => <li key={e.message_id}>“{e.question}”</li>)}</ul>
          )}
        </article>
      ))}
    </div>
  );
}

function Review() {
  const { data, error } = useLoad(() => api.copilot.review());
  const [open, setOpen] = useState<NeuroSymbolicAnswer | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  if (!data.length) return <p className="ns-muted">Nothing needs review.</p>;
  async function inspect(id: string) {
    setOpenError(null);
    try {
      setOpen((await api.copilot.trace(id)).neurosymbolic);
    } catch {
      setOpenError("Could not load that trace.");
    }
  }
  return (
    <>
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Question</th><th>Outcome</th><th>Why it is here</th><th /></tr></thead>
          <tbody>
            {data.map((item) => (
              <tr key={item.message_id}>
                <td>{item.question || <span className="ns-muted">(question unavailable)</span>}</td>
                <td>{item.decision || item.outcome}</td>
                <td>{item.reasons.join("; ")}{item.gap ? ` — ${item.gap}` : ""}</td>
                <td><button className="text-button" onClick={() => void inspect(item.message_id)}>Trace</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {openError && <div className="error-banner" role="alert">{openError}</div>}
      {open && (
        <div className="admin-trace">
          <div className="admin-trace-head">
            <strong>Reasoning trace</strong>
            <button className="text-button" onClick={() => setOpen(null)}>Close</button>
          </div>
          <DecisionCard ns={{ ...open, audience: "staff" }} latencyMs={0} onChoose={() => undefined} busy />
        </div>
      )}
    </>
  );
}

// -- policies and rules ------------------------------------------------------------------------------

function KnowledgeFiles() {
  const policies = useLoad(() => api.copilot.policies());
  const history = useLoad(() => api.copilot.history());
  const [target, setTarget] = useState<{ folder: string; name: string } | null>(null);
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [note, setNote] = useState("");
  const [validation, setValidation] = useState<CopilotValidation | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function open(folder: string, name: string) {
    setValidation(null);
    setStatus(null);
    const file = await api.copilot.readFile(folder, name);
    setTarget({ folder, name });
    setContent(file.content);
    setOriginal(file.content);
  }

  async function check() {
    if (!target) return;
    setBusy(true);
    try {
      setValidation(await api.copilot.validate(target.folder, target.name, content));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!target) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await api.copilot.writeFile(target.folder, target.name, content, note);
      setOriginal(content);
      setNote("");
      setStatus(`Saved as revision ${result.revision}. The copilot uses it from the next question.`);
      policies.reload();
      history.reload();
    } catch (caught) {
      setStatus(caught instanceof api.RequestError ? `Not saved: ${caught.message}` : "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  async function restore(id: string) {
    setBusy(true);
    try {
      const result = await api.copilot.restore(id);
      setStatus(`Restored; now revision ${result.revision}.`);
      history.reload();
      policies.reload();
      if (target) await open(target.folder, target.name);
    } catch (caught) {
      setStatus(caught instanceof api.RequestError ? caught.message : "Could not restore.");
    } finally {
      setBusy(false);
    }
  }

  if (!policies.data) return <Loading error={policies.error} />;
  const files = policies.data.files;
  return (
    <div className="knowledge-layout">
      <aside className="knowledge-files">
        <h3>Policy versions</h3>
        <ul className="ns-list">
          {policies.data.documents.map((doc) => (
            <li key={`${doc.policy}-${doc.version}`}>
              <button className="text-button" onClick={() => void open("policies", doc.file)}>
                {doc.title} v{doc.version}
              </button>
              <span className={`ns-pill${doc.in_force ? "" : " superseded"}`}>
                {doc.in_force ? `in force since ${doc.effective || "—"}` : `superseded ${doc.superseded_on}`}
              </span>
            </li>
          ))}
        </ul>
        {(["rules", "knowledge"] as const).map((folder) => (
          <div key={folder}>
            <h3>{folder === "rules" ? "Rule files" : "Records"}</h3>
            <ul className="ns-list">
              {(files[folder] ?? []).map((file) => (
                <li key={file.name}>
                  <button className="text-button" onClick={() => void open(folder, file.name)}>{file.name}</button>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <h3>History</h3>
        {history.data?.length ? (
          <ul className="ns-list">
            {history.data.slice(0, 10).map((entry) => (
              <li key={entry.id}>
                <code>{entry.id}</code> {entry.note || entry.files.join(", ")}
                <span className="ns-muted"> · {entry.author}</span>{" "}
                <button className="text-button" disabled={busy} onClick={() => void restore(entry.id)}>Restore</button>
              </li>
            ))}
          </ul>
        ) : <p className="ns-muted">No changes yet.</p>}
      </aside>

      <section className="knowledge-editor">
        {!target ? (
          <p className="ns-muted">
            Choose a policy, rule file or record file. Rules are written as
            <code> rule CODE "name" [CLAUSE] if … then … because … end</code>; every rule must cite a
            policy clause, and a new policy version is a new document with a later effective date.
          </p>
        ) : (
          <>
            <div className="editor-head">
              <strong>{target.folder}/{target.name}</strong>
              {content !== original && <span className="badge warn">unsaved changes</span>}
            </div>
            <label className="visually-hidden" htmlFor="knowledge-content">File content</label>
            <textarea id="knowledge-content" className="code-editor" spellCheck={false}
              value={content} onChange={(event) => { setContent(event.target.value); setValidation(null); }} />
            <div className="editor-actions">
              <label className="field-inline">
                Change note{" "}
                <input value={note} maxLength={500} onChange={(event) => setNote(event.target.value)}
                  placeholder="e.g. Returns policy v3 effective 1 Nov" />
              </label>
              <button className="text-button" disabled={busy} onClick={() => void check()}>Validate</button>
              <button className="primary-button" disabled={busy || content === original}
                onClick={() => void save()}>Validate &amp; save</button>
            </div>
            {validation && (
              <div className={`decision-callout ${validation.ok ? "ok" : "warn"}`} role="status">
                {validation.ok
                  ? <>Valid. The pack would load with {Object.entries(validation.summary).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.</>
                  : <><strong>Would break the knowledge pack:</strong><ul>{validation.errors.map((e) => <li key={e}>{e}</li>)}</ul></>}
              </div>
            )}
            {status && <p className="ns-muted" role="status">{status}</p>}
          </>
        )}
      </section>
    </div>
  );
}

// -- graph ---------------------------------------------------------------------------------------------

function Graph() {
  const [type, setType] = useState<string>("policy_version");
  const { data, error, reload } = useLoad(() => api.copilot.graph(type), [type]);
  const [form, setForm] = useState({ id: "", type: "policy_version", name: "", attributes: "" });
  const [status, setStatus] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setStatus(null);
    const attributes: Record<string, unknown> = {};
    for (const line of form.attributes.split("\n").map((l) => l.trim()).filter(Boolean)) {
      const [key, ...rest] = line.split("=");
      const raw = rest.join("=").trim();
      if (!key || !raw) {
        setStatus(`Each attribute line is name = value ("${line}" is not).`);
        return;
      }
      attributes[key.trim()] = raw === "true" ? true : raw === "false" ? false
        : /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw;
    }
    try {
      const result = await api.copilot.upsertEntity({ id: form.id, type: form.type,
        name: form.name || undefined, attributes });
      setStatus(`Saved as revision ${result.revision}.`);
      reload();
    } catch (caught) {
      setStatus(caught instanceof api.RequestError ? `Not saved: ${caught.message}` : "Not saved.");
    }
  }

  if (!data) return <Loading error={error} />;
  return (
    <div className="knowledge-layout">
      <section>
        <label className="field-inline">
          Entity type{" "}
          <select value={type} onChange={(event) => setType(event.target.value)}>
            {data.types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <div className="table-scroll">
          <table className="ns-table">
            <thead><tr><th>Id</th><th>Name</th><th>Attributes</th><th>Relations</th></tr></thead>
            <tbody>
              {data.entities.map((entity) => (
                <tr key={entity.id}>
                  <td><code>{entity.id}</code></td>
                  <td>{entity.name}</td>
                  <td>{Object.entries(entity.attributes).map(([k, v]) => `${k}=${String(v)}`).join(", ")}</td>
                  <td>{entity.relations.map((r) => `${r.direction === "out" ? "→" : "←"} ${r.predicate} ${r.target}`).join("; ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="knowledge-editor">
        <h3>Add an entity or change attributes</h3>
        <p className="ns-muted">
          Relationships are attributes whose value is another entity's id (for example
          <code> placed_by = CUST-1001</code>). Changes are layered over the base records and validated first.
        </p>
        <form onSubmit={(event) => void submit(event)} className="entity-form">
          <label>Id <input required value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} /></label>
          <label>Type
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              {data.types.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>Name <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Attributes (one <code>name = value</code> per line)
            <textarea className="code-editor small" value={form.attributes}
              onChange={(e) => setForm({ ...form, attributes: e.target.value })}
              placeholder={"policy = returns\nversion = 3\neffective_from = 2026-11-01\ndamage_report_days = 20"} />
          </label>
          <button className="primary-button" type="submit">Validate &amp; save</button>
        </form>
        {status && <p className="ns-muted" role="status">{status}</p>}
      </section>
    </div>
  );
}

// -- rule tester -------------------------------------------------------------------------------------------

function RuleTester() {
  const overview = useLoad(() => api.copilot.overview());
  const [goal, setGoal] = useState("replacement_available");
  const [subject, setSubject] = useState("ORD-33001");
  const [facts, setFacts] = useState("return_reason = damaged_on_arrival");
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.copilot.testRule>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const parsed: Record<string, unknown> = {};
    for (const line of facts.split("\n").map((l) => l.trim()).filter(Boolean)) {
      const [key, ...rest] = line.split("=");
      const raw = rest.join("=").trim();
      if (!key) continue;
      parsed[key.trim()] = raw === "true" ? true : raw === "false" ? false
        : /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw;
    }
    try {
      setResult(await api.copilot.testRule({ goal, subject: subject || undefined, facts: parsed }));
    } catch (caught) {
      setError(caught instanceof api.RequestError ? caught.message : "The test did not run.");
    }
  }

  return (
    <div className="knowledge-layout">
      <form className="entity-form" onSubmit={(event) => void run(event)}>
        <label>Goal
          <select value={goal} onChange={(e) => setGoal(e.target.value)}>
            {(overview.data?.goals ?? []).map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
          </select>
        </label>
        <label>Record id (leave empty for a general case)
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label>Stated facts (one <code>name = value</code> per line)
          <textarea className="code-editor small" value={facts} onChange={(e) => setFacts(e.target.value)} />
        </label>
        <button className="primary-button" type="submit">Evaluate</button>
      </form>
      <section>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {result && (
          <div className="decision-card">
            <div className="decision-head">
              <div className="decision-label ok">
                <span className="decision-kicker">{result.status}</span>
                <span className="decision-value">{result.label ?? result.value}</span>
              </div>
            </div>
            {result.decided_by && <p>Decided by <code>{result.decided_by}</code>: {result.because}</p>}
            {result.missing.length > 0 && <p className="ns-muted">Undecided without: {result.missing.join(", ")}</p>}
            {result.policy_versions.map((v) => <p key={v.name} className="ns-muted">{v.name} — {v.basis}</p>)}
          </div>
        )}
      </section>
    </div>
  );
}

// -- access -----------------------------------------------------------------------------------------------

function Access() {
  const { data, error } = useLoad(() => api.copilot.access());
  if (!data) return <Loading error={error} />;
  return (
    <>
      <p className="ns-muted">
        Authorization happens before reasoning: the rule engine only receives records a person may see.
        Visibility is declared in the schema rule file with <code>access</code> statements.
      </p>
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Who</th><th>Scope</th><th>Entity types</th></tr></thead>
          <tbody>
            {data.grants.map((grant) => (
              <tr key={`${grant.kind}-${grant.scope}`}>
                <td>{grant.kind}</td><td>{grant.kind === "public" ? "everyone" : grant.scope === "own" ? "their own records" : "all records"}</td>
                <td>{grant.types.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Role permissions</h3>
      <div className="table-scroll">
        <table className="ns-table">
          <thead><tr><th>Role</th><th>Permissions</th></tr></thead>
          <tbody>
            {Object.entries(data.roles).map(([role, perms]) => (
              <tr key={role}><td>{role}</td><td>{perms.join(", ")}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
