import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import Markdown from "./Markdown";
import { ClarificationOptions, HowDetermined, KnowledgeUsed, NsStatus } from "./ReasoningPanel";
import type { AssistantTurn, ChatMessage, PipelineMode } from "../lib/types";

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (message: string) => void;
  onRetry: () => void;
  onInspect: (turn: AssistantTurn, citationIndex?: number) => void;
  pipelineMode: PipelineMode;
  displayName: string;
  workspace?: string;
  roles?: string[];
}

// Starter questions for the neuro-symbolic workspace. They are here to show what kinds
// of question the assistant handles; nothing in the assistant knows or matches them.
const CUSTOMER_SUGGESTIONS = [
  { kind: "Policy reasoning", text: "I ordered shoes two weeks ago but changed my mind. Can I send them back?" },
  { kind: "Multi-hop", text: "Why was my refund rejected even though I returned the product?" },
  { kind: "Clarification", text: "My package arrived damaged. Can I get a replacement instead of a refund?" },
  { kind: "Record lookup", text: "Where is my order?" },
  { kind: "Case analysis", text: "Can I return a product 20 days after delivery?" },
  { kind: "Enumeration", text: "Which products are eligible for same-day delivery?" },
];
const EMPLOYEE_SUGGESTIONS = [
  { kind: "Calculation", text: "How much will I be reimbursed if my business trip cost ₹8,750?" },
  { kind: "Conditional", text: "Am I eligible for reimbursement if I drove 450 km and spent ₹4,200?" },
  { kind: "Calculation", text: "How many days of annual leave do I get?" },
  { kind: "Why not", text: "Why am I not eligible for the higher annual leave?" },
  { kind: "Retrieval", text: "What is the company's leave policy?" },
  { kind: "Retrieval", text: "I have a problem with my credentials, who should I call?" },
];
const NS_WORKSPACES = new Set(["meridian"]);

const SUGGESTIONS = [
  { kind: "Retrieval", text: "What does the employee handbook say about sick leave?" },
  { kind: "Symbolic reasoning", text: "How many annual leave days am I entitled to?" },
  { kind: "Contradiction", text: "How long is the warranty on the Praxis Router X1?" },
  { kind: "Abstention", text: "What is the company policy on submarine maintenance?" },
  { kind: "Authorization", text: "Can this user access salary records?" },
  { kind: "General knowledge", text: "What is retrieval-augmented generation?" },
];

