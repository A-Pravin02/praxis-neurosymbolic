import { useEffect, useState, type ChangeEvent } from "react";

import { RequestError, deleteDocument, listDocuments, listRules, uploadDocument } from "../lib/api";
import type { DocumentSummary, RuleSummary } from "../lib/types";

interface Props {
  canUpload: boolean;
  canDelete: boolean;
  onClose: () => void;
}

export default function DocumentsView({ canUpload, canDelete, onClose }: Props) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [rules, setRules] = useState<RuleSummary[]>([]);
  const [tab, setTab] = useState<"documents" | "rules">("documents");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const [nextDocuments, nextRules] = await Promise.all([listDocuments(), listRules()]);
      setDocuments(nextDocuments);
      setRules(nextRules);
      setError(null);
    } catch (caught) {
      setError(caught instanceof RequestError ? caught.message : "Could not load the workspace.");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDocument(file);
      await refresh();
    } catch (caught) {
      setError(caught instanceof RequestError ? caught.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      await deleteDocument(id);
      await refresh();
    } catch (caught) {
      setError(caught instanceof RequestError ? caught.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="evidence-panel">
      <div className="panel-header">
        <h2>Workspace knowledge</h2>
        <button className="icon-button" onClick={onClose} aria-label="Close panel">✕</button>
      </div>

      <div className="panel-tabs">
        <button className={`panel-tab${tab === "documents" ? " active" : ""}`}
                onClick={() => setTab("documents")}>
          Documents ({documents.length})
        </button>
        <button className={`panel-tab${tab === "rules" ? " active" : ""}`}
                onClick={() => setTab("rules")}>
          Rules ({rules.length})
        </button>
      </div>

      <div className="panel-body">
        {error && <div className="form-error">{error}</div>}

        {tab === "documents" && (
          <>
            {canUpload && (
              <div className="doc-actions">
                <label className="upload-label">
                  {busy ? "Working…" : "Upload a document (PDF, DOCX, MD, TXT, CSV, JSON)"}
                  <input type="file" onChange={onUpload} disabled={busy}
                         accept=".pdf,.docx,.md,.markdown,.txt,.csv,.tsv,.json,.jsonl" />
                </label>
              </div>
            )}

            {documents.length === 0 && (
              <p className="panel-empty">
                No documents you are permitted to see. Upload one to give the assistant
                something to ground its answers in.
              </p>
            )}

            {documents.map((document) => (
              <div className="card" key={document.id}>
                <div className="card-head">
                  <span className="card-title">{document.title}</span>
                  {document.injection_flagged_chunks > 0 && (
                    <span className="badge warn">{document.injection_flagged_chunks} flagged</span>
                  )}
                </div>
                <div className="card-meta">
                  <span>{document.collection}</span>
                  <span>{document.sensitivity}</span>
                  <span>{document.chunk_count} chunks</span>
                  {document.effective_date && <span>effective {document.effective_date}</span>}
                  {document.version_label && <span>v{document.version_label}</span>}
                  <span>{document.status}</span>
                </div>
                {document.required_permission && (
                  <div className="card-meta">
                    requires <code>{document.required_permission}</code>
                  </div>
                )}
                {canDelete && (
                  <div className="turn-actions">
                    <button className="text-button" onClick={() => remove(document.id)}
                            disabled={busy}>
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
          </>
        )}

        {tab === "rules" && (
          <>
            {rules.length === 0 && <p className="panel-empty">No rules in this workspace.</p>}
            {rules.map((rule) => (
              <div className="card" key={rule.id}>
                <div className="card-head">
                  <span className="card-title">
                    {rule.code} — {rule.name}
                  </span>
                  <span className={`badge ${rule.is_active ? "ok" : "neutral"}`}>
                    {rule.is_active ? "active" : "inactive"}
                  </span>
                </div>
                {rule.description && <div className="card-text">{rule.description}</div>}
                <div className="card-meta">
                  <span>priority {rule.priority}</span>
                  <span>confidence {rule.confidence}</span>
                  <span>{rule.origin}</span>
                </div>
              </div>
            ))}
            <p className="card-meta">
              Rules are stored as validated JSON, never as code, and are applied
              deterministically by the reasoning engine.
            </p>
          </>
        )}
      </div>
    </aside>
  );
}
