/**
 * A small, dependency-free markdown renderer.
 *
 * It builds React elements directly rather than setting innerHTML, so document
 * text that reaches the browser can never become markup. That matters here: the
 * assistant's answers quote retrieved documents, which are untrusted.
 */
import { Fragment, type ReactNode } from "react";

import type { Citation } from "../lib/types";

interface Props {
  text: string;
  citations?: Citation[];
  onCitationClick?: (index: number) => void;
}

const CITATION = /\[(\d{1,2})\]/g;
const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;

function renderInline(text: string, citations: Citation[],
                      onCitationClick?: (index: number) => void): ReactNode[] {
  const nodes: ReactNode[] = [];
  let key = 0;

  for (const chunk of text.split(INLINE)) {
    if (!chunk) continue;
    if (chunk.startsWith("**") && chunk.endsWith("**")) {
      nodes.push(<strong key={key++}>{chunk.slice(2, -2)}</strong>);
    } else if (chunk.startsWith("`") && chunk.endsWith("`")) {
      nodes.push(<code key={key++}>{chunk.slice(1, -1)}</code>);
    } else if (chunk.startsWith("*") && chunk.endsWith("*") && chunk.length > 2) {
      nodes.push(<em key={key++}>{chunk.slice(1, -1)}</em>);
    } else {
      let cursor = 0;
      CITATION.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = CITATION.exec(chunk)) !== null) {
        if (match.index > cursor) nodes.push(chunk.slice(cursor, match.index));
        const index = Number(match[1]);
        const citation = citations.find((c) => c.index === index);
        const className = `citation-chip${citation?.quarantined ? " quarantined" : ""}`;
        nodes.push(
          onCitationClick ? (
            <button
              key={key++}
              type="button"
              className={className}
              title={citation ? `${citation.document_title}${citation.section ? " - " + citation.section : ""}` : "Unknown source"}
              onClick={() => onCitationClick(index)}
            >
              {index}
            </button>
          ) : (
            <span key={key++} className={className}>{index}</span>
          ),
        );
        cursor = match.index + match[0].length;
      }
      if (cursor < chunk.length) nodes.push(chunk.slice(cursor));
    }
  }
  return nodes;
}

export default function Markdown({ text, citations = [], onCitationClick }: Props) {
  const blocks: ReactNode[] = [];
  const lines = (text ?? "").split("\n");
  let index = 0;
  let key = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";

    if (line.trimStart().startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? "").trimStart().startsWith("```")) {
        body.push(lines[index] ?? "");
        index += 1;
      }
      index += 1;
      blocks.push(
        <pre key={key++}>
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(<h3 key={key++}>{renderInline(heading[2] ?? "", citations, onCitationClick)}</h3>);
      index += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index] ?? "")) {
        items.push((lines[index] ?? "").replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ul key={key++}>
          {items.map((item, i) => (
            <li key={i}>{renderInline(item, citations, onCitationClick)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index] ?? "")) {
        items.push((lines[index] ?? "").replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ol key={key++}>
          {items.map((item, i) => (
            <li key={i}>{renderInline(item, citations, onCitationClick)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    if (line.trimStart().startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && (lines[index] ?? "").trimStart().startsWith(">")) {
        quote.push((lines[index] ?? "").replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(
        <blockquote key={key++}>{renderInline(quote.join(" "), citations, onCitationClick)}</blockquote>,
      );
      continue;
    }

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length && (lines[index] ?? "").trim()
           && !/^\s*([-*]|\d+[.)])\s+/.test(lines[index] ?? "")
           && !(lines[index] ?? "").trimStart().startsWith(">")
           && !(lines[index] ?? "").trimStart().startsWith("```")
           && !/^#{1,6}\s/.test(lines[index] ?? "")) {
      paragraph.push(lines[index] ?? "");
      index += 1;
    }
    blocks.push(
      <p key={key++}>{renderInline(paragraph.join(" "), citations, onCitationClick)}</p>,
    );
  }

  return <div className="prose">{blocks.map((block, i) => <Fragment key={i}>{block}</Fragment>)}</div>;
}
