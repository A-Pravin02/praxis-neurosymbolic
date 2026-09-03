export type PipelineMode =
  | "llm_only"
  | "llm_rag"
  | "llm_rag_verify"
  | "neurosymbolic";

export interface Principal {
  user_id: string;
  tenant_id: string;
  tenant_slug: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
}

export interface TokenBundle {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: Principal;
  tokens: TokenBundle;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  is_archived: boolean;
  message_count: number;
}

export interface Citation {
  index: number;
  document_id: string | null;
  document_title: string;
  chunk_id: string | null;
  section: string;
  page: number | null;
  snippet: string;
  score: number;
  quarantined: boolean;
}

export interface ClaimView {
  text: string;
  kind: string;
  status: string;
  support_score: number;
  relevance?: number;
  citation_indexes: number[];
  fact_ids: string[];
}

export interface FactView {
  id: string | null;
  subject: string;
  predicate: string;
  object: { type: string; value: unknown; unit?: string };
  kind: "DIRECT" | "INFERRED" | "MODEL_GENERATED";
  confidence: number;
  provenance?: {
    source_kind: string;
    document_title: string;
    evidence_text: string;
    rule_codes: string[];
    premise_fact_ids: string[];
    effective_date: string | null;
  };
  citation_index: number | null;
}

export interface InferenceStep {
  step: number;
  rule_code: string;
  rule_name: string;
  rule_text: string;
  premises: string[];
  conclusion: string;
  confidence: number;
  sentence?: string;
}

export interface ContradictionView {
  subject: string;
  predicate: string;
  values: unknown[];
  severity: string;
  resolution: string;
  kind: string;
  resolved: boolean;
  preferred_value: unknown;
  superseded_values: unknown[];
  citation_indexes: number[];
  blocking?: boolean;
}

export interface ReasoningTrace {
  goal: string;
  facts: FactView[];
  inferences: InferenceStep[];
  rules_considered: string[];
  rules_fired?: string[];
  contradictions: ContradictionView[];
  conclusion: string;
  iterations?: number;
}

export interface PolicyDecision {
  decision: string;
  reason: string;
  matched_policy: string | null;
  obligations: string[];
}

export interface StageTiming {
  name: string;
  ms: number;
  detail: Record<string, unknown>;
}

export interface AssistantTurn {
  message_id: string | null;
  conversation_id: string | null;
  answer: string;
  route: string;
  gate_decision: string;
  verification_status: string;
  confidence: number;
  citations: Citation[];
  claims: ClaimView[];
  reasoning: ReasoningTrace;
  policy_decision: PolicyDecision | null;
  warnings: string[];
  stages: StageTiming[];
  latency_ms: number;
  pipeline_mode: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: "streaming" | "complete" | "error";
  turn?: AssistantTurn | null;
  error?: string | null;
}

export interface DocumentSummary {
  id: string;
  title: string;
  media_type: string;
  collection: string;
  sensitivity: string;
  required_permission: string | null;
  status: string;
  byte_size: number;
  chunk_count: number;
  effective_date: string | null;
  version_label: string;
  created_at: string;
  error: string | null;
  injection_flagged_chunks: number;
}

export interface RuleSummary {
  id: string;
  code: string;
  name: string;
  description: string;
  body: unknown[];
  head: Record<string, unknown>;
  priority: number;
  confidence: number;
  is_active: boolean;
  origin: string;
}

export interface ApiError {
  code: string;
  message: string;
  fields?: string[];
}
