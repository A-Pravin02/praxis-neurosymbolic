const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  PageBreak, LevelFormat, Footer, PageNumber, ImageRun,
} = require("docx");
const fs = require("fs");

const USABLE = 9360;               // Letter 12240 - 1440 margins each side
const ACCENT = "1F3864";
const MUTED = "5A5A5A";

const numbering = {
  config: [{
    reference: "bullets",
    levels: [{
      level: 0, format: LevelFormat.BULLET, text: "•",
      alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 460, hanging: 240 } } },
    }],
  }],
};

const styles = {
  default: {
    document: { run: { font: "Calibri", size: 21 }, paragraph: { spacing: { after: 140, line: 288 } } },
  },
  paragraphStyles: [
    { id: "Title", name: "Title", basedOn: "Normal", next: "Normal",
      run: { size: 40, bold: true, color: ACCENT, font: "Calibri" },
      paragraph: { spacing: { after: 100 } } },
    { id: "Subtitle", name: "Subtitle", basedOn: "Normal", next: "Normal",
      run: { size: 23, color: MUTED, font: "Calibri" },
      paragraph: { spacing: { after: 320 } } },
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 28, bold: true, color: ACCENT, font: "Calibri" },
      paragraph: { spacing: { before: 360, after: 160 },
                   border: { bottom: { color: "D0D7E5", style: BorderStyle.SINGLE, size: 8, space: 6 } } } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 24, bold: true, color: "2E4A7D", font: "Calibri" },
      paragraph: { spacing: { before: 260, after: 120 } } },
    { id: "Caption", name: "Caption", basedOn: "Normal", next: "Normal",
      run: { size: 18, italics: true, color: MUTED },
      paragraph: { spacing: { after: 200 } } },
  ],
};

// ---- helpers ---------------------------------------------------------------
const P = (text, opts = {}) => new Paragraph({ text, ...opts });

const rich = (runs, opts = {}) => new Paragraph({
  children: runs.map(r => typeof r === "string"
    ? new TextRun(r)
    : new TextRun({ text: r.t, bold: r.b, italics: r.i, font: r.code ? "Consolas" : undefined,
                    color: r.color, size: r.code ? 19 : undefined })),
  ...opts,
});

const bullet = (runs) => rich(runs, { numbering: { reference: "bullets", level: 0 } });

const H1 = (t) => new Paragraph({ text: t, heading: HeadingLevel.HEADING_1 });
const H2 = (t) => new Paragraph({ text: t, heading: HeadingLevel.HEADING_2 });

const codeBlock = (lines) => new Paragraph({
  children: lines.flatMap((l, i) => [
    ...(i ? [new TextRun({ break: 1 })] : []),
    new TextRun({ text: l, font: "Consolas", size: 18 }),
  ]),
  shading: { type: ShadingType.CLEAR, fill: "F4F6FA" },
  spacing: { before: 140, after: 180 },
  indent: { left: 200, right: 200 },
  border: {
    left: { color: ACCENT, style: BorderStyle.SINGLE, size: 12, space: 8 },
  },
});

// PNG dimensions straight from the IHDR chunk, so an image is scaled to the text
// column by its real aspect ratio rather than a guessed one.
function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}

const FIGDIR = process.env.FIGDIR || "figs";

function figure(name, caption, maxWidthPx = 600) {
  const path = `${FIGDIR}/${name}`;
  const { w, h } = pngSize(path);
  const width = Math.min(maxWidthPx, w);
  const height = Math.round(width * (h / w));
  const out = [new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 200, after: 60 },
    children: [new ImageRun({
      type: "png", data: fs.readFileSync(path),
      transformation: { width, height },
    })],
  })];
  if (caption) {
    out.push(new Paragraph({
      alignment: AlignmentType.CENTER, style: "Caption", text: caption,
    }));
  }
  return out;
}

const calloutBox = (title, body, fill = "FFF4E5", bar = "C77700") => new Paragraph({
  children: [
    new TextRun({ text: title, bold: true, size: 20 }),
    new TextRun({ break: 1 }),
    new TextRun({ text: body, size: 20 }),
  ],
  shading: { type: ShadingType.CLEAR, fill },
  spacing: { before: 180, after: 220 },
  indent: { left: 160, right: 160 },
  border: { left: { color: bar, style: BorderStyle.SINGLE, size: 18, space: 10 } },
});

function table(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const scaled = widths.map(w => Math.round(w / total * USABLE));
  const cell = (text, { bold = false, fill = null, code = false } = {}, w) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: fill ? { type: ShadingType.CLEAR, fill } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({
      spacing: { after: 0 },
      children: [new TextRun({ text, bold, size: code ? 18 : 19,
                               font: code ? "Consolas" : undefined,
                               color: bold && fill ? "FFFFFF" : undefined })],
    })],
  });
  return new Table({
    columnWidths: scaled,
    width: { size: USABLE, type: WidthType.DXA },
    rows: [
      new TableRow({
        tableHeader: true,
        children: headers.map((h, i) => cell(h, { bold: true, fill: ACCENT }, scaled[i])),
      }),
      ...rows.map((r, ri) => new TableRow({
        children: r.map((c, i) => cell(String(c),
          { fill: ri % 2 ? "F4F6FA" : null, code: /^[0-9.]+$|^[a-z_]+$/.test(String(c)) && i > 0 },
          scaled[i])),
      })),
    ],
  });
}

const spacer = () => new Paragraph({ text: "", spacing: { after: 120 } });

function titleBlock(title, subtitle, meta) {
  return [
    new Paragraph({ text: title, style: "Title" }),
    new Paragraph({ text: subtitle, style: "Subtitle" }),
    new Paragraph({
      children: meta.map((m, i) => new TextRun({
        text: m, size: 19, color: MUTED, break: i ? 1 : 0,
      })),
      spacing: { after: 300 },
      border: { top: { color: "D0D7E5", style: BorderStyle.SINGLE, size: 8, space: 8 } },
    }),
  ];
}

const footer = (label) => new Footer({
  children: [new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [
      new TextRun({ text: label + "   |   page ", size: 16, color: MUTED }),
      new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUTED }),
    ],
  })],
});

const PAGE = { size: { width: 12240, height: 15840 }, margin: { top: 1300, bottom: 1300, left: 1440, right: 1440 } };

