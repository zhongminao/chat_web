import React from "react";

const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function safeHref(rawHref) {
  const href = String(rawHref || "").trim();
  if (!href) return null;
  if (href.startsWith("#") || href.startsWith("/")) return href;
  try {
    const url = new URL(href);
    return SAFE_LINK_PROTOCOLS.has(url.protocol) ? href : null;
  } catch {
    return null;
  }
}

function renderInline(text, keyPrefix = "i") {
  const nodes = [];
  let index = 0;

  function pushText(until) {
    if (until > index) {
      nodes.push(text.slice(index, until));
      index = until;
    }
  }

  while (index < text.length) {
    const codeStart = text.indexOf("`", index);
    const boldStart = text.indexOf("**", index);
    const linkStart = text.indexOf("[", index);
    const emStart = text.indexOf("*", index);
    const candidates = [codeStart, boldStart, linkStart, emStart]
      .filter((value) => value >= index)
      .sort((a, b) => a - b);

    if (candidates.length === 0) {
      nodes.push(text.slice(index));
      break;
    }

    const start = candidates[0];
    pushText(start);

    if (text.startsWith("`", index)) {
      const end = text.indexOf("`", index + 1);
      if (end < 0) {
        nodes.push(text.slice(index));
        break;
      }
      nodes.push(<code key={`${keyPrefix}-code-${index}`}>{text.slice(index + 1, end)}</code>);
      index = end + 1;
      continue;
    }

    if (text.startsWith("**", index)) {
      const end = text.indexOf("**", index + 2);
      if (end < 0) {
        nodes.push(text.slice(index));
        break;
      }
      nodes.push(<strong key={`${keyPrefix}-strong-${index}`}>{renderInline(text.slice(index + 2, end), `${keyPrefix}-strong-${index}`)}</strong>);
      index = end + 2;
      continue;
    }

    if (text.startsWith("[", index)) {
      const labelEnd = text.indexOf("]", index + 1);
      const hrefStart = labelEnd >= 0 && text[labelEnd + 1] === "(" ? labelEnd + 2 : -1;
      const hrefEnd = hrefStart >= 0 ? text.indexOf(")", hrefStart) : -1;
      if (hrefEnd < 0) {
        nodes.push(text[index]);
        index += 1;
        continue;
      }
      const label = text.slice(index + 1, labelEnd);
      const href = safeHref(text.slice(hrefStart, hrefEnd));
      nodes.push(
        href ? (
          <a key={`${keyPrefix}-link-${index}`} href={href} target="_blank" rel="noreferrer">
            {renderInline(label, `${keyPrefix}-link-${index}`)}
          </a>
        ) : (
          `[${label}](${text.slice(hrefStart, hrefEnd)})`
        )
      );
      index = hrefEnd + 1;
      continue;
    }

    if (text.startsWith("*", index)) {
      const end = text.indexOf("*", index + 1);
      if (end < 0) {
        nodes.push(text.slice(index));
        break;
      }
      nodes.push(<em key={`${keyPrefix}-em-${index}`}>{renderInline(text.slice(index + 1, end), `${keyPrefix}-em-${index}`)}</em>);
      index = end + 1;
      continue;
    }
  }

  return nodes;
}

function paragraphNodes(lines, keyPrefix) {
  return lines.flatMap((line, index) => {
    const nodes = renderInline(line, `${keyPrefix}-line-${index}`);
    return index === 0 ? nodes : [<br key={`${keyPrefix}-br-${index}`} />, ...nodes];
  });
}

export default function MarkdownContent({ content }) {
  const lines = String(content || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*(.*)$/);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre className="markdown-code-block" key={`code-${index}`}>
          <code>{code.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const Tag = `h${level}`;
      blocks.push(<Tag key={`heading-${index}`}>{renderInline(heading[2], `heading-${index}`)}</Tag>);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(<blockquote key={`quote-${index}`}>{paragraphNodes(quoteLines, `quote-${index}`)}</blockquote>);
      continue;
    }

    const listMatch = line.match(/^(\s*)([-*]|\d+[.)])\s+(.+)$/);
    if (listMatch) {
      const ordered = /\d/.test(listMatch[2]);
      const items = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^(\s*)([-*]|\d+[.)])\s+(.+)$/);
        if (!itemMatch || /\d/.test(itemMatch[2]) !== ordered) break;
        items.push(itemMatch[3]);
        index += 1;
      }
      const Tag = ordered ? "ol" : "ul";
      blocks.push(
        <Tag key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, `list-${index}-${itemIndex}`)}</li>
          ))}
        </Tag>
      );
      continue;
    }

    const paragraph = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^```/.test(lines[index]) &&
      !/^(#{1,4})\s+/.test(lines[index]) &&
      !/^>\s?/.test(lines[index]) &&
      !/^(\s*)([-*]|\d+[.)])\s+/.test(lines[index])
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`paragraph-${index}`}>{paragraphNodes(paragraph, `paragraph-${index}`)}</p>);
  }

  return <div className="markdown-content">{blocks}</div>;
}
