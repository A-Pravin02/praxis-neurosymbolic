import { useCallback, useEffect, useRef, useState } from "react";

import AuthView from "./components/AuthView";
import ChatView from "./components/ChatView";
import DocumentsView from "./components/DocumentsView";
import EvidencePanel from "./components/EvidencePanel";
import * as api from "./lib/api";
import type {
  AssistantTurn, ChatMessage, Conversation, PipelineMode, Principal,
} from "./lib/types";

type PanelMode = "evidence" | "documents" | null;

const MODES: { value: PipelineMode; label: string }[] = [
  { value: "neurosymbolic", label: "Full pipeline" },
  { value: "llm_rag_verify", label: "RAG + verification" },
  { value: "llm_rag", label: "RAG only" },
  { value: "llm_only", label: "Model only" },
];

export default function App() {
  const [user, setUser] = useState<Principal | null>(api.currentUser());
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<PanelMode>(null);
  const [panelTurn, setPanelTurn] = useState<AssistantTurn | null>(null);
  const [focusCitation, setFocusCitation] = useState<number | null>(null);
  const [pipelineMode, setPipelineMode] = useState<PipelineMode>("neurosymbolic");
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const lastQuestion = useRef<string>("");

  useEffect(() => api.onSessionChange((session) => setUser(session?.user ?? null)), []);

  const refreshConversations = useCallback(async () => {
    if (!api.isAuthenticated()) return;
    try {
      setConversations(await api.listConversations());
    } catch {
      /* the sidebar is not worth an error banner */
    }
  }, []);

  useEffect(() => {
    if (user) void refreshConversations();
  }, [user, refreshConversations]);

  async function openConversation(id: string) {
    setActiveId(id);
    setSidebarOpen(false);
    setPanel(null);
    try {
      setMessages(await api.listMessages(id));
    } catch (caught) {
      setError(caught instanceof api.RequestError ? caught.message : "Could not open that conversation.");
    }
  }

  function startNewConversation() {
    setActiveId(null);
    setMessages([]);
    setPanelTurn(null);
    setPanel(null);
    setSidebarOpen(false);
  }

  async function removeConversation(id: string) {
    try {
      await api.deleteConversation(id);
      if (id === activeId) startNewConversation();
      await refreshConversations();
    } catch (caught) {
      setError(caught instanceof api.RequestError ? caught.message : "Could not delete that conversation.");
    }
  }

  async function send(text: string) {
    if (busy) return;
    lastQuestion.current = text;
    setError(null);
    setBusy(true);

    const userMessage: ChatMessage = {
      id: `local-user-${Date.now()}`, role: "user", content: text, status: "complete",
    };
    const placeholderId = `local-assistant-${Date.now()}`;
    setMessages((current) => [
      ...current,
      userMessage,
      { id: placeholderId, role: "assistant", content: "", status: "streaming", turn: null },
    ]);

    let conversationId = activeId;

    try {
      await api.streamChat(
        { message: text, conversation_id: conversationId, pipeline_mode: pipelineMode },
        {
          onStart: (data) => {
            conversationId = data.conversation_id;
            setActiveId(data.conversation_id);
          },
          onDelta: (delta) => {
            setMessages((current) =>
              current.map((message) =>
                message.id === placeholderId
                  ? { ...message, content: message.content + delta }
                  : message,
              ),
            );
          },
          onDone: (turn) => {
            setMessages((current) =>
              current.map((message) =>
                message.id === placeholderId
                  ? {
                      id: turn.message_id ?? placeholderId,
                      role: "assistant",
                      content: turn.answer,
                      status: "complete",
                      turn,
                    }
                  : message,
              ),
            );
            setPanelTurn(turn);
          },
          onError: (apiError) => {
            setMessages((current) =>
              current.map((message) =>
                message.id === placeholderId
                  ? { ...message, status: "error", error: apiError.message }
                  : message,
              ),
            );
            setError(apiError.message);
          },
        },
      );
    } catch (caught) {
      const message =
        caught instanceof api.RequestError
          ? caught.message
          : "The assistant is unreachable. Check that the API is running.";
      setMessages((current) =>
        current.map((entry) =>
          entry.id === placeholderId ? { ...entry, status: "error", error: message } : entry,
        ),
      );
      setError(message);
    } finally {
      setBusy(false);
      void refreshConversations();
    }
  }

  function retry() {
    if (!lastQuestion.current) return;
    setMessages((current) => current.filter((message) => message.status !== "error"));
    void send(lastQuestion.current);
  }

  function inspect(turn: AssistantTurn, citationIndex?: number) {
    setPanelTurn(turn);
    setFocusCitation(citationIndex ?? null);
    setPanel("evidence");
  }

  async function signOut() {
    await api.logout();
    setUser(null);
    setConversations([]);
    setMessages([]);
    setActiveId(null);
    setPanel(null);
  }

  if (!user) {
    return <AuthView onAuthenticated={setUser} />;
  }

  const activeTitle =
    conversations.find((conversation) => conversation.id === activeId)?.title ??
    "New conversation";
  const canUpload = user.permissions.includes("document:write");
  const canDelete = user.permissions.includes("document:delete");
  // Ablation modes answer without the evidence gate, so the API restricts them to
  // users who may run the benchmark. Hiding the selector keeps the UI honest
  // about what an ordinary user can choose; the API enforces it regardless.
  const canCompareModes = user.permissions.includes("eval:run");

  return (
    <div className={`app${panel ? " with-panel" : ""}`}>
      <nav className={`sidebar${sidebarOpen ? " open" : ""}`}>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">P</div>
          <div>
            <div className="brand-name">Praxis</div>
            <div className="brand-sub">evidence-gated assistant</div>
          </div>
        </div>

        <button className="new-chat" onClick={startNewConversation}>+ New conversation</button>

        <div className="sidebar-section">Conversations</div>
        <div className="conversation-list">
          {conversations.length === 0 && (
            <p className="sidebar-section" style={{ opacity: 0.7 }}>Nothing yet.</p>
          )}
          {conversations.map((conversation) => (
            <div
              key={conversation.id}
              className={`conversation-item${conversation.id === activeId ? " active" : ""}`}
              onClick={() => void openConversation(conversation.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter") void openConversation(conversation.id);
              }}
            >
              <span className="conversation-title">{conversation.title}</span>
              <button
                className="conversation-delete"
                aria-label={`Delete ${conversation.title}`}
                onClick={(event) => {
                  event.stopPropagation();
                  void removeConversation(conversation.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <button
            className="link-button"
            onClick={() => setPanel(panel === "documents" ? null : "documents")}
          >
            Documents &amp; rules
          </button>
          <div className="user-chip">
            <div className="avatar" aria-hidden="true">
              {(user.display_name || user.email).slice(0, 1).toUpperCase()}
            </div>
            <div className="user-meta">
              <div className="user-email">{user.display_name || user.email}</div>
              <div className="user-tenant">
                {user.tenant_slug} · {user.roles.join(", ") || "no role"}
              </div>
            </div>
          </div>
          <button className="link-button" onClick={() => void signOut()}>Sign out</button>
        </div>
      </nav>

      <main className="main">
        <header className="topbar">
          <button
            className="icon-button"
            onClick={() => setSidebarOpen((open) => !open)}
            aria-label="Toggle conversations"
          >
            ☰
          </button>
          <h1>{activeTitle}</h1>
          {canCompareModes && (
            <>
              <label className="visually-hidden" htmlFor="pipeline-mode">
                Pipeline mode
              </label>
              <select
                id="pipeline-mode"
                className="mode-select"
                value={pipelineMode}
                onChange={(event) => setPipelineMode(event.target.value as PipelineMode)}
                title="Compare the full pipeline against its ablations"
              >
                {MODES.map((mode) => (
                  <option key={mode.value} value={mode.value}>{mode.label}</option>
                ))}
              </select>
            </>
          )}
          <button
            className={`icon-button${panel === "evidence" ? " active" : ""}`}
            onClick={() => setPanel(panel === "evidence" ? null : "evidence")}
          >
            Evidence
          </button>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button className="text-button" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}

        <ChatView
          messages={messages}
          busy={busy}
          onSend={(text) => void send(text)}
          onRetry={retry}
          onInspect={inspect}
          pipelineMode={pipelineMode}
          displayName={user.display_name}
        />
      </main>

      {panel === "evidence" && (
        <EvidencePanel
          turn={panelTurn}
          focusCitation={focusCitation}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "documents" && (
        <DocumentsView
          canUpload={canUpload}
          canDelete={canDelete}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  );
}
