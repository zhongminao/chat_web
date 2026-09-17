import React, { useState } from "react";

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

function unescapeText(text) {
  return text.replace(/\\(.)/g, (match, char) => (ESCAPABLE.has(char) ? char : match));
}

function isWhitespace(char) {
  return !char || /\s/.test(char);
}

function isEscaped(text, position) {
  let count = 0;
  for (let index = position - 1; index >= 0 && text[index] === "\\"; index -= 1) {
    count += 1;
  }
  return count % 2 === 1;
}

function indexOfUnescaped(text, needle, fromIndex) {
  let found = text.indexOf(needle, fromIndex);
  while (found >= 0 && isEscaped(text, found)) {
    found = text.indexOf(needle, found + needle.length);
  }
  return found;
}

const ESCAPABLE = new Set(["\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", ">", "~", "|"]);

function isAsciiAlnum(char) {
  return !!char && /[A-Za-z0-9]/.test(char);
}

// 「这个星号像不像代码/数学，而不像强调符」——这一条是这轮修的核心。
//
// 中文和英文在这个问题上的形状**不同**，所以只靠"两侧是不是空白/标点"永远判不准：
//   `长*宽` 与 `这是*强调*文字` 的局部形状完全一样（汉字-星号-汉字），
//   任何只看相邻字符类别的规则都无法区分它们。
// 能区分的是 **ASCII 与汉字不是一回事**：
//   两侧都是 ASCII 字母/数字 → 一定是乘号、通配符或变量名（`3*4`、`a*b`、`m*c^2`），
//   永远不当定界符。这一条同时救掉了 `2*3 and 4*5`、`P = 2*pi*r` 和路径通配。
function isDelimiterRun(text, position, runLength) {
  const before = text[position - 1];
  const after = text[position + runLength];
  return !(isAsciiAlnum(before) && isAsciiAlnum(after));
}

// 开定界符：后面必须跟非空白（`* 3` 这种不开放）。
function canOpenEmphasis(text, position, marker) {
  return !isWhitespace(text[position + marker.length]);
}

// 闭定界符：前面必须是非空白。
//
// 这里**没有**"后面必须是空白/标点"那条限制 —— 那条是我上一轮加的，它确实挡住了
// `4*5`，但代价是中文行内强调全废：`这是*强调*文字` 的闭定界符后面就是汉字（不是
// 标点），于是整句退化成字面量。现在 `4*5` 那一类改由 isDelimiterRun 挡（两侧都是
// ASCII 数字），所以这条限制可以撤掉，两边都保住。
function canCloseEmphasis(text, position) {
  return !isWhitespace(text[position - 1]);
}

// 这对定界符允不允许配对。
//
// 这里换过三版，把前两版的坑记下来，免得又绕回去：
//
//  v1（首版）：闭定界符"后面必须是空白/标点"。挡住了 `4*5`，但中文行内强调全废 ——
//     `这是*强调*文字` 的闭定界符后面就是汉字，整句退化成字面量。
//  v2：改成"开合双方必须**同为** ASCII 邻接"。救回了 `长*宽 … 2*(`，但把
//     `**P99 延迟**` 判死了 —— 那里开定界符贴着 `P`（ASCII）、闭定界符贴着 `迟`
//     （汉字），两侧不同类。而"中文句子里夹数字/英文的粗体"几乎全是这个形状，
//     等于误伤了最高频的一种写法。纯 ASCII 和纯中文都好，**混排就坏**。
//  v3（当前）：只保留**单向**约束 —— 闭定界符左边粘着 ASCII 字母/数字时
//     （`e*`、`2*` 这种"星号贴在词尾"的形状），才要求开定界符右边也粘着 ASCII
//     （`*n`、`*P`）。也就是"这对定界符是围着一段以 ASCII 开头的内容开的"。
//
// 为什么单向就够：要挡的是 `2*(` 这种**闭定界符贴在 ASCII 词尾**的形状拿前面的
// 中文星号当开定界符。反过来（`**P99 延迟**` —— 开头 ASCII、结尾汉字）是正常强调，
// 没有任何数学含义需要防，所以不设约束。
function findRunClose(text, openerPos, runLength) {
  const marker = "*".repeat(runLength);
  const openerRightIsAscii = isAsciiAlnum(text[openerPos + runLength]);
  let cursor = indexOfUnescaped(text, marker, openerPos + runLength);
  while (cursor >= 0) {
    const length = starRunLength(text, cursor);
    if (
      length === runLength &&
      isDelimiterRun(text, cursor, length) &&
      canCloseEmphasis(text, cursor) &&
      (!isAsciiAlnum(text[cursor - 1]) || openerRightIsAscii)
    ) {
      return cursor;
    }
    cursor = indexOfUnescaped(text, marker, cursor + Math.max(length, 1));
  }
  return -1;
}

