/**
 * API client.
 *
 * Tokens live in memory plus sessionStorage: the access token is short-lived and
 * the refresh token rotates on every use, so a stolen copy from storage is worth
 * one request before the family is burned. A single in-flight refresh is shared
 * by all concurrent 401s, so a page that fires several requests at once does not
 * rotate the token several times and trip the replay detector.
 */
import type {
  ApiError,
  AssistantTurn,
  AuthResponse,
  ChatMessage,
  Conversation,
  DocumentSummary,
  PipelineMode,
  Principal,
  RuleSummary,
  TokenBundle,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";
const STORAGE_KEY = "praxis.session";

export class RequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields?: string[];

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.status = status;
    this.code = error.code;
    this.fields = error.fields;
  }
}

interface Session {
  tokens: TokenBundle;
  user: Principal;
}

let session: Session | null = loadSession();
let refreshing: Promise<void> | null = null;
const listeners = new Set<(session: Session | null) => void>();

function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

function persist(next: Session | null): void {
  session = next;
  try {
    if (next) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the in-memory session still works for this tab */
  }
  listeners.forEach((listener) => listener(next));
}

export function onSessionChange(listener: (session: Session | null) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function currentUser(): Principal | null {
  return session?.user ?? null;
}

export function isAuthenticated(): boolean {
  return session !== null;
}

async function parseError(response: Response): Promise<never> {
  let error: ApiError = { code: "error", message: response.statusText };
  try {
    const body = await response.json();
    if (body?.error) error = body.error as ApiError;
  } catch {
    /* non-JSON error body */
  }
  throw new RequestError(response.status, error);
}

async function refreshSession(): Promise<void> {
  if (!session) throw new RequestError(401, { code: "unauthenticated", message: "Sign in again." });
  if (!refreshing) {
    refreshing = (async () => {
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: session!.tokens.refresh_token }),
      });
      if (!response.ok) {
        persist(null);
        await parseError(response);
      }
      const body = (await response.json()) as AuthResponse;
      persist({ tokens: body.tokens, user: body.user });
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
  retryOnUnauthorized?: boolean;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, formData, retryOnUnauthorized = true, signal } = options;
  const headers: Record<string, string> = {};
  if (session) headers.Authorization = `Bearer ${session.tokens.access_token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    signal,
  });

  if (response.status === 401 && retryOnUnauthorized && session) {
    await refreshSession();
    return request<T>(path, { ...options, retryOnUnauthorized: false });
  }
  if (!response.ok) await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// --- auth -------------------------------------------------------------------

export async function login(email: string, password: string): Promise<Principal> {
  const body = await request<AuthResponse>("/auth/login", {
    method: "POST",
    body: { email, password },
    retryOnUnauthorized: false,
  });
  persist({ tokens: body.tokens, user: body.user });
  return body.user;
}

export async function register(payload: {
  email: string;
  password: string;
  display_name?: string;
  tenant_slug?: string;
  tenant_name?: string;
}): Promise<Principal> {
  const body = await request<AuthResponse>("/auth/register", {
    method: "POST",
    body: payload,
    retryOnUnauthorized: false,
  });
  persist({ tokens: body.tokens, user: body.user });
  return body.user;
}

export async function logout(): Promise<void> {
  const token = session?.tokens.refresh_token;
  try {
    if (token) await request<void>("/auth/logout", { method: "POST", body: { refresh_token: token } });
  } catch {
    /* logging out locally matters more than the server's answer */
  } finally {
    persist(null);
  }
}

export async function me(): Promise<Principal> {
  const user = await request<Principal>("/auth/me");
  if (session) persist({ ...session, user });
  return user;
}

// --- conversations ----------------------------------------------------------

export const listConversations = () => request<Conversation[]>("/conversations");

export const createConversation = (title?: string) =>
  request<Conversation>("/conversations", { method: "POST", body: { title: title ?? null } });

export const renameConversation = (id: string, title: string) =>
  request<Conversation>(`/conversations/${id}`, { method: "PATCH", body: { title } });

export const deleteConversation = (id: string) =>
  request<void>(`/conversations/${id}`, { method: "DELETE" });

interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  created_at: string;
  status: string;
  route: string | null;
  verification_status: string | null;
  confidence: number | null;
  trace: Record<string, unknown>;
}

export interface ScopeEntry {
  name: string;
  subject: string;
  kind: string;
  description: string;
}

export interface WorkspaceScope {
  decides: ScopeEntry[];
  looks_up: ScopeEntry[];
  note: string;
}

/** What the workspace's rule base can decide. Empty for a document-pipeline tenant. */
export async function workspaceScope(): Promise<WorkspaceScope> {
  return request<WorkspaceScope>("/chat/scope");
}

export async function listMessages(conversationId: string): Promise<ChatMessage[]> {
  const rows = await request<StoredMessage[]>(`/conversations/${conversationId}/messages`);
  return rows.map((row) => ({
    id: row.id,
    role: row.role,
    content: row.content,
    status: "complete",
    turn: row.role === "assistant" ? traceToTurn(row) : null,
  }));
}

/** Rebuild the assistant envelope from the stored trace so history looks live. */
function traceToTurn(row: StoredMessage): AssistantTurn {
  const trace = (row.trace ?? {}) as Record<string, any>;
  return {
    message_id: row.id,
    conversation_id: null,
    answer: row.content,
    route: row.route ?? trace.route ?? "",
    gate_decision: trace.gate?.decision ?? "",
    verification_status: row.verification_status ?? trace.verification_status ?? "",
    confidence: row.confidence ?? trace.confidence ?? 0,
    citations: trace.citations ?? [],
    claims: trace.claims ?? [],
    reasoning:
      trace.reasoning ??
      { goal: "", facts: [], inferences: [], rules_considered: [], contradictions: [], conclusion: "" },
    policy_decision: trace.policy_decision ?? null,
    warnings: trace.warnings ?? [],
    stages: trace.stages ?? [],
    latency_ms: 0,
    pipeline_mode: trace.plan?.route ? "neurosymbolic" : "neurosymbolic",
  };
}

// --- chat -------------------------------------------------------------------

export const sendChat = (payload: {
  message: string;
  conversation_id?: string | null;
  pipeline_mode?: PipelineMode;
}) => request<AssistantTurn>("/chat", { method: "POST", body: payload });

export interface StreamHandlers {
  onStart?: (data: { conversation_id: string; route: string; gate_decision: string }) => void;
  onDelta?: (text: string) => void;
  onDone?: (turn: AssistantTurn) => void;
  onError?: (error: ApiError) => void;
}

/**
 * Stream a turn over SSE.
 *
 * The server runs the whole verified pipeline before emitting a token, so what
 * streams here is already gate-approved text. It is chunked purely so the UI can
 * show progress.
 */
export async function streamChat(
  payload: { message: string; conversation_id?: string | null; pipeline_mode?: PipelineMode },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const run = async (allowRetry: boolean): Promise<void> => {
    const response = await fetch(`${BASE}/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(session ? { Authorization: `Bearer ${session.tokens.access_token}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    });

    if (response.status === 401 && allowRetry && session) {
      await refreshSession();
      return run(false);
    }
    if (!response.ok || !response.body) {
      try {
        await parseError(response);
      } catch (error) {
        if (error instanceof RequestError) {
          handlers.onError?.({ code: error.code, message: error.message });
          return;
        }
        throw error;
      }
    }

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        dispatchFrame(frame, handlers);
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) dispatchFrame(buffer, handlers);
  };

  await run(true);
}

function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  let data: any;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }
  if (event === "start") handlers.onStart?.(data);
  else if (event === "delta") handlers.onDelta?.(data.text ?? "");
  else if (event === "done") handlers.onDone?.(data as AssistantTurn);
  else if (event === "error") handlers.onError?.(data as ApiError);
}

// --- documents & knowledge --------------------------------------------------

export const listDocuments = () => request<DocumentSummary[]>("/documents");

export async function uploadDocument(
  file: File,
  options: { collection?: string; sensitivity?: string; effectiveDate?: string } = {},
): Promise<DocumentSummary> {
  const form = new FormData();
  form.append("file", file);
  form.append("collection", options.collection ?? "default");
  form.append("sensitivity", options.sensitivity ?? "internal");
  if (options.effectiveDate) form.append("effective_date", options.effectiveDate);
  return request<DocumentSummary>("/documents/upload", { method: "POST", formData: form });
}

export const deleteDocument = (id: string) =>
  request<void>(`/documents/${id}`, { method: "DELETE" });

export const listRules = () => request<RuleSummary[]>("/rules");

export const health = () =>
  request<{ status: string; database: string; llm_provider: string; embedding_provider: string }>(
    "/health",
    { retryOnUnauthorized: false },
  );

// --- copilot administration -----------------------------------------------------

export interface CopilotEvaluationRun {
  file: string;
  split: string;
  cases: number;
  label: string;
  llm_model?: string;
  frozen?: boolean;
  note?: string;
  systems: Record<string, Record<string, number | null>>;
  by_category: Record<string, Record<string, number | null>>;
  not_run: Record<string, string>;
}

export interface CopilotValidation { ok: boolean; errors: string[]; summary: Record<string, number> }

export const copilot = {
  overview: () => request<{
    company: string; as_of: string; revision: string;
    counts: Record<string, number>; entity_types: Record<string, number>;
    goals: { name: string; subject: string; kind: string; description: string }[];
    evaluation: CopilotEvaluationRun | null;
  }>("/copilot/overview"),
  analytics: () => request<Record<string, unknown>>("/copilot/analytics"),
  gaps: () => request<{
    type: string; summary: string; detail: string; owner?: string | null; count: number;
    last_seen: string; examples: { question: string; message_id: string }[];
  }[]>("/copilot/gaps"),
  review: () => request<{
    message_id: string; at: string; question: string; answer: string; outcome: string;
    pipeline: string; decision: string; reasons: string[]; gap: string | null;
  }[]>("/copilot/review"),
  trace: (id: string) => request<{ message_id: string; answer: string; neurosymbolic: import("./types").NeuroSymbolicAnswer }>(`/copilot/traces/${id}`),
  policies: () => request<{
    documents: { policy: string; version: number; title: string; effective: string;
      superseded_on: string | null; in_force: boolean; file: string;
      clauses: { id: string; title: string }[] }[];
    files: Record<string, { name: string; bytes: number }[]>;
  }>("/copilot/policies"),
  rules: () => request<{
    rules: { code: string; name: string; clauses: string[]; concludes: string; priority: number; source: string }[];
    files: { name: string; bytes: number }[];
  }>("/copilot/rules"),
  readFile: (folder: string, name: string) =>
    request<{ content: string }>(`/copilot/files/${folder}/${encodeURIComponent(name)}`),
  validate: (folder: string, name: string, content: string) =>
    request<CopilotValidation>("/copilot/validate", { method: "POST", body: { folder, name, content } }),
  writeFile: (folder: string, name: string, content: string, note: string) =>
    request<{ revision: string }>(`/copilot/files/${folder}/${encodeURIComponent(name)}`,
      { method: "PUT", body: { content, note } }),
  history: () => request<{ id: string; author: string; note: string; files: string[] }[]>("/copilot/history"),
  restore: (id: string) => request<{ revision: string }>(`/copilot/history/${id}/restore`, { method: "POST" }),
  graph: (type?: string) => request<{
    types: string[];
    entities: { id: string; type: string; name: string; attributes: Record<string, unknown>;
      relations: { predicate: string; target: string; direction: string }[] }[];
    attributes: Record<string, { subject: string; type: string }>;
  }>(`/copilot/graph${type ? `?type=${encodeURIComponent(type)}` : ""}`),
  upsertEntity: (payload: { id: string; type: string; name?: string; attributes: Record<string, unknown> }) =>
    request<{ revision: string }>("/copilot/graph/entities", { method: "POST", body: payload }),
  testRule: (payload: { goal: string; subject?: string; facts: Record<string, unknown> }) =>
    request<{
      goal: string; subject: string; status: string; value: string; label: string | null;
      decided_by: string | null; because: string | null; missing: string[];
      policy_versions: { name: string; basis: string }[];
    }>("/copilot/rules/test", { method: "POST", body: payload }),
  access: () => request<{ grants: { kind: string; scope: string; types: string[] }[]; roles: Record<string, string[]> }>("/copilot/access"),
  evaluation: () => request<{ runs: CopilotEvaluationRun[] }>("/copilot/evaluation"),
  compareStatus: () => request<{ available: boolean; model: string; reason: string | null }>("/copilot/compare"),
  compare: (question: string) =>
    request<CompareResult>("/copilot/compare", { method: "POST", body: { question } }),
};

export interface CompareFlag { kind: "contradicts_rules" | "unsupported_answer" | "unsupported_number"; detail: string }
export interface CompareSystem {
  key: "llm_only" | "llm_rag" | "praxis";
  label: string;
  text: string;
  outcome: string;
  decision: boolean | null;
  value: number | string | null;
  sources: string[];
  latency_ms: number;
  flags: CompareFlag[];
  decision_label?: string;
  pipeline?: string;
  verification?: { status: string; passed: boolean | null; checks: { check: string; passed: boolean; detail: string }[] };
  claims?: import("./types").NsClaim[];
  rules_applied?: { code: string; name: string }[];
  policy_versions?: { name: string; basis: string }[];
}
export interface CompareResult { question: string; model: string; systems: CompareSystem[] }