// =============================================================================
// DOCUMENT 1 — Praxis
// =============================================================================
const doc1Children = [
  ...titleBlock(
    "Reducing Hallucination with a Neuro-Symbolic Verification Layer",
    "Project 1 — Praxis: Retrieval-Augmented Generation with Symbolic Proof and an Evidence Gate",
    ["Enterprise question answering over company policy, records and rules",
     "Technical report prepared for review — 25 September 2026"]),

  H1("1. The central idea"),
  rich([{ t: "A language model is never what decides. ", b: true },
        "It performs two jobs only: it understands what was asked, and it words the final answer. " +
        "Every fact, number and yes/no in between is produced by document retrieval or by a symbolic " +
        "rule engine. When a statement cannot be supported by evidence or derived from rules, the " +
        "system withholds it rather than producing it."]),
  rich(["This is the mechanism by which hallucination is reduced: not by instructing the model to be " +
        "careful, and not by asking it whether it is confident — a model cannot be trusted to lower " +
        "its own bar, and asking \"are you sure?\" measures nothing. The decision to answer is taken " +
        "in ordinary code, from measurable quantities."]),

  H1("2. Pipeline architecture"),
  ...figure("p1_architecture.png",
            "Figure 1 — The stages of Project 1, coloured by the layer that executes each one."),
  codeBlock([
    "QUERY",
    "  |  NEURAL     encode the question  (BAAI/bge-small-en-v1.5, ONNX, 384-dim, offline)",
    "  |  NEURAL     semantic parse into the closed schema (goal, inputs, clause)",
    "  |  HYBRID     ROUTER          choose pipeline A-G          -> section 4",
    "  |  SYMBOLIC   AUTHORIZATION   admit only permitted records -> section 5",
    "  |  SYMBOLIC   GROUNDING       resolve subject, check stated facts",
    "  |  SYMBOLIC   REASONER        three-valued proof over rules-> section 6",
    "  |  HYBRID     VERIFICATION    check each claim vs evidence -> section 7",
    "  |  HYBRID     EVIDENCE GATE   answer / caveat / abstain    -> section 8",
    "  |  NEURAL     REALIZER        word the verified conclusion only",
    "FINAL VERIFIED ANSWER",
  ]),
  calloutBox("Why the order matters",
    "Authorization runs BEFORE reasoning, so the symbolic engine only ever receives facts the asker " +
    "is permitted to see. A system that reasoned first and filtered afterwards would leak restricted " +
    "records through its own explanations.",
    "EAF1DD", "2E7D32"),

  H1("3. Input and output"),
  P("Input: a natural-language question together with the identity of the person asking (email and " +
    "roles). Identity is not decoration — it determines which records may enter the reasoning at all."),
  P("Output is a structured object, not a string:"),
  table(["Field", "What it carries"], [
    ["answer", "the wording presented to the user"],
    ["pipeline", "which of the seven routes handled the question, and why"],
    ["outcome", "answered / insufficient / clarification / denied"],
    ["citations", "the clause identifiers and record identifiers the answer rests on"],
    ["trace", "every stage executed, labelled NEURAL, SYMBOLIC or HYBRID"],
    ["confidence", "a number computed in code, with its factor breakdown"],
    ["verification", "per-claim status: VERIFIED, SUPPORTED, WEAK or CONTRADICTED"],
  ], [26, 74]),
  spacer(),

  new Paragraph({ children: [new PageBreak()] }),

  H1("4. Decision criteria — how a question is routed"),
  P("The router chooses one of seven pipelines and records its reasoning:"),
  table(["", "Pipeline", "Chosen when"], [
    ["A", "Knowledge retrieval", "the answer is a policy clause or a stored record"],
    ["B", "Policy reasoning", "a yes/no decision that the rules determine"],
    ["C", "Multi-hop reasoning", "an explanation following relations to a failed condition"],
    ["D", "Calculation", "an amount computed by rule arithmetic"],
    ["E", "Clarification", "cannot be pinned to one goal or one record"],
    ["F", "Insufficient knowledge", "IS a company question, but nothing covers it"],
    ["G", "General conversation", "NOT a company question at all"],
  ], [6, 26, 68]),
  spacer(),
  rich([{ t: "F and G are deliberately different failures. ", b: true },
        "\"What is the escalation window for a damaged laptop?\" is a company question the knowledge " +
        "pack does not cover; saying so is the honest answer, because guessing would invent policy. " +
        "\"Who won the 2018 World Cup?\" is not a company question at all, so answering it from the " +
        "company knowledge base would be a category error — the model answers it plainly, labelled " +
        "as its own knowledge, carrying no citation and no verification."]),

  H2("4.1 Why the symbolic route is chosen, and when it is not"),
  ...figure("p1_routing.png",
            "Figure 2 — The routing decision. The plain language model is reached only on the far right."),
  P("This is not a heuristic. It follows from the structure of the rule base for the matched goal:"),
  bullet([{ t: "a goal whose rules compute a value", b: true }, " → Calculation (D)"]),
  bullet([{ t: "a goal concluded by rules at all", b: true }, " → symbolic evaluation (B or C)"]),
  bullet([{ t: "a goal with no rules", b: true }, " → lookup (A)"]),
  bullet([{ t: "no goal matched, question is on-topic", b: true }, " → insufficient knowledge (F)"]),
  bullet([{ t: "no goal matched, question is off-topic", b: true }, " → plain language model (G)"]),
  P("The plain-model route is therefore reached only when the question has no connection to the " +
    "company at all. That test compares the question's content words against the vocabulary of the " +
    "knowledge pack itself — policy text, goal descriptions, schema predicate and entity-type names. " +
    "A single shared content word keeps the question inside the company pipeline."),
  calloutBox("A subtle design point",
    "Entity labels are deliberately excluded from that vocabulary. Whether a particular customer's " +
    "name appears in the graph is a fact about the records, and topicality is decided before " +
    "authorization has admitted anything. A test that consulted labels would answer differently for " +
    "two people permitted to see different records."),

  H2("4.2 Thresholds used"),
  table(["Constant", "Value", "Role"], [
    ["GOAL_ACCEPT", "0.60", "goal accepted on similarity alone"],
    ["GOAL_SUPPORTED", "0.55", "accepted if stated inputs or mentions corroborate it"],
    ["GOAL_SUGGEST", "0.45", "only good enough to offer as \"did you mean\""],
    ["RETRIEVAL_ACCEPT", "0.55", "clause relevant enough to answer an informational question"],
    ["TIE_WINDOW", "0.03", "goals this close are resolved by the rule graph, not by score"],
    ["KIND_WINDOW", "0.06", "a goal of the requested kind this close to the top is preferred"],
    ["PARSED_CLAUSE_FLOOR", "0.40", "floor for a clause the semantic parser itself selected"],
  ], [30, 14, 56]),
  spacer(),

  H2("4.3 Two symbolic checks applied to the neural interpretation"),
  rich([{ t: "Content-word floor. ", b: true },
        "A question containing no content word cannot select a goal, however close its embedding " +
        "happens to be. The bare question \"How much?\" scored 0.60 against a delivery-charge goal."]),
  rich([{ t: "Kind agreement. ", b: true },
        "The goal's kind must match what was asked. \"What is my reimbursement?\" (an amount) and " +
        "\"does my trip qualify?\" (a yes/no) sit a few hundredths apart in embedding space, and " +
        "answering the wrong one produces a fluent, correctly cited answer to a question nobody asked."]),
  P("Near-ties within 0.03 are broken by the rule graph — preferring the goal the question supplies " +
    "evidence for, then the prerequisite over the goal that depends on it — never by a rounding " +
    "difference in cosine similarity."),

  new Paragraph({ children: [new PageBreak()] }),

  H1("5. Retrieval (RAG) parameters"),
  table(["Parameter", "Value", "Note"], [
    ["chunk_target_tokens", "220", "target chunk size"],
    ["chunk_overlap_tokens", "40", "overlap between adjacent chunks"],
    ["embedding_dimension", "384", ""],
    ["retrieval_top_k", "24", "first-pass recall"],
    ["rerank_top_k", "8", "passed forward to verification"],
    ["embedding_provider", "semantic", "was hashing - see below"],
    ["encoder", "BAAI/bge-small-en-v1.5", "ONNX Runtime, offline, ~130 MB"],
  ], [30, 22, 48]),
  spacer(),
  P("Retrieval is hybrid: pgvector cosine search combined with PostgreSQL full-text lexical search, " +
    "fused and then reranked. Every span is authorization-filtered at the SQL level, and retrieved " +
    "documents are treated as untrusted data — a span may be quarantined, and quarantined spans are " +
    "excluded from the usable evidence count."),
  calloutBox("A defect found and corrected during this work",
    "Retrieval was configured to use feature hashing - hashed word and character n-grams. That is " +
    "lexical-semantic rather than a sentence encoder, so a paraphrase such as \"can I give this " +
    "back?\" could not reach a passage about returning an item: the two share no term, and " +
    "character n-grams do not bridge the gap. The right passage never entered the candidate set, " +
    "which no amount of reranking recovers. Measured on that pair: 0.151 under hashing, 0.769 " +
    "under the sentence encoder. Document retrieval now uses the same ONNX encoder the neural " +
    "layer already ran. On the seeded corpus, \"who do I contact if my laptop breaks\" retrieves " +
    "the IT helpdesk clause at 0.756 with no content word in common. Vectors from the two " +
    "providers are not comparable, so an existing corpus must be re-embedded when the provider " +
    "changes.",
    "EAF1DD", "2E7D32"),

  H1("6. The symbolic layer — three-valued logic"),
  P("Classical rule engines are two-valued: a condition holds or it does not, and a missing fact " +
    "counts as false. That is precisely why rule-based assistants answer \"no\" to questions they " +
    "ought to be able to answer. This reasoner keeps three outcomes apart:"),
  table(["Value", "Meaning", "What it enables"], [
    ["satisfied", "the knowledge proves the condition", ""],
    ["violated", "the knowledge refutes the condition", "why-not explanations"],
    ["unknown", "nobody has stated what it depends on", "names the missing fact; conditional answers"],
  ], [18, 44, 38]),
  spacer(),
  rich([{ t: "Open and closed worlds. ", b: true },
        "A company record is a closed world: if an order has no delivery date, it was not delivered. " +
        "A hypothetical the user describes (\"if I spent Rs 8,750\") is an open world: anything " +
        "unstated is unknown, not false. The reasoner is told which subjects are open."]),
  rich([{ t: "Inference confidence. ", b: true },
        "A derived fact takes confidence = min(premise confidences) × rule confidence, so it can " +
        "never be more certain than the weakest premise it rests upon."]),
  rich([{ t: "Contradictions. ", b: true },
        "While a conflict is unresolved, neither value may be asserted — stating one and mentioning " +
        "the disagreement afterwards would let the reader take the first number as the answer. A " +
        "conflict resolved by version ordering permits the newer value and reports both."]),

  new Paragraph({ children: [new PageBreak()] }),

  H1("7. Verification of every claim"),
  P("Each claim in the drafted answer is assigned a status before the answer may be shown:"),
  table(["Status", "Awarded when"], [
    ["VERIFIED", "derived by the rule engine from verified premises; or from the user's own profile record; or a computed statistic whose figure matches the fact's value and appears in the retrieved evidence"],
    ["SUPPORTED", "corroborated by evidence, scored 0.35 × lexical + 0.65 × numeric agreement"],
    ["WEAK", "the derivation's premises are not fully evidence-backed"],
    ["CONTRADICTED", "sources disagree, or the value was superseded by a newer source"],
  ], [20, 80]),
  spacer(),
  P("Evidence relevance is computed as 0.65 × best span score + 0.35 × mean of the top spans. A claim " +
    "whose numbers do not appear in the supporting span has its score multiplied by 0.4 — this is the " +
    "check that catches a fluent sentence carrying an invented figure."),

  H1("8. The evidence gate — deciding to answer or abstain"),
  P("Hard stops are applied in order: an unresolved contradiction reports the conflict; required " +
    "evidence that is absent forces abstention; evidence relevance below 0.18 forces abstention; and " +
    "no supported claim forces abstention."),
  H2("8.1 Confidence formula"),
  ...figure("p1_confidence.png",
            "Figure 3 — The weights in each confidence blend. Determinacy of the proof dominates."),
  codeBlock([
    "confidence = 0.34 * mean_support",
    "           + 0.22 * evidence_relevance",
    "           + 0.14 * min(1, best_retrieval_score)",
    "           + 0.30 * verified_share",
    "           - 0.08 * min(unsupported_claims, 4)",
    "",
    "if a symbolic conclusion exists and is verified:  confidence = max(confidence, 0.82)",
    "clamped to the range [0.00, 0.97]",
  ]),
  rich(["Note the ceiling: ", { t: "0.97, never 1.00", b: true },
        ". The system does not claim certainty. When the relevance floor is waived, the 0.22 " +
        "relevance weight is redistributed proportionally across the remaining signals rather than " +
        "dropped, so the result stays a weighted average and cannot be inflated."]),
  H2("8.2 Decision thresholds"),
  table(["Setting", "Value", "Effect"], [
    ["evidence_relevance_threshold", "0.18", "below this, abstain"],
    ["claim_support_threshold", "0.40", "minimum for a claim to count as supported"],
    ["answer_confidence_threshold", "0.55", "at or above this, ANSWER"],
    ["caveat_confidence_threshold", "0.40", "at or above this, ANSWER WITH CAVEAT; below, ABSTAIN"],
  ], [38, 14, 48]),
  spacer(),
  P("An answer given from general knowledge (pipeline G) is capped at 0.45 and always labelled, so it " +
    "can never be presented as grounded in company knowledge."),
  H2("8.3 A second confidence, for reasoned answers"),
  codeBlock([
    "score = 0.25 * understanding        (how well the goal was matched)",
    "      + 0.10 * stated_inputs        (confidence in facts the user supplied)",
    "      + 0.35 * symbolic_determinacy (how decisively the rules concluded)",
    "      + 0.15 * fact_support",
    "      + 0.15 * verification",
    "",
    "if the semantic encoder is unavailable:  score = score * 0.8",
  ]),
  P("Symbolic determinacy takes 1.00 when determined, 0.95 by default, 0.70 when conditional, 0.30 " +
    "when unknown or absent, and 0.20 on conflict. It carries the largest weight in the blend, which " +
    "states the design position outright: the determinacy of the proof matters more than similarity."),

  new Paragraph({ children: [new PageBreak()] }),

  H1("9. The system running, end to end"),
  P("Each row below was run against the live stack on 23 September 2026, signed in as a " +
    "customer of the demonstration workspace. Which pipeline a question reaches is the " +
    "router's decision, not a setting."),
  table(["Question", "Pipeline", "Conf.", "Outcome"], [
    ["am I eligible for free delivery?", "Policy reasoning", "1.00",
     "Verified, 8 checks. The rule is shown firing: membership = plus AND total 62,999 > 500"],
    ["what happens if my item arrives damaged", "Calculation", "0.98",
     "Computes her entitlement for her own order"],
    ["how long do I have to cancel an order", "Policy reasoning", "1.00",
     "Concludes the order can no longer be cancelled - already shipped"],
    ["who do I contact if my laptop breaks", "Knowledge retrieval", "0.56",
     "Cited. Shares no content word with the passage it found"],
    ["what does the refund policy say", "Knowledge retrieval", "0.82",
     "Answered from policy text, with citations"],
    ["can I return order ORD-22003?", "Access refused", "-",
     "The record belongs to another customer; the engine never receives it"],
    ["can I give this back?", "Clarification", "-",
     "Lists her actual orders and asks which one"],
    ["who won the 2018 world cup", "General conversation", "-",
     "Answered plainly, uncited, marked unverified"],
  ], [27, 16, 7, 50]),
  spacer(),
  P("437 backend tests pass. The production frontend build succeeds."),

  new Paragraph({ children: [new PageBreak()] }),

  H1("10. Results — measured against language-model baselines"),
  P("86 questions of the frozen test split, one knowledge pack, and the same "
    + "Qwen2.5-7B-Instruct serving every system that uses a model, so the only thing that "
    + "differs between rows is the architecture."),
  table(["System", "Accuracy", "Hallucination", "Unsupported", "Latency"], [
    ["the model alone", "0.058", "0.221", "1.000", "622 ms"],
    ["+ retrieval over the same corpus", "0.209", "0.198", "0.000", "592 ms"],
    ["+ a basic rule engine", "0.372", "0.151", "0.000", "12,656 ms"],
    ["Praxis", "0.733", "0.081", "0.000", "114 ms"],
  ], [34, 16, 18, 16, 16]),
  spacer(),
  rich([{ t: "Against standard retrieval on the same encoder, corpus and model: ", b: true },
        "accuracy 0.209 to 0.733, and hallucination 0.198 to 0.081 — a 59% relative "
        + "reduction. The unsupported-answer rate of 1.000 for the model alone is not a "
        + "defect of measurement: with no corpus it has no source for anything it says."]),
  calloutBox("One number runs the other way, and is reported rather than buried",
    "On the 33 questions that turn on a yes/no decision, the basic rule engine is right 28 "
    + "times and Praxis 24. All four systems answer the same 33, so this is not an "
    + "abstention artefact. An exact McNemar test on the paired outcomes gives p = 0.219: a "
    + "four-case difference, which is no measured difference. What separates the two systems "
    + "is everything around the decision — final-answer accuracy is double and hallucination "
    + "roughly half, because a two-valued engine that always applies the newest policy "
    + "version is confidently wrong exactly where those shortcuts bite. On ambiguous "
    + "questions it hallucinates at 0.43 against 0.14.",
    "E8F0FE", "1A73E8"),
  spacer(),
  rich([{ t: "The cost of the rule engine baseline is worth noting. ", b: true },
        "It answers in 12.7 seconds against 114 milliseconds, because it calls the model "
        + "twice per question where the symbolic path calls it not at all once the question "
        + "has been read."]),

  H1("11. Ablations — which component earns the result"),
  P("Each component removed in turn over 255 generated test cases, each ablation paired "
    + "against the full system by an exact McNemar test on the per-case outcomes."),
  table(["Removed", "What breaks", "p"], [
    ["Authorization before reasoning",
     "disclosure of records the asker may not see, 0.000 to 0.867", "6.1e-05"],
    ["Statement checking", "false-premise accuracy 0.90 to 0.00", "1.5e-08"],
    ["Three-valued logic",
     "missing-information accuracy 1.00 to 0.00; hallucination there 0.00 to 0.67", "0.002"],
    ["Version ordering", "policy-version accuracy 0.88 to 0.64", "0.0039"],
    ["The evidence gate", "nothing: 0 of 255 answers change", "1"],
  ], [30, 55, 15]),
  spacer(),
  rich([{ t: "Each of the first four fails where its mechanism predicts, ", b: true },
        "which is the strongest form this table can take: the component is not merely "
        + "correlated with the result, it is the thing that handles that category. Removing "
        + "authorization leaks 13 of 15 restricted records."]),
  calloutBox("The last row is a null result and is kept as one",
    "Removing the evidence gate changes nothing measurable — not one case, not even the "
    + "wording. The flag is honoured: the controller does release a failed report when it is "
    + "set. Verification simply never fails on this split, so there is nothing for the gate "
    + "to withhold. On this evidence it is a guarantee that costs nothing, not a mechanism "
    + "shown to work. The same null appeared independently in Project 2's validation stage.",
    "FDECEA", "B3261E"),
  spacer(),
  rich([{ t: "Two readings of that run that would otherwise mislead. ", b: true },
        "The hashing-encoder ablation records 0.000 hallucination on 0.000 coverage: it "
        + "answers nothing at all, so the zero is not a virtue. And every variant scores 0.00 "
        + "on questions no policy covers — see section 13."]),

  H1("12. Calibration — is the computed confidence honest?"),
  P("Confidence is derived in code from retrieval relevance, rule coverage and the "
    + "verification outcome, and the model is never asked how sure it is. That is a claim "
    + "about the design; this section is what turns it into a measurement."),
  table(["Population", "ECE", "MCE", "Brier", "Accuracy", "Mean confidence"], [
    ["all 86 cases", "0.192", "0.650", "0.203", "0.733", "0.779"],
    ["answered only (62)", "0.179", "0.650", "0.199", "0.758", "0.914"],
  ], [26, 14, 14, 14, 16, 16]),
  spacer(),
  rich([{ t: "The computed confidence is overconfident, and nearly binary. ", b: true },
        "A well-calibrated system is usually below 0.05 expected calibration error. Two "
        + "specific problems. The top bin holds 42 of the 86 cases, where the system claims "
        + "0.959 and is right 0.786 of the time. And the scale is barely used: nothing at all "
        + "falls between 0.1 and 0.5, so in practice confidence takes about three values."]),
  calloutBox("What this does and does not cost the argument",
    "Computing confidence in code rather than asking the model remains defensible on its own "
    + "terms: it is reproducible, inspectable, and cannot be talked up by a fluent model. But "
    + "computed is not the same as calibrated, and nothing here should be read as claiming "
    + "the number is a probability of being right. Fitting the weights on a development split "
    + "and reporting on a held-out one is the obvious next step and has not been done."),

  H1("13. What does not work"),
  rich([{ t: "Questions no policy covers are answered anyway. ", b: true },
        "On the 15 such cases in the generated test split the system scores 0.00 accuracy "
        + "and 0.87 hallucination. Asked whether an order can be gift-wrapped, it returns a "
        + "free-delivery ruling. This is the weakest result in the system."]),
  P("Three signals were measured for whether any of them separates these questions from "
    + "covered ones, before anything was changed. None does:"),
  table(["Signal", "Why it fails"], [
    ["out-of-vocabulary content words",
     "paraphrases have more of them — 5.00 against 3.73"],
    ["margin between the top two goals",
     "policy-version 0.019 and multi-hop 0.023 sit at or below not-covered's 0.021"],
    ["relevance of the best policy clause",
     "multi-hop 0.559 is indistinguishable from not-covered 0.554"],
  ], [34, 66]),
  spacer(),
  rich(["In the encoder's space these questions are genuinely close to covered ones, so no "
        + "threshold separates them. Replacing the understanding stage with a language-model "
        + "parser — which can name a goal as absent, where similarity cannot — moves the "
        + "category to 0.40 (p = 0.031) and the system's overall hallucination rate from "
        + "0.149 to 0.035. That is an improvement on the worst result in the system, and it "
        + "is still the worst result in the system: three in five such questions are answered "
        + "when they should be refused."]),
  rich([{ t: "That substitution is not free either. ", b: true },
        "Calculation accuracy falls from 1.00 to 0.78 (p = 0.002), and all ten lost cases "
        + "are the same question. Asked about \"my yearly holiday allowance\", the encoder "
        + "binds the question to the asker's service record and computes their entitlement; "
        + "the parser recites the policy clause instead, which in one case contradicts what "
        + "that person is owed. Neither component dominates, and neither has been adopted "
        + "over the other."]),
  rich([{ t: "One domain, and thresholds fitted to it. ", b: true },
        "Everything above is a single knowledge pack. The goal-acceptance threshold is now "
        + "swept on a development split rather than hand-set, but that split holds 21 "
        + "questions, so a single question moves the number by 4.8 points; and the retrieval "
        + "threshold does not move that objective at all and remains hand-set."]),

  H1("14. Where hallucination is actually prevented"),
  ...figure("comparison_properties.png",
            "Figure 5 — Project 1 against existing approaches. These are structural properties of each design, not measured scores."),
  table(["Failure mode", "What prevents it"], [
    ["An invented number", "the realizer only words the rule engine's computed value, and claim numbers must appear in the evidence"],
    ["A fluent answer to the wrong question", "kind-agreement check, and rule-graph tie-breaking"],
    ["Policy invented for an uncovered topic", "pipeline F states what is missing instead of guessing"],
    ["A company answer to an off-topic question", "pipeline G answers plainly, uncited, capped at 0.45"],
    ["Asserting one of two conflicting values", "an unresolved contradiction blocks both values"],
    ["A stale policy value", "version ordering; superseded values are marked CONTRADICTED"],
    ["Leaking records through explanations", "authorization precedes reasoning"],
    ["A missing fact treated as false", "three-valued logic returns unknown and names the gap"],
  ], [36, 64]),
  spacer(),
  calloutBox("Scope of the claim",
    "Hallucinations are reduced in enterprise question answering. They are not prevented, and they " +
    "are not solved. This system does not claim otherwise, and no evaluation in this project reports " +
    "a zero-hallucination result as a general property.",
    "FDECEA", "B3261E"),
];