// 连续的星号是**一个整体**（CommonMark 的 delimiter run）。不这么看会出这种事：
// `src/*.py ... **粗体**` 里，前者的单个 `*` 会找到后者闭合用的那对星号中的第一个
// 去配对 —— 于是中间整段变成斜体、`*` 从界面上消失。所以配对只在**同长度**的段
// 之间发生：单星号找单星号，双星号找双星号。
function starRunLength(text, position) {
  let length = 0;
  while (text[position + length] === "*") length += 1;
  return length;
}

function renderInline(text, keyPrefix = "i") {
  const nodes = [];
  let index = 0;

  function pushText(until) {
    if (until > index) {
      nodes.push(unescapeText(text.slice(index, until)));
      index = until;
    }
  }

  while (index < text.length) {
    if (text[index] === "\\" && ESCAPABLE.has(text[index + 1])) {
      nodes.push(text[index + 1]);
      index += 2;
      continue;
    }

    const codeStart = indexOfUnescaped(text, "`", index);
    const starStart = indexOfUnescaped(text, "*", index);
    const linkStart = indexOfUnescaped(text, "[", index);
    const mathStart = indexOfUnescaped(text, "$", index);
    const candidates = [codeStart, starStart, linkStart, mathStart]
      .filter((value) => value >= index)
      .sort((a, b) => a - b);

    if (candidates.length === 0) {
      nodes.push(unescapeText(text.slice(index)));
      break;
    }

    const start = candidates[0];
    pushText(start);

    if (text.startsWith("`", index)) {
      const end = indexOfUnescaped(text, "`", index + 1);
      if (end < 0) {
        nodes.push(unescapeText(text.slice(index)));
        break;
      }
      nodes.push(<code key={`${keyPrefix}-code-${index}`}>{text.slice(index + 1, end)}</code>);
      index = end + 1;
      continue;
    }

    // 三颗及以上的连续星号：整个段原样留下（`***x***` 显示成 `***x***`）。
    // 明确列为已知限制，而不是猜一个嵌套含义 —— 猜错的方向是**吞字符**。
    if (text.startsWith("*") && starRunLength(text, index) > 2) {
      const runLength = starRunLength(text, index);
      nodes.push("*".repeat(runLength));
      index += runLength;
      continue;
    }

    // 数学区间 `$...$` / `$$...$$`：整段**不透明**，里面的 `*` 一律不当强调符。
    //
    // 这是这一轮补的第二道口子。前面那套定界符规则能救 `m*c^2`（两侧都是 ASCII），
    // 但救不了 `$x^{*}$ 与 $y^{*}$` —— 那里两颗星号两侧都是 `{` `}`，既非 ASCII
    // 字母数字、又彼此同类，规则上是"合法的一对"，照样把中间吞掉。想让它安全，
    // 只能先认出"这里是公式"，而不是继续在星号上做文章。
    //
    // 判定用启发式，因为 `$` 也是货币符号：
    //   开 `$` 后面必须紧跟非空白、闭 `$` 前面必须不是空白（`价格 $5 到 $10` 因此
    //   不会成对）。找不到合法闭合就整个当字面量，一个字符都不动。
    // 输出**保留 `$` 定界符**：不排版数学，只保证原样，方便你整段复制去别处用。
    if (text.startsWith("$", index)) {
      const marker = text.startsWith("$$", index) ? "$$" : "$";
      const start = index + marker.length;
      let cursor = indexOfUnescaped(text, marker, start);
      let end = -1;
      while (cursor >= 0) {
        const content = text.slice(start, cursor);
        if (content && !isWhitespace(content[0]) && !isWhitespace(content[content.length - 1])) {
          end = cursor;
          break;
        }
        cursor = indexOfUnescaped(text, marker, cursor + marker.length);
      }
      if (end < 0) {
        nodes.push(marker);
        index += marker.length;
        continue;
      }
      nodes.push(
        <span className="markdown-math-inline" key={`${keyPrefix}-math-${index}`}>
          {text.slice(index, end + marker.length)}
        </span>
      );
      index = end + marker.length;
      continue;
    }

    if (text.startsWith("**", index)) {
      if (!isDelimiterRun(text, index, 2) || !canOpenEmphasis(text, index, "**")) {
        nodes.push("**");
        index += 2;
        continue;
      }
      const end = findRunClose(text, index, 2);
      if (end < 0) {
        // 找不到合法闭合就**原样留下这两个星号**，然后继续扫后面的内容
        // （不是把剩下的整段一起吐出来 —— 那样后面真正的粗体/斜体就不渲染了）。
        nodes.push("**");
        index += 2;
        continue;
      }
      nodes.push(<strong key={`${keyPrefix}-strong-${index}`}>{renderInline(text.slice(index + 2, end), `${keyPrefix}-strong-${index}`)}</strong>);
      index = end + 2;
      continue;
    }

    if (text.startsWith("[", index)) {
      const labelEnd = indexOfUnescaped(text, "]", index + 1);
      const hrefStart = labelEnd >= 0 && text[labelEnd + 1] === "(" ? labelEnd + 2 : -1;
      const hrefEnd = hrefStart >= 0 ? indexOfUnescaped(text, ")", hrefStart) : -1;
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
      if (!isDelimiterRun(text, index, 1) || !canOpenEmphasis(text, index, "*")) {
        nodes.push("*");
        index += 1;
        continue;
      }
      const end = findRunClose(text, index, 1);
      if (end < 0) {
        nodes.push("*");
        index += 1;
        continue;
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

function parseTableRow(line) {
  const trimmed = line.trim();
  const withoutLeft = trimmed.startsWith("|") ? trimmed.slice(1) : trimmed;
  const withoutEdges = withoutLeft.endsWith("|") ? withoutLeft.slice(0, -1) : withoutLeft;
  return withoutEdges.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line) {
  const cells = parseTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isTableStart(lines, index) {
  if (index + 1 >= lines.length) return false;
  const header = parseTableRow(lines[index]);
  const separator = parseTableRow(lines[index + 1]);
  return lines[index].includes("|") && isTableSeparator(lines[index + 1]) && header.length === separator.length;
}

function CodeBlock({ code, language }) {
  const [copied, setCopied] = useState(false);
  const shownLanguage = language || "text";

  async function copyCode() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="markdown-code-frame">
      <div className="markdown-code-toolbar">
        <span className="markdown-code-language">{shownLanguage}</span>
        <button className="markdown-code-copy" type="button" onClick={copyCode}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre className="markdown-code-block"><code>{code}</code></pre>
    </div>
  );
}

export default function MarkdownContent({ content }) {
  const source = String(content || "");
  if (!source.trim()) return null;

  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([^`]*)$/);
    if (fence) {
      const language = fence[1].trim().split(/\s+/)[0] || "";
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(<CodeBlock code={code.join("\n")} language={language} key={`code-${index}`} />);
      continue;
    }

    // 行间公式块：整行就是 `$$...$$`（单行写完，或 `$$` 单独一行再往下收）。
    // 和行内那个同一个理由 —— 里面是公式，不是 markdown，一个字符都不解析。
    // 只在**整行**成立时才走这里，所以 `$$x$$ 后面还有话` 会落到普通段落，
    // 由行内规则去保护，不会把尾巴吃掉。
    const displayMath = line.trim();
    const opensDisplayMath =
      displayMath === "$$" ||
      (displayMath.startsWith("$$") && displayMath.endsWith("$$") && displayMath.length > 4);
    if (opensDisplayMath) {
      const collected = [line];
      index += 1;
      // 单行 `$$...$$` 已经闭合；`$$` 单独一行则往下收到闭合那一行为止。
      // 收不到就一路收到末尾 —— 宁可多包一块，也不丢字符。
      if (displayMath === "$$") {
        while (index < lines.length) {
          collected.push(lines[index]);
          const closed = lines[index].trim().endsWith("$$");
          index += 1;
          if (closed) break;
        }
      }
      blocks.push(
        <div className="markdown-math-block" key={`math-${index}`}>
          {collected.join("\n")}
        </div>
      );
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = parseTableRow(lines[index]);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = parseTableRow(lines[index]);
        rows.push(headers.map((_, cellIndex) => row[cellIndex] || ""));
        index += 1;
      }
      blocks.push(
        <div className="markdown-table-wrap" key={`table-${index}`}>
          <table>
            <thead>
              <tr>{headers.map((header, cellIndex) => <th key={cellIndex}>{renderInline(header, `table-${index}-h-${cellIndex}`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => <td key={cellIndex}>{renderInline(cell, `table-${index}-${rowIndex}-${cellIndex}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
      !isTableStart(lines, index) &&
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
