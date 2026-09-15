# Final demonstration: one support problem, four systems

Generated 2026-09-15 15:26 UTC by `scripts/demo_copilot.py` from actual runs. Language model for the baselines: **none configured**. Asked as `support@meridian.test` (support role).

**What the policy actually implies.** Under Returns policy version 2 (in force from 1 Sep 2026, so it governs an item delivered 12 days before 14 Sep 2026) damaged items must be reported within **15 days**, not 30. The laptop is in stock, so the playbook's first remedy is a **replacement**; the ₹62,999 order is above the ₹25,000 threshold, so **team-lead approval** is required first (SUP-2).

## The demonstration question

> My customer's laptop arrived damaged 12 days ago and they want a replacement. What can I offer?

### LLM only

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### LLM + RAG

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### LLM + RAG + basic rules

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### Praxis (neuro-symbolic)

```text
The recommended resolution for the customer's order is to offer a replacement of the same product: the item qualifies for a replacement: it arrived damaged, defective or wrong, was reported in time, and the product is in stock.
No specific order was named, so this is evaluated as a general case using what you said and the policies that apply. Team-lead approval is required before issuing a remedy on the customer's order: the order total of ₹62,999 is above the ₹25,000 threshold, so a team lead must approve the remedy before it is issued. Applied Returns policy version 2, in force on 2 Sep 2026.
```

- **Decision:** Offer a replacement of the same product
- **Recommended action:** Get team-lead approval recorded on the order before issuing the remedy (SUP-2). Create a replacement order for the same product and arrange collection of the damaged item. No refund is issued.
- **Verification:** passed (9 checks)
- **Confidence:** 0.88
- **Policy version:** Returns policy version 2 — the delivery date is 2 Sep 2026
- **Premise check:** no claim about the policy
- **Rules applied:**
  - `R-SUP-REPL` Offer a replacement [SUP-1, RET-5]: `replacement available = yes → recommended resolution = replacement`
  - `R-RET-R` Replacement for a damaged or wrong item [RET-5]: `return allowed = yes AND return reason = damaged on arrival AND damaged on arrival in ['damaged on arrival', 'wrong item'] AND days since delivery = 12 AND returns policy = Returns policy version 2 AND damage report days = 15 AND 12 ≤ 15 AND contains = Aurora 14 Laptop AND in stock = yes → replacement available = yes`
  - `R-RET-D` Damaged, defective or wrong item [RET-4]: `order status = delivered AND return reason = damaged on arrival AND damaged on arrival in ['damaged on arrival', 'wrong item'] AND order category = Electronics AND not excluded from damage returns AND days since delivery = 12 AND returns policy = Returns policy version 2 AND damage report days = 15 AND 12 ≤ 15 → return allowed = yes`
  - `R-CLK-IMP` A stated delivery age implies the item was delivered [RET-1]: `days since delivery = 12 → order status = delivered`
  - `R-KG-CAT` Category of an ordered item [RET-1]: `contains = Aurora 14 Laptop AND product category = Electronics → order category = Electronics`
  - `R-SUP-APPROVAL` Team-lead approval for a high-value remedy [SUP-2]: `recommended resolution = replacement AND replacement in ['replacement', 'full refund', 'return and refund after inspection'] AND order total = ₹62,999 AND approval threshold = ₹25,000 AND ₹62,999 > ₹25,000 → approval required = yes`
  - `R-KG-PRICE` A general case is valued at the product's list price [SUP-2]: `contains = Aurora 14 Laptop AND price = ₹62,999 → order total = ₹62,999`
- **The ten steps, as recorded in the trace:**
  1. Neural interpretation — request type `resolve`, goal `recommended_resolution`, stated inputs `return_reason=damaged_on_arrival`, `days_since_delivery=12.0`
  2. Entity resolution — ['case:order'] (general case: True)
  3. Authorized data retrieval — `support` access, 3 records withheld from the reasoner
  4. Knowledge-graph traversal — approval_required, approval_threshold, cancellation_allowed, contains, damage_report_days, days_since_delivery, delivery_date, in_stock, order_category, order_status, order_total, plus_extension_days, price, product_category, recommended_resolution, replacement_available, return_allowed, return_reason, return_window_days, returns_policy, today
  5. Policy version resolution — Returns policy version 2
  6. Symbolic rule evaluation — 7 rules applied, 2 refuted
  7. Deterministic decision — `replacement`
  8. Verification — fact validity ✓, authorization ✓, policy applicability ✓, policy version ✓, symbolic provenance ✓, supported numbers ✓, contradictions ✓, answer agreement ✓, unsupported claims ✓
  9. Evidence — RET-1 v2, RET-4 v2, RET-5 v2, SUP-1 v1, SUP-2 v1
  10. Natural-language explanation — the answer above, composed only from the verified conclusion

