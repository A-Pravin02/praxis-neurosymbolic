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
  neurosymbolic?: NeuroSymbolicAnswer | null;
}

// --- neuro-symbolic assistant -------------------------------------------------------

export type NsOutcome = "answered" | "conditional" | "clarification" | "insufficient" | "denied" | "needs_review";
export type NsLayer = "NEURAL" | "SYMBOLIC" | "HYBRID";

export interface NsSource {
  type: "policy" | "record";
  id: string;
  title: string;
  document?: string;
  text?: string;
  policy?: string;
  version?: number;
  effective?: string;
  in_force?: boolean;
}

export interface NsCheck {
  check: string;
  passed: boolean;
  detail: string;
}

export interface NsPolicyVersion {
  subject: string;
  policy: string;
  version: string;
  name: string;
  number: number | null;
  effective_from: string;
  superseded_on: string | null;
  reference_date: string;
  basis: string;
  assumed: boolean;
}

export interface NsRuleSummary {
  rule: string;
  name: string;
  clauses: string[];
  status?: string;
  because?: string;
  formula: string;
}

export interface NsGap {
  type: "uncovered_topic" | "undefined_case" | "missing_policy_parameter" | "unclear_request" | "verification_failure";
  summary: string;
  detail: string;
  owner?: string | null;
}

export interface NsClaim {
  kind: string;
  predicate: string;
  stated: string;
  record: string;
  confirmed: boolean;
  source: string;
  reason: string;
}

export interface NsCondition {
  index: number;
  condition: string;
  status: "satisfied" | "violated" | "unknown";
  note: string;
}

export interface NsRule {
  rule: string;
  name: string;
  clauses: string[];
  status?: "proved" | "refuted" | "conditional";
  value?: string | null;
  because?: string;
  failed?: NsCondition | null;
  conditions?: NsCondition[];
}

export interface NsTrace {
  question: string;
  neural_interpretation: {
    speech_act: { label: string; score: number };
    intent_candidates: { name: string; kind: string; goal_kind: string; score: number }[];
    stated_inputs: { predicate: string; value: unknown; confidence: number; evidence: string; method: string }[];
    encoder: string;
    semantic_understanding: boolean;
  };
  entities: { type: string; id?: string; value?: number; text: string; method: string }[];
  retrieved_knowledge: { id: string; title: string; document: string; text: string; score?: number }[];
  symbolic_facts: { subject: string; predicate: string; value: string; source: string; rule?: string }[];
  rules_used: NsRule[];
  rules_considered: NsRule[];
  reasoning_steps: string[];
  neural_symbolic_loop: { iteration: number; symbolic_needed: string[]; accepted: string[] }[];
  grounding: {
    assumptions?: string[];
    rejected_statements?: { predicate: string; stated: string; record: string; kind?: string; reason?: string }[];
  };
  policy_versions?: NsPolicyVersion[];
  authorization?: { access: string; person: string | null; visible_records: number; withheld_records: number };
  verification: { passed?: boolean; checks?: { check: string; passed: boolean; detail: string }[] };
  selected_pipeline: string;
  pipeline_label: string;
  pipeline_reason: string;
  why_neural: string;
  why_symbolic: string;
  components: { layer: NsLayer; stage: string }[];
  confidence: number;
  confidence_factors: Record<string, number>;
}

export interface NeuroSymbolicAnswer {
  pipeline: string;
  pipeline_label: string;
  outcome: NsOutcome;
  confidence: number;
  explanation: string[];
  sources: NsSource[];
  clarification: { question: string; options: { id: string; label: string; detail: string }[] } | null;
  goal: string | null;
  trace: NsTrace;
  decision_label?: string;
  decision_kind?: string;
  recommended_action?: string | null;
  verification?: { status: "passed" | "needs_review" | "not_applicable"; passed: boolean | null; checks: NsCheck[] };
  knowledge_gap?: NsGap | null;
  policy_versions?: NsPolicyVersion[];
  rules_applied?: NsRuleSummary[];
  facts?: { subject: string; predicate: string; value: string; source: string; rule?: string }[];
  claims?: NsClaim[];
  audience?: "staff" | "self";
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