// =============================================================================
// DOCUMENT 2 — Neuro-Symbolic KBQA
// =============================================================================
const doc2Children = [
  ...titleBlock(
    "Reducing Hallucination by Removing the Model's Authority to Assert",
    "Project 2 — Neuro-Symbolic Knowledge-Base Question Answering, without Retrieval",
    ["Compositional question answering over a 16,960-entity knowledge base (KQA Pro)",
     "Technical report prepared for review — 25 September 2026"]),

  H1("1. The central idea"),
  rich([{ t: "A language model cannot state a fact it is never permitted to state. ", b: true },
        "In this system the model's only task is to translate a question into a small program. The " +
        "program is validated against the knowledge base, executed, and its result is the answer. " +
        "The model never sees a fact and never writes one."]),
  P("This is a stricter position than Project 1 takes. There, the model reads retrieved passages and " +
    "may misuse what it reads, so a verification layer must check it afterwards. Here the model is " +
    "never given a fact in the first place, so the only error it can make is to misunderstand the " +
    "question — and that residual error is exactly what the evaluation measures."),
  calloutBox("The distinguishing design decision",
    "There is no retrieval anywhere in this system: no documents, no vector store, no embeddings. " +
    "The knowledge base answers directly. This is what makes it a separate piece of work from " +
    "Project 1 rather than a variation of it.",
    "E8F0FE", "1A73E8"),

  H1("2. Pipeline architecture"),
  ...figure("p1_architecture.png",
            "Figure 1 — The stages of Project 1, coloured by the layer that executes each one."),
  codeBlock([
    "QUESTION",
    "  |  SYMBOLIC   ENTITY LINKING   longest match over the KB's own name index",
    "  |  SYMBOLIC   SCHEMA           keys of the linked entities only (max 40)",
    "  |  NEURAL     MODEL            writes a program over a closed vocabulary",
    "  |  SYMBOLIC   VALIDATION       every operator, name and argument must exist",
    "  |  SYMBOLIC   REPAIR           spelling (>=0.90) and wiring, or fail",
    "  |  SYMBOLIC   EXECUTION        over the KB, carrying provenance",
    "  |",
    "ANSWER   or   ABSTENTION with a reason",
  ]),
  P("The model is shown the question, the operator list, seven worked examples, and the schema keys " +
    "of the entities that were linked — the knowledge base describing itself. It is shown no fact " +
    "value whatsoever, and an automated test enforces this."),

  H1("3. Input and output"),
  P("Input: a natural-language question. There is no user identity, because this is a public " +
    "knowledge base rather than a tenanted one — the key architectural difference from Project 1."),
  table(["Field", "What it carries"], [
    ["text", "the answer, or empty"],
    ["answered", "whether the system answered at all"],
    ["reason", "why it abstained, expressed in the knowledge base's own vocabulary"],
    ["program", "the executed program, step by step"],
    ["provenance", "the actual knowledge-base facts the execution touched"],
    ["repairs", "any name or wiring repairs that were applied"],
    ["ambiguities", "recorded when a name matched several entities"],
    ["raw", "the model's unmodified reply"],
  ], [24, 76]),
  spacer(),

  new Paragraph({ children: [new PageBreak()] }),

  H1("4. Dataset and parameters"),
  P("KQA Pro (Cao et al., ACL 2022; MIT licence) is a Wikidata subset that ships its own knowledge " +
    "base, so no Freebase dump and no document corpus is involved."),
  table(["Quantity", "Value"], [
    ["entities", "16,960"],
    ["concepts", "794"],
    ["attribute keys", "629"],
    ["relation predicates", "363"],
    ["qualifier keys", "275"],
    ["validation questions with gold programs and answers", "11,797"],
  ], [70, 30]),
  spacer(),
  P("Values are typed as string, quantity with unit, date or year. Comparison is three-valued, and a " +
    "unit mismatch is false for every operator — never silently coerced."),
  H2("4.1 Data splits, frozen before any system ran"),
  table(["Split", "Size", "Purpose"], [
    ["dev", "300", "may be read, failed on, and used to fix the system"],
    ["test", "1,000", "reserved for the reported result; run exactly once"],
  ], [16, 14, 70]),
  spacer(),
  P("The selection seed is fixed at 20260920 and both splits were written to disk before any system " +
    "was run against them, so the split cannot be tuned after seeing results."),
  H2("4.2 Parser parameters"),
  table(["Parameter", "Value"], [
    ["maximum words in a linked name", "10"],
    ["maximum schema keys shown", "40"],
    ["maximum candidate entities", "8"],
    ["model output limit", "500 tokens"],
    ["spelling-repair cutoff", "0.90"],
    ["decoding", "greedy (temperature 0)"],
  ], [70, 30]),
  spacer(),

  H1("5. The closed vocabulary — 27 operators"),
  codeBlock([
    "Find  FindAll  FilterConcept  FilterStr  FilterNum  FilterYear  FilterDate",
    "QFilterStr  QFilterNum  QFilterYear  QFilterDate  Relate  And  Or  What  Count",
    "SelectAmong  SelectBetween  QueryAttr  QueryRelation  QueryAttrUnderCondition",
    "QueryAttrQualifier  QueryRelationQualifier",
    "VerifyStr  VerifyNum  VerifyYear  VerifyDate",
  ]),
  P("Before anything is executed, validation rejects an unknown operator; the wrong number of inputs " +
    "or dependencies; a dependency that is not an earlier step (preventing cycles and forward " +
    "references); a name the knowledge base does not contain, checked per argument kind; and a final " +
    "step that returns a set of entities rather than an answer."),
  calloutBox("This is the core anti-hallucination mechanism",
    "Every argument is checked against the knowledge base by kind — entity, concept, attribute, " +
    "relation, qualifier, comparison operator, extremum, direction. A refusal such as \"no attribute " +
    "is called 'employee count'\" is produced by the knowledge base's own vocabulary, not by a guess " +
    "or by a model's self-assessment.",
    "E8F0FE", "1A73E8"),

  new Paragraph({ children: [new PageBreak()] }),

  H1("6. Decision criteria — answer or abstain"),
  rich([{ t: "There is no confidence score and no threshold in this system. ", b: true },
        "This is the deepest difference from Project 1, and it is deliberate: a score would be a " +
        "second quantity to calibrate and to defend. The decision is binary and mechanical."]),
  table(["Condition", "Outcome"], [
    ["the reply is not valid JSON, or not a program", "abstain"],
    ["any operator, name or argument fails validation", "abstain, after repair is attempted"],
    ["execution raises an error", "abstain, reporting the executor's reason"],
    ["execution succeeds", "answer — the execution result IS the answer"],
  ], [62, 38]),
  spacer(),
  H2("6.1 The two repairs, and their limits"),
  rich([{ t: "Spelling repair ", b: true },
        "uses a similarity cutoff of 0.90 and applies only when the match is unique. " +
        "\"cancelation\" becomes \"cancellation\"; a plausible but wrong name does not survive."]),
  rich([{ t: "Wiring repair ", b: true },
        "corrects dependency arity and ordering that the model got wrong."]),
  rich([{ t: "The content is never repaired. ", b: true },
        "A program naming an entity outside the knowledge base is refused, not coerced into the " +
        "nearest available one."]),
  H2("6.2 Why there is no \"ordinary language model\" route here"),
  P("Project 1 routes some questions to a plain model answer, because a company assistant must be " +
    "able to handle ordinary conversation. Project 2 has exactly one job — to answer from this " +
    "knowledge base — so there is no legitimate off-topic path. Language-model-only behaviour exists " +
    "in this project solely as a measured baseline and as an ablation, never as a fallback inside " +
    "the product."),

  H1("7. Systems compared"),
  ...figure("comparison_properties.png",
            "Figure 2 — Project 2 against existing approaches. Structural properties of each design, not measured scores."),
  table(["System", "Where the answer comes from"], [
    ["llm_only", "the model, from its training (60-token limit)"],
    ["llm_cot", "the model, after step-by-step reasoning (400-token limit)"],
    ["llm_selfcheck", "the model, after checking its own answer"],
    ["neurosymbolic", "the model writes a program; the knowledge base answers"],
    ["gold_program", "the dataset's own program, executed — the ceiling a parser aims at"],
  ], [24, 76]),
  spacer(),
  P("Five ablations each remove exactly one guarantee: the schema shown to the model, name " +
    "validation, name repair, shape repair, and abstention itself. The last of these releases the " +
    "model's guess instead of abstaining, and exists to quantify what abstention actually buys."),

  new Paragraph({ children: [new PageBreak()] }),

  H1("8. Metrics"),
  table(["Metric", "Definition"], [
    ["accuracy", "matches the dataset's answer"],
    ["hallucination rate", "answered, and wrong"],
    ["coverage", "answered at all"],
    ["selective accuracy", "accuracy among the questions that were answered"],
    ["unsupported answer rate", "the answer is not a name, value, count or yes/no the KB contains"],
    ["abstention rate", "the complement of coverage"],
  ], [26, 74]),
  spacer(),
  calloutBox("Why hallucination is always reported together with coverage",
    "Either number alone is meaningless. A system that abstains on everything records zero " +
    "hallucinations; a system that answers everything records full coverage. Only the pair describes " +
    "behaviour. Abstention is scored as neither correct nor hallucinated.",
    "EAF1DD", "2E7D32"),
  P("Rates carry 95% bootstrap confidence intervals, and systems are compared using an exact McNemar " +
    "test on paired per-question outcomes."),

  H1("9. Verified status of the symbolic layer"),
  P("Running the dataset's own gold programs through this executor must reproduce the dataset's own " +
    "answers. This is the check that bounds how much of any later error belongs to the executor " +
    "rather than to the model that wrote the program."),
  ...figure("p2_executor.png",
            "Figure 3 — The executor checked against the dataset's own gold programs."),
  table(["Check", "Result"], [
    ["all 11,797 validation questions", "11,735 correct — 0.9947"],
    ["the 300-question development split", "1.000 accuracy at 1.000 coverage, zero hallucinations"],
  ], [58, 42]),
  spacer(),
  P("Reaching that figure required correcting five classes of genuine defect in the executor — date " +
    "format handling, extremum wording, concepts appearing as nodes, empty-set propagation, and " +
    "distinct counting. Each was found by this check rather than assumed away."),

  H1("10. Results"),
  ...figure("p2_results.png",
            "Figure 4 — Measured on the 300-question development split. Every bar is a completed run."),
  P("Qwen2.5-7B-Instruct, 4-bit, one run each on the 300-question development split."),
  table(["Metric", "cot", "direct", "selfcheck", "open-book", "neuro-symbolic", "gold"], [
    ["Hallucination rate", "0.337", "0.230", "0.123", "0.140", "0.017", "0.000"],
    ["Selective accuracy", "0.205", "0.258", "0.315", "0.567", "0.886", "1.000"],
    ["Answer not in the KB", "0.213", "0.083", "0.067", "0.043", "0.000", "0.000"],
    ["Accuracy", "0.087", "0.080", "0.057", "0.183", "0.130", "1.000"],
    ["Coverage", "0.423", "0.310", "0.180", "0.323", "0.147", "1.000"],
  ], [24, 11, 11, 13, 14, 16, 11]),
  spacer(),
  calloutBox("The open-book baseline is the comparison that counts",
    "Every other baseline answers from training memory, so beating it shows only that "
    + "consulting a knowledge base beats not consulting one - nothing about architecture. "
    + "The open-book baseline receives the same entities' facts the executor reads, "
    + "rendered into its prompt, and answers directly. For \"What is the duration of the "
    + "film Casablanca?\" its prompt contains the line \"Casablanca | duration | 102 "
    + "minute\": the answer is present. What still differs is only who composes the answer "
    + "- a language model reading facts, or a program executing over them.",
    "E8F0FE", "1A73E8"),
  spacer(),
  H2("10.1 What the comparison actually shows"),
  rich([{ t: "The guarantee survives the fair test. ", b: true },
        "Given the same facts, with the answer present in its prompt, the open-book model " +
        "still hallucinates eight times as often: 0.140 against 0.017. The paired exact " +
        "McNemar test gives p = 9.3 x 10^-9 - forty-one questions hallucinated by it alone " +
        "against four by the neuro-symbolic system. That difference is attributable to the " +
        "architecture, not to access to information."]),
  rich([{ t: "And the open-book model is the more accurate system. ", b: true },
        "It reaches 0.183 accuracy against 0.130 (p = 0.048), because it answers 2.2 times " +
        "as many questions. It wins on volume; the neuro-symbolic system wins on reliability."]),
  calloutBox("The claim this supports is a trade, not a sweep",
    "The neuro-symbolic layer trades coverage for reliability. Given identical facts, a "
    + "language model answers roughly twice as many questions and gets more of them right "
    + "in absolute terms, but is wrong eight times as often when it does answer. Where a "
    + "confident wrong answer costs more than a missed right one, the symbolic path is "
    + "preferable. Against the closed-book baselines the gap is larger still - 0.230 for "
    + "direct answering and 0.337 for chain-of-thought - but those comparisons carry less "
    + "weight, because those systems could not see the knowledge base at all."),
  H2("10.2 Four results that are easy to miss"),
  rich([{ t: "Chain-of-thought makes it worse. ", b: true },
        "Asking the model to reason step by step before answering raises hallucination from " +
        "0.230 to 0.337. More reasoning produced more confident wrong answers, not fewer."]),
  rich([{ t: "Self-checking helps, and is not close. ", b: true },
        "Asking the model to check its own answer halves hallucination against answering " +
        "directly, 0.230 to 0.123 - but it achieves that by abstaining more often (coverage " +
        "falls from 0.310 to 0.180) rather than by being more often right, and the rate is " +
        "still seven times the neuro-symbolic one. A model auditing itself is working from " +
        "the same knowledge that produced the answer."]),
  rich([{ t: "The unsupported-answer rate is exactly zero. ", b: true },
        "Every answer the system gave was a value the knowledge base actually contains. It " +
        "is structurally incapable of producing anything else."]),
  rich([{ t: "Coverage is the price, and it is real. ", b: true },
        "It answers 14.7% of questions against the language model's 31%: this 7B model " +
        "writes a valid program for roughly one question in seven. When it does answer, it " +
        "is right 89% of the time against 26%. Accuracy is nonetheless the highest of any " +
        "system measured (p = 0.049 against answering directly, p = 0.0032 against " +
        "self-checking): it abstains far more and still gets more questions outright right."]),
  P("The gold-program column is the ceiling: the dataset's own programs, executed by this " +
    "engine, score 1.000 at full coverage. That separates parser error from executor error, " +
    "and shows the symbolic layer is not what limits the result."),
  P("The model is served locally in 4-bit. On a 6 GB card the output layer must be " +
    "quantized too; that is a different quantization rather than a free optimisation - on " +
    "sixteen replayed parser prompts, four produced a different program. It is therefore " +
    "applied uniformly to every system, run against a fresh completion cache, and disclosed. " +
    "Results obtained under earlier configurations are archived, never merged."),
  calloutBox("What is not yet reported",
    "The five ablations are still running, and the frozen 1,000-question test split has not " +
    "been touched. It will be run exactly once. Nothing above comes from it.",
    "FDECEA", "B3261E"),

  new Paragraph({ children: [new PageBreak()] }),

  new Paragraph({ children: [new PageBreak()] }),

  H1("11. Ablations — which guarantee does the work"),
  P("Each row removes exactly one thing from the full system, over the same 300 questions."),
  table(["Removed", "Hallucination", "Coverage", "Accuracy", "Not in KB"], [
    ["nothing (full system)", "0.017", "0.147", "0.130", "0.000"],
    ["the schema shown to the model", "0.000", "0.003", "0.003", "0.000"],
    ["name validation", "0.010", "0.140", "0.130", "0.000"],
    ["name repair", "0.010", "0.140", "0.130", "0.000"],
    ["shape repair", "0.013", "0.133", "0.120", "0.000"],
    ["abstention", "0.207", "0.393", "0.187", "0.073"],
  ], [34, 18, 16, 16, 16]),
  spacer(),
  rich([{ t: "The schema is what makes it work at all. ", b: true },
        "Without the knowledge base describing itself in the prompt, coverage collapses " +
        "from 14.7% to 0.3%: the model cannot guess a valid vocabulary unaided. Nothing " +
        "else in the pipeline matters if this is absent."]),
  rich([{ t: "Abstention is what makes it safe. ", b: true },
        "Releasing the model's guess instead of abstaining raises hallucination from 0.017 " +
        "to 0.207 - twelvefold - and the unsupported-answer rate from 0.000 to 0.073, so " +
        "answers appear that the knowledge base could not contain. Accuracy rises to 0.187: " +
        "the same trade as the open-book baseline, more answers and more of them right, at " +
        "twelve times the rate of confident error."]),
  calloutBox("Three of the five ablations show no measurable effect",
    "Validation, name repair and shape repair change nothing detectable on this split. That "
    + "is reported because it is what the runs say. The likely explanation is that they are "
    + "largely redundant given the schema: a model shown the available attributes and "
    + "relations rarely proposes a name outside them, so the check has little left to "
    + "reject. They remain a guarantee rather than an optimisation - a bound on what can "
    + "happen, not a component that improves the average case - but this evidence does not "
    + "support presenting them as the mechanism behind the result. A dataset with noisier "
    + "entity linking would be the way to test them properly."),

  H1("12. Chatbot mode"),
  P("A knowledge base cannot answer everything, and the guarantee this system demonstrates " +
    "exists precisely because its vocabulary is closed. So the application offers both " +
    "behaviours and never lets them be confused."),
  P("With chatbot mode off - the default, and the system the results above measure - a " +
    "question the knowledge base cannot answer is abstained on. With it on, the model " +
    "answers from its own knowledge instead, and that answer is returned as unverified: an " +
    "amber verdict rather than a green one, its own neural stage in the trace, and the " +
    "statement that nothing checked it."),
  table(["", "Knowledge base answered", "Model answered"], [
    ["Verdict", "green, VERIFIED", "amber, UNVERIFIED"],
    ["What it shows", "the facts it read", "\"Nothing checked this answer - it may be wrong\""],
    ["Typical latency", "7-50 ms", "0.3-2.4 s"],
  ], [20, 40, 40]),
  spacer(),
  rich([{ t: "The pairing worth demonstrating. ", b: true },
        "Ask \"Tell me about Michael Curtiz\" and then \"How many films did Michael Curtiz " +
        "direct?\". Same subject, same model, same session: the first is an amber paragraph " +
        "nothing can check, the second is a green 4 with the knowledge-base facts behind it."]),
  calloutBox("Amber does not mean the knowledge base lacks the fact",
    "\"Who is the director of Casablanca?\" falls back to the model - yet the knowledge base " +
    "holds that fact, and answers a differently-phrased question about the same director " +
    "correctly. Validation refused the program the model wrote (\"the last step returns a " +
    "set of entities, not an answer\"). Amber means the symbolic path did not produce this " +
    "answer, not that the knowledge base does not know it. This path also uses an ordinary " +
    "assistant prompt and is not the evaluated system: no number in section 10 describes it."),

  H1("13. Where hallucination is actually prevented"),
  table(["Failure mode", "What prevents it"], [
    ["An invented fact", "the model never emits a fact — only a program"],
    ["An invented entity, attribute or relation", "per-kind name validation against the knowledge base"],
    ["A plausible but wrong name silently accepted", "repair requires a unique match at 0.90 or above"],
    ["Unit confusion", "a unit mismatch is false for every operator"],
    ["Missing data treated as a value", "empty sets propagate; execution fails and the system abstains"],
    ["A confident guess on an unparseable question", "abstention with a reason, never a fallback"],
    ["An answer outside what the KB could contain", "measured directly as the unsupported answer rate"],
  ], [40, 60]),
  spacer(),
  calloutBox("Scope of the claim",
    "The guarantee concerns what can be asserted. The model can still misunderstand a question and " +
    "write a valid program that answers a different one. That residual error is precisely what the " +
    "evaluation measures, and it is why accuracy is reported alongside hallucination rate rather " +
    "than in place of it.",
    "FDECEA", "B3261E"),
];

// ---- build -----------------------------------------------------------------
function build(children, footerLabel) {
  return new Document({
    styles, numbering,
    sections: [{ properties: { page: PAGE }, footers: { default: footer(footerLabel) }, children }],
  });
}

async function main() {
  const jobs = [
    [build(doc1Children, "Project 1 — Praxis: RAG + Neuro-Symbolic"),
     process.argv[2]],
    [build(doc2Children, "Project 2 — Neuro-Symbolic KBQA"),
     process.argv[3]],
  ];
  for (const [doc, out] of jobs) {
    const buf = await Packer.toBuffer(doc);
    fs.writeFileSync(out, buf);
    console.log("wrote", out, (buf.length / 1024).toFixed(0) + " KB");
  }
}
main().catch(e => { console.error(e); process.exit(1); });