export default function ChatView({
  messages, busy, onSend, onRetry, onInspect, pipelineMode, displayName, workspace, roles,
}: Props) {
  const neuroSymbolic = NS_WORKSPACES.has(workspace ?? "");
  const suggestions = !neuroSymbolic ? SUGGESTIONS
    : (roles ?? []).includes("customer") ? CUSTOMER_SUGGESTIONS
    : (roles ?? []).includes("support") ? CUSTOMER_SUGGESTIONS
    : EMPLOYEE_SUGGESTIONS;
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 190)}px`;
  }, [draft]);

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  const isEmpty = messages.length === 0;

  return (
    <>
      <div className="messages">
        {isEmpty ? (
          <div className="empty-state">
            <h2>{neuroSymbolic ? `How can I help, ${displayName || "there"}?`
              : `Ask your workspace, ${displayName || "there"}`}</h2>
            <p>
              {neuroSymbolic
                ? "Ask anything about orders, returns, refunds, delivery, leave or expenses, in your own words. I understand the question, then apply the company's actual rules to your records — and when a decision depends on something I don't know, I tell you exactly what."
                : "Answers are built from your documents, structured facts and rules. Every factual claim is checked against evidence before it reaches you — and when the evidence is not there, the assistant says so instead of guessing."}
            </p>
            <div className="suggestions">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion.text}
                  className="suggestion"
                  onClick={() => onSend(suggestion.text)}
                  disabled={busy}
                >
                  <span className="suggestion-kind">{suggestion.kind}</span>
                  {suggestion.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="messages-inner">
            {messages.map((message) => (
              <MessageRow
                key={message.id}
                message={message}
                onInspect={onInspect}
                onRetry={onRetry}
                onSend={onSend}
                busy={busy}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="composer">
        <div className="composer-inner">
          <div className="composer-box">
            <label className="visually-hidden" htmlFor="composer">Message</label>
            <textarea
              id="composer"
              ref={textareaRef}
              rows={1}
              placeholder={neuroSymbolic ? "Ask anything — orders, returns, refunds, leave, expenses…"
                : "Ask about your documents, policies or entitlements…"}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              disabled={busy}
            />
            <button
              className="send-button"
              onClick={submit}
              disabled={busy || !draft.trim()}
              aria-label="Send"
            >
              {busy ? <span className="spinner" /> : "↑"}
            </button>
          </div>
          <p className="composer-hint">
            {neuroSymbolic
              ? <>Enter to send. Decisions come from company rules applied to your records; open <strong>How was this determined?</strong> under any answer to see why.</>
              : <>Enter to send, Shift+Enter for a new line. Pipeline: <strong>{pipelineMode}</strong>. Praxis abstains when the knowledge base does not support an answer.</>}
          </p>
        </div>
      </div>
    </>
  );
}

function MessageRow({
  message, onInspect, onRetry, onSend, busy,
}: {
  message: ChatMessage;
  onInspect: (turn: AssistantTurn, citationIndex?: number) => void;
  onRetry: () => void;
  onSend: (message: string) => void;
  busy: boolean;
}) {
  const turn = message.turn ?? null;
  const streaming = message.status === "streaming";

  return (
    <div className={`message ${message.role}`}>
      <div className="message-avatar" aria-hidden="true">
        {message.role === "user" ? "You" : "P"}
      </div>
      <div className="message-body">
        <div className="message-role">{message.role === "user" ? "You" : "Praxis"}</div>

        {message.status === "error" ? (
          <div className="form-error">
            {message.error ?? "Something went wrong."}
            <button className="text-button" onClick={onRetry} style={{ marginLeft: 10 }}>
              Retry
            </button>
          </div>
        ) : streaming && !message.content ? (
          <span className="typing" aria-label="Thinking">
            <span /><span /><span />
          </span>
        ) : (
          <Markdown
            text={message.content}
            citations={turn?.citations ?? []}
            onCitationClick={turn ? (index) => onInspect(turn, index) : undefined}
          />
        )}

        {turn && !streaming && turn.neurosymbolic && (
          <>
            <ClarificationOptions ns={turn.neurosymbolic} onChoose={onSend} disabled={busy} />
            <NsStatus ns={turn.neurosymbolic} latencyMs={turn.latency_ms} />
            <KnowledgeUsed ns={turn.neurosymbolic} />
            <HowDetermined ns={turn.neurosymbolic} />
          </>
        )}
        {turn && !streaming && !turn.neurosymbolic && <TurnFooter turn={turn} onInspect={onInspect} />}
      </div>
    </div>
  );
}

function TurnFooter({
  turn, onInspect,
}: {
  turn: AssistantTurn;
  onInspect: (turn: AssistantTurn, citationIndex?: number) => void;
}) {
  const gate = gateBadge(turn.gate_decision);
  const verification = verificationBadge(turn.verification_status);
  const confidenceColour = useMemo(() => {
    if (turn.confidence >= 0.7) return "var(--ok)";
    if (turn.confidence >= 0.45) return "var(--warn)";
    return "var(--danger)";
  }, [turn.confidence]);

  return (
    <>
      <div className="turn-status">
        <span className={`badge ${gate.tone}`}>
          <span className="badge-dot" /> {gate.label}
        </span>
        {verification && (
          <span className={`badge ${verification.tone}`}>{verification.label}</span>
        )}
        <span className="badge neutral">{turn.route.replace(/_/g, " ").toLowerCase()}</span>
        {turn.gate_decision !== "DENY" && (
          <span className="confidence-meter">
            confidence
            <span className="confidence-track">
              <span
                className="confidence-fill"
                style={{
                  width: `${Math.round(turn.confidence * 100)}%`,
                  background: confidenceColour,
                }}
              />
            </span>
            {(turn.confidence * 100).toFixed(0)}%
          </span>
        )}
        {turn.latency_ms > 0 && (
          <span className="badge neutral">{turn.latency_ms} ms</span>
        )}
      </div>

      {turn.warnings.length > 0 && (
        <ul className="warning-list">
          {turn.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}

      {turn.citations.length > 0 && (
        <details className="sources">
          <summary>Sources ({turn.citations.length})</summary>
          {turn.citations.map((citation) => (
            <div className="source-row" key={citation.index}>
              <button
                className={`citation-chip source-index${citation.quarantined ? " quarantined" : ""}`}
                onClick={() => onInspect(turn, citation.index)}
              >
                {citation.index}
              </button>
              <div>
                <div className="source-title">{citation.document_title}</div>
                <div className="source-snippet">{citation.snippet}</div>
                <div className="source-meta">
                  {citation.section && <span>{citation.section} · </span>}
                  relevance {citation.score.toFixed(2)}
                  {citation.quarantined && " · quarantined, treated as inert data"}
                </div>
              </div>
            </div>
          ))}
        </details>
      )}

      <div className="turn-actions">
        <button className="text-button" onClick={() => onInspect(turn)}>
          Evidence &amp; reasoning
        </button>
        <button
          className="text-button"
          onClick={() => navigator.clipboard?.writeText(turn.answer)}
        >
          Copy
        </button>
      </div>
    </>
  );
}

function gateBadge(decision: string): { label: string; tone: string } {
  switch (decision) {
    case "ANSWER":
      return { label: "Answered from evidence", tone: "ok" };
    case "ANSWER_WITH_CAVEAT":
      return { label: "Provisional", tone: "warn" };
    case "REPORT_CONFLICT":
      return { label: "Sources conflict", tone: "danger" };
    case "ABSTAIN":
      return { label: "Abstained", tone: "info" };
    case "DENY":
      return { label: "Denied by policy", tone: "danger" };
    default:
      return { label: decision || "unknown", tone: "neutral" };
  }
}

function verificationBadge(status: string): { label: string; tone: string } | null {
  switch (status) {
    case "VERIFIED":
      return { label: "verified", tone: "ok" };
    case "PARTIALLY_VERIFIED":
      return { label: "partly verified", tone: "warn" };
    case "CONTRADICTED":
      return { label: "contradicted", tone: "danger" };
    case "INSUFFICIENT_EVIDENCE":
      return { label: "insufficient evidence", tone: "info" };
    case "UNVERIFIED":
      return { label: "unverified", tone: "warn" };
    default:
      return null;
  }
}