## The hallucination test: a false premise

> The replacement policy is 30 days. My customer's laptop arrived damaged 12 days ago and they want a replacement. What can I offer?

### LLM only

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### LLM + RAG

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### LLM + RAG + basic rules

*Not run: no language model is configured (`LLM_PROVIDER=mock`). It is not simulated.*

### Praxis (neuro-symbolic)

```text
You said the policy allows 30 days, but Returns policy version 2 (in force from 1 Sep 2026) sets the damage report days at 15, so I've applied the policy as written.
The recommended resolution for the customer's order is to offer a replacement of the same product: the item qualifies for a replacement: it arrived damaged, defective or wrong, was reported in time, and the product is in stock.
No specific order was named, so this is evaluated as a general case using what you said and the policies that apply. Team-lead approval is required before issuing a remedy on the customer's order: the order total of ₹62,999 is above the ₹25,000 threshold, so a team lead must approve the remedy before it is issued. Applied Returns policy version 2, in force on 2 Sep 2026.
```

- **Decision:** Offer a replacement of the same product
- **Recommended action:** Get team-lead approval recorded on the order before issuing the remedy (SUP-2). Create a replacement order for the same product and arrange collection of the damaged item. No refund is issued.
- **Verification:** passed (9 checks)
- **Confidence:** 0.84
- **Policy version:** Returns policy version 2 — the delivery date is 2 Sep 2026
- **Premise check:** claimed 30 days, Returns policy version 2 (in force from 1 Sep 2026) sets the damage report days at 15, so the policy as written was applied
- **Rules applied:**
  - `R-SUP-REPL` Offer a replacement [SUP-1, RET-5]: `replacement available = yes → recommended resolution = replacement`
  - `R-RET-R` Replacement for a damaged or wrong item [RET-5]: `return allowed = yes AND return reason = damaged on arrival AND damaged on arrival in ['damaged on arrival', 'wrong item'] AND days since delivery = 12 AND returns policy = Returns policy version 2 AND damage report days = 15 AND 12 ≤ 15 AND contains = Aurora 14 Laptop AND in stock = yes → replacement available = yes`
  - `R-RET-D` Damaged, defective or wrong item [RET-4]: `order status = delivered AND return reason = damaged on arrival AND damaged on arrival in ['damaged on arrival', 'wrong item'] AND order category = Electronics AND not excluded from damage returns AND days since delivery = 12 AND returns policy = Returns policy version 2 AND damage report days = 15 AND 12 ≤ 15 → return allowed = yes`
  - `R-CLK-IMP` A stated delivery age implies the item was delivered [RET-1]: `days since delivery = 12 → order status = delivered`
  - `R-KG-CAT` Category of an ordered item [RET-1]: `contains = Aurora 14 Laptop AND product category = Electronics → order category = Electronics`
  - `R-SUP-APPROVAL` Team-lead approval for a high-value remedy [SUP-2]: `recommended resolution = replacement AND replacement in ['replacement', 'full refund', 'return and refund after inspection'] AND order total = ₹62,999 AND approval threshold = ₹25,000 AND ₹62,999 > ₹25,000 → approval required = yes`
  - `R-KG-PRICE` A general case is valued at the product's list price [SUP-2]: `contains = Aurora 14 Laptop AND price = ₹62,999 → order total = ₹62,999`
- **The ten steps, as recorded in the trace:**
  1. Neural interpretation — request type `resolve`, goal `recommended_resolution`, stated inputs `return_reason=damaged_on_arrival`, `days_since_delivery=12.0`
  2. Entity resolution — ['case:order'] (general case: True)
  3. Authorized data retrieval — `support` access, 3 records withheld from the reasoner
  4. Knowledge-graph traversal — approval_required, approval_threshold, cancellation_allowed, contains, damage_report_days, days_since_delivery, delivery_date, in_stock, order_category, order_status, order_total, plus_extension_days, price, product_category, recommended_resolution, replacement_available, return_allowed, return_reason, return_window_days, returns_policy, today
  5. Policy version resolution — Returns policy version 2
  6. Symbolic rule evaluation — 7 rules applied, 2 refuted
  7. Deterministic decision — `replacement`
  8. Verification — fact validity ✓, authorization ✓, policy applicability ✓, policy version ✓, symbolic provenance ✓, supported numbers ✓, contradictions ✓, answer agreement ✓, unsupported claims ✓
  9. Evidence — RET-1 v2, RET-4 v2, RET-5 v2, SUP-1 v1, SUP-2 v1
  10. Natural-language explanation — the answer above, composed only from the verified conclusion

