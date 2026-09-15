# Praxis Support Decision Copilot

**Natural language in. Verified enterprise decision out.**

Praxis is a vertical decision-intelligence platform. It is designed to reduce
hallucinated and unsupported answers in enterprise AI. It combines neural language
understanding with symbolic reasoning over a company's policies, structured records and
business rules.

The first product built on it is the **Support Decision Copilot**. It helps
customer-service agents decide, correctly and quickly, what the company allows in a
specific situation.

- **Research contribution:** hallucination reduction through neuro-symbolic reasoning
  and verification.
- **Business value:** faster, more consistent and more auditable decisions.
- **Principle:** neural AI understands what the person describes; symbolic AI determines
  what the enterprise's rules imply.

---

## 1. The problem it solves

A support agent who hears *"my laptop arrived damaged 12 days ago and I want a
replacement"* must combine several things at once:

- the order, the product and its category;
- the delivery date, and which version of the returns policy was in force on it;
- the damage-reporting window, and whether the item is in stock;
- the playbook's order of remedies, and the approval threshold for a high-value order.

The difficulty is not *finding* the policy. It is **determining what the policy allows
for this case**.

Enterprise search returns documents. Retrieval-augmented generation (RAG) retrieves a
policy and has a language model guess how it applies. Praxis applies it:

```
Question
 → neural interpretation              what is being asked, about which record, with which facts
 → authorization                      only records this person may see enter the reasoning
 → entity resolution                  which order, or a general case if none is named
 → knowledge-graph traversal          order → product → category → policy version
 → policy version resolution          the version in force on the delivery date
 → symbolic rule evaluation           three-valued: satisfied / violated / unknown
 → deterministic decision             replacement, refund, cancel, return, or nothing
 → claim checking                     "the policy is 30 days" is compared with the policy
 → verification (10 checks)           failing answers are withheld, not released
 → evidence and explanation           decision, action, rules, versions, verification
```

The language model is never the authority for a decision. A decision that is not derived
by a rule is not released.

## 2. What an agent sees

Every answer is a **decision card**:

| Part | Content |
|---|---|
| Decision | Yes / No / an amount / a recommendation / needs information / not covered / needs review |
| Recommended action | What to do next, authored with the policy (e.g. "Get team-lead approval recorded on the order before issuing the remedy (SUP-2). Create a replacement order…") |
| Verification | Passed (with the number of checks), or needs review |
| Confidence | Computed deterministically from understanding, input support, whether the decision is determinate, and verification |
| Premise check | Any rejected claim ("you said 30 days; Returns policy version 2 sets 15") |
| Knowledge gap | Shown when the knowledge could not answer, with who owns the fix |
| Why this answer? | The steps, the rules applied with their formulas, and the policy version applied |
| Evidence | The policy clauses in the version that governed the case, plus the records used |
| Audit trace | Support and admin roles only: candidate intents, facts with provenance, each rule condition-by-condition, the neural–symbolic loop, and every verification check |

Customers see the decision, the reason and the evidence. The audit machinery is shown only
to roles that audit decisions.

## 3. What administrators can do (no code changes)

Knowledge & reliability console, for the `admin` role or anyone with `audit:read`:

- **Policies & rules.** Edit policy documents, rule files and records in the browser.
  - Every save is validated against the *whole* knowledge pack first: rule grammar,
    stratification, every rule citing a clause that exists, every goal provable, every
    record attribute declared, every relationship pointing at a real entity.
  - Invalid changes are rejected with the reason and nothing is written.
  - Every write is snapshotted and can be restored.
- **Policy versions.** A policy change is a new document version plus a new
  `policy_version` entity with an effective date. Cases are decided under the version in
  force on their own date.
- **Knowledge graph.** Browse entities and relations; add entities or change attributes
  through a form. Changes are layered over the curated base files.
- **Rule tester.** Evaluate any goal for a record or a described case, without asking a
  question in natural language.
- **Knowledge gaps.** Unanswerable questions, grouped by cause:
  - `uncovered_topic`: no policy exists;
  - `undefined_case`: the rule doesn't cover this case, e.g. *"Handling team is not
    defined for: 'their password reset email never arrives'"*;
  - `missing_policy_parameter`: a value the rule needs was never recorded;
  - `verification_failure`: an answer was derived but withheld.
- **Review queue.** Withheld, not-covered, clarification and low-confidence decisions, each
  with its full trace.
