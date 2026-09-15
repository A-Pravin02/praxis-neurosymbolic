# Pilot study protocol: measuring business outcomes

The offline benchmark measures whether decisions are *correct*. It cannot measure whether
agents become *faster* or customers are better served, because that needs real agents
handling real contacts. This protocol describes how a pilot measures those outcomes. No
result below exists yet; this document defines how one would be produced.

## Question

Compared with agents using their current tools (the policy wiki plus the order system),
do agents using the Support Decision Copilot:

1. resolve policy decisions faster (average handle time, time spent looking up policy);
2. make fewer incorrect or non-compliant decisions;
3. resolve more contacts on first contact, escalating less;
4. receive fewer customer complaints about the decisions?

## Design

- **Randomisation.** Randomised, controlled, and parallel. Agents are the unit of
  randomisation, stratified by tenure (under 6 months / 6 months or more), because tenure
  is the strongest predictor of handle time.
- **Arms.**
  - *Control:* the existing tools.
  - *Copilot:* the existing tools plus the copilot. Agents may ignore its recommendation;
    the actual decision is always the agent's.
- **Scope.** Contacts whose disposition is a return, refund, replacement, cancellation,
  delivery compensation, failed payment or goodwill request. These are the goals the
  knowledge pack covers. Other contacts are excluded from analysis but not from the
  agents' queues.
- **Duration.** One week of training plus warm-up (excluded from analysis), then four
  weeks of measurement.
- **Sample size.** To detect a 10% reduction in mean handle time, the planning figures are:
  - assumed coefficient of variation 0.6;
  - α = 0.05, power 0.8, two-sided;
  - intra-agent correlation 0.05, average 150 contacts per agent.
  
  Those give roughly 20 agents per arm. Recompute with the organisation's own baseline
  variance before starting.

## Measures and sources

| Outcome | Definition | Source |
|---|---|---|
| Average handle time | Talk + hold + after-contact work, per in-scope contact | Contact-centre platform |
| Policy lookup time | Seconds on policy pages during the contact (control); time from question to decision card (copilot) | Browser telemetry (control); `latency_ms` plus agent dwell (copilot) |
| Decision correctness | A blinded quality analyst re-decides a random 10% sample of contacts from the records and the policy in force; disagreement means incorrect | QA sample |
| Policy violations | QA-sampled decisions that contradict the policy in force (a subset of incorrect decisions) | QA sample |
| First-contact resolution | No repeat contact on the same order within 7 days | Ticketing system |
| Escalation rate | Contacts transferred to a team lead, other than approvals the policy requires | Ticketing system |
| Unsupported recommendations | Copilot decisions later judged incorrect by QA while the card said "Verified" | QA sample joined to `trace_json` |
| Customer satisfaction | Post-contact survey score | Survey tool |
| Agent training time | Hours until a new agent's QA correctness reaches the tenured median | QA plus HR |

The copilot already records, for every decision:
- the outcome, pipeline and decision label;
- verification status and each check;
- claims rejected and approval follow-ups;
- knowledge gaps and latency.

These are stored in `messages.trace_json` and aggregated at `GET /api/copilot/analytics`.

## Analysis

- **Handle time.** A mixed-effects model on log handle time, with arm as a fixed effect,
  agent as a random effect, and contact type and tenure as covariates. Report the ratio
  of geometric means with a 95% CI.
- **Binary outcomes** (correctness, first-contact resolution, escalation). A logistic
  mixed model with the same structure. Report risk differences.
- **Pre-registration.** Register the primary outcome (decision correctness) and secondary
  outcomes before unblinding.
- **Separate what the copilot said from what the agent did.** Report both "copilot
  recommendation correct" and "agent decision correct".

## Stating the results

A statement such as "Praxis reduced incorrect policy decisions by X%" is allowed only as:

> in a randomised pilot of N agents over four weeks, the rate of QA-judged incorrect
> decisions was A% in the control arm and B% in the copilot arm (difference D, 95% CI …)

A result from the offline benchmark is never presented as an operational outcome.