- **Reliability.** Benchmark results per system and category, labelled as frozen or post-hoc.
- **Access.** Who may reason over which entity types, declared in the schema, and the role
  permissions.

## 4. Why neuro-symbolic, and why a vertical

| | LLM only | LLM + RAG | LLM + RAG + basic rules | Praxis |
|---|---|---|---|---|
| Understands paraphrase | yes | yes | yes | yes (neural) |
| Knows company policy | no | retrieved text | retrieved text | executable rules |
| Applies policy to a record | guesses | guesses | yes, two-valued | yes, three-valued |
| Uses the policy version in force for the case | no | may retrieve both | newest only | yes |
| Rejects a false premise | rarely | sometimes | no | yes, checked against parameters and records |
| Says what is missing instead of guessing | rarely | rarely | no (unknown = false) | yes |
| Authorization before reasoning | n/a | retrieval filter | retrieval filter | yes, the reasoner sees only authorized facts |
| Withholds unverified decisions | no | no | no | yes |
| Logs why it couldn't answer | no | no | no | yes, classified gaps |

A generic enterprise chatbot competes with every other product built on a large language
model. A decision copilot for one workflow is judged on whether its decisions are right,
and that can be measured.

## 5. Measurable value — and what has actually been measured

The spec asks for claims like "reduced incorrect policy decisions by X%". These must come
from experiments, not marketing.

| Claim | Measured? | Where |
|---|---|---|
| Decision, calculation and policy-compliance accuracy against baselines on a frozen question set | **Yes**, for the offline systems | [NEURO_SYMBOLIC_ARCHITECTURE.md §5](NEURO_SYMBOLIC_ARCHITECTURE.md#5-evaluation), `docs/benchmark/` |
| Hallucination and unsupported-answer rates against LLM and LLM+RAG baselines | **Harness built, not yet run** — needs a hosted model key or the local model download | §5 "What has not been run" |
| Share of decisions verified, withheld, clarified, not covered, escalated; false premises rejected; latency | **Yes, live**, from each workspace's own traffic | Admin → Overview |
| Average handle time, first-contact resolution, escalation rate, agent training time, customer satisfaction | **No.** These need a pilot with real agents | [PILOT_STUDY_PROTOCOL.md](PILOT_STUDY_PROTOCOL.md) |

No figure for handle time, resolution rate or ROI is stated anywhere in this repository,
because none has been measured.

## 6. Commercial model

The product is priced for enterprises that each get measurable value from one workflow, not
for large numbers of casual users.

| Tier | For | Includes |
|---|---|---|
| **Pilot** | One department, one workflow (e.g. returns and refunds decisions) | Knowledge-pack onboarding from existing policy documents; the copilot; the reliability dashboard; a pilot study measuring handle time and decision quality against a control group |
| **Business** | Several support teams | Multiple knowledge packs; integrations with order, CRM and ticketing systems as graph sources; knowledge-gap workflows; policy version management |
| **Enterprise** | Regulated or large organisations | SSO; private deployment (the reasoning runs without a hosted model); a full audit trail export; human approval workflows; custom access policies; analytics |

Onboarding a customer means writing their policies as rules that cite clauses, and
connecting their records as graph entities. That knowledge pack and its evaluation set are
the switching cost.

## 7. Moat

The advantage is not "we use an LLM". It is:

- **Enterprise-specific symbolic knowledge:** executable, versioned policies that cite their
  source clauses.
- **Reliable reasoning:** three-valued, so "not known" is never silently "no".
- **Verification and auditability:** every decision can be traced to rules, records and
  versions, and failing decisions are withheld.
- **Vertical workflow integration:** remedies, approvals, routing.
- **Accumulated evaluation data:** frozen benchmark sets, logged gaps and reviewed
  decisions, which improve the knowledge pack over time.

## 8. Expansion

The engine is domain-free. No module under `backend/app/` names a Meridian concept. A new
vertical is a new knowledge pack: policies, rules with goals and inputs, access
declarations, and records.

| Vertical | Decisions |
|---|---|
| Customer support *(built)* | Return, refund, replacement, cancellation, delivery compensation, failed payments, approval escalation, goodwill exceptions, routing |
| HR *(partly built: leave)* | Leave, benefits, eligibility |
| Finance *(partly built: travel)* | Expenses, reimbursement caps, approvals, procurement thresholds |
| Insurance | Coverage eligibility, claim decisions, policy interpretation by version |
| Healthcare administration | Eligibility, coverage, prior-authorisation rules |
| Compliance | Policy validation, rule checking, audit support |
