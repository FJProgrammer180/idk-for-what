/**
 * A small, deliberately limited Markdown renderer with real sanitisation.
 *
 * Why this exists instead of `dangerouslySetInnerHTML` over `marked` + `DOMPurify`: the text
 * rendered here is *model output* (the agent's answer, a report body, a clarification
 * question). Anything an untrusted generator can write, it can write on purpose. So the
 * pipeline is:
 *
 *   1. Extract fenced code blocks and math spans into opaque placeholders, so nothing inside
 *      them is treated as formatting and nothing inside them is re-escaped twice.
 *   2. HTML-escape everything else. Raw HTML in the input can therefore never become markup.
 *   3. Apply a whitelist of block/inline constructs (headings, lists, tables, blockquotes,
 *      rules, paragraphs, emphasis, inline code, links).
 *   4. Emit only allowlisted tags/attributes, with every URL filtered through `sanitizeUrl`
 *      (http/https/mailto/tel/relative only).
 *   5. Hand math to KaTeX with `trust: false`, so `\href`, `\includegraphics` and friends are
 *      rejected rather than rendered.
 *
 * Anything not in the whitelist stays escaped text. The output is therefore safe to inject
 * with `dangerouslySetInnerHTML`, and `markdown.test.ts` pins that property.
 */

import katex from "katex";
import "katex/dist/katex.min.css";

// Private-use codepoints: a placeholder must be unforgeable by the input text itself,
// otherwise the model could smuggle a token in and have us paste arbitrary HTML for it.
const OPEN = "\uE000";
const CLOSE = "\uE001";
const TOKEN_PATTERN = new RegExp(`${OPEN}(\\d+)${CLOSE}`, "g");
/**
 * Any sentinel the *input* already contains is removed before stashing begins. Without this,
 * text that types a token could point at another block's HTML; private-use codepoints have no
 * legitimate meaning in a physics document, so they are replaced by U+FFFD instead.
 */
const FORGED_SENTINELS = new RegExp(`[${OPEN}${CLOSE}]`, "g");
const LINE_TOKEN = new RegExp(`^${OPEN}(\\d+)${CLOSE}$`);

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(input: string): string {
  return input.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char);
}

const SAFE_URL = /^(?:https?:|mailto:|tel:|\/(?!\/)|#|\.\/|\.\.\/)/i;
const CONTROL_CHARS = /[\u0000-\u001F\u007F]/g;

/**
 * Returns the URL only if it may be used in an `href`. `javascript:`, `data:`,
 * `vbscript:` and protocol-relative `//host` targets are replaced with `#`, because they
 * would either execute in this origin or send the user somewhere else entirely.
 */
export function sanitizeUrl(raw: string): string {
  const candidate = raw.replace(CONTROL_CHARS, "").replace(/\s+/g, "").trim();
  if (!candidate) return "#";
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(candidate);
  if (hasScheme && !SAFE_URL.test(candidate)) return "#";
  if (candidate.startsWith("//")) return "#";
  return escapeHtml(candidate);
}

function renderMath(latex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(latex, {
      displayMode,
      throwOnError: false,
      strict: "ignore",
      // `trust: false` is KaTeX's default; it is stated here so a future dependency bump
      // cannot quietly enable \href / \includegraphics / \url in model-authored math.
      trust: false,
      output: "htmlAndMathml",
      errorColor: "#b45309",
      maxExpand: 50,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return `<span class="math-error" aria-label="math could not be rendered">\\(${escapeHtml(
      latex,
    )}\\)<span class="math-error-note">${escapeHtml(message.slice(0, 160))}</span></span>`;
  }
}

/** Pull out constructs that must be copied verbatim; everything else gets escaped and parsed. */
function extract(source: string): { text: string; parts: string[] } {
  const parts: string[] = [];
  const stash = (html: string): string => {
    const token = `${OPEN}${parts.length}${CLOSE}`;
    parts.push(html);
    return token;
  };

  let text = source;

  // 1. fenced code blocks (``` or ~~~) with an optional language tag
  text = text.replace(
    /^[ \t]*(```|~~~)[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n([\s\S]*?)^[ \t]*\1[ \t]*$/gm,
    (_match, _fence: string, lang: string, body: string) =>
      stash(
        `<pre class="code-block"${
          lang ? ` data-lang="${escapeHtml(lang)}"` : ""
        }><code>${escapeHtml(body.replace(/\n$/, ""))}</code></pre>`,
      ),
  );

  // 2. display math: $$...$$ and \[...\]
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, body: string) => stash(renderMath(body.trim(), true)));
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => stash(renderMath(body.trim(), true)));

  // 3. inline math: $...$ with no space right after the opening delimiter, so currency
  //    ("$5 and $6") is never mistaken for TeX
  text = text.replace(/(?<![$\\])\$(?!\s)((?:[^$\\\n]|\\.)+?)\$(?!\d)/g, (_m, body: string) =>
    stash(renderMath(body, false)),
  );

  // 4. inline code
  text = text.replace(
    /`([^`\n]+)`/g,
    (_m, body: string) => stash(`<code class="inline-code">${escapeHtml(body)}</code>`),
  );

  return { text, parts };
}

/** Escape a line, keeping placeholder tokens intact so they survive into the block pass. */
function escapeKeepingTokens(raw: string): string {
  const pieces = raw.split(TOKEN_PATTERN);
  const tokens = [...raw.matchAll(TOKEN_PATTERN)].map((match) => match[0] ?? "");
  const escaped = pieces.map((piece) => escapeHtml(piece));
  let out = "";
  escaped.forEach((piece, index) => {
    out += piece + (tokens[index] ?? "");
  });
  return out;
}

function inline(raw: string): string {
  let text = escapeKeepingTokens(raw);

  // links: [label](url "title")
  text = text.replace(/\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (_m, label: string, url: string, title?: string) => {
    const href = sanitizeUrl(url);
    const safeLabel = label.trim() === "" ? href : label;
    return `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow"${
      title ? ` title="${escapeHtml(title)}"` : ""
    }>${safeLabel}</a>`;
  });

  // autolinks, in both the <https://…> form (already escaped by the pass above) and bare
  text = text.replace(/&lt;((?:https?:\/\/|mailto:)[^&\s>]+)&gt;/g, (_m, url: string) => {
    const href = sanitizeUrl(url);
    return `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow">${escapeHtml(url)}</a>`;
  });
  text = text.replace(
    /(^|[\s(])((?:https?:\/\/)[^\s<>()]+[^\s<>().,;:!?"'])/g,
    (_m, before: string, url: string) =>
      `${before}<a href="${sanitizeUrl(url)}" target="_blank" rel="noopener noreferrer nofollow">${escapeHtml(
        url,
      )}</a>`,
  );

  // emphasis - bold first, so `**a *b* c**` survives
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  text = text.replace(/(^|[^_\w])_([^_\n]+)_(?![\w_])/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

  return text;
}

function splitRow(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

const ALIGN_CELL = /^:?-{2,}:?$/;

function table(rows: string[]): string {
  const header = splitRow(rows[0] ?? "");
  const separator = splitRow(rows[1] ?? "");
  const aligns = separator.map((cell) =>
    cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "left",
  );
  const body = rows.slice(2).map(splitRow);
  const headHtml = header
    .map((cell, index) => `<th style="text-align:${aligns[index] ?? "left"}">${inline(cell)}</th>`)
    .join("");
  const bodyHtml = body
    .map(
      (cells) =>
        `<tr>${cells
          .map((cell, index) => `<td style="text-align:${aligns[index] ?? "left"}">${inline(cell)}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  return `<table class="md-table"><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

/** Block-level structure. */
function blocks(source: string): string {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  let index = 0;

  const isBlank = (line: string) => /^\s*$/.test(line);
  const listMarker = (line: string) => /^\s*(?:[-*+]|\d+[.)])\s+/.exec(line);
  const wholeToken = (line: string) => LINE_TOKEN.test(line.trim());

  while (index < lines.length) {
    const line = lines[index] ?? "";

    if (isBlank(line)) {
      index += 1;
      continue;
    }

    // a code block or display-math block stands on its own
    if (wholeToken(line)) {
      out.push(line.trim());
      index += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(6, (heading[1] ?? "#").length);
      out.push(`<h${level}>${inline((heading[2] ?? "").trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      out.push("<hr />");
      index += 1;
      continue;
    }

    // pipe table: a header row followed by a |---|---| separator
    if (line.includes("|") && /\|/.test(lines[index + 1] ?? "") && /^[\s|:-]+$/.test((lines[index + 1] ?? "").trim())) {
      const rows: string[] = [];
      while (index < lines.length && (lines[index] ?? "").includes("|")) {
        rows.push(lines[index] ?? "");
        index += 1;
      }
      const valid = splitRow(rows[1] ?? "").length > 0 && splitRow(rows[1] ?? "").every((cell) => ALIGN_CELL.test(cell));
      if (rows.length >= 2 && valid) {
        out.push(table(rows));
        continue;
      }
    }

    if (/^\s*>\s?/.test(line)) {
      const inner: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index] ?? "")) {
        inner.push((lines[index] ?? "").replace(/^\s*>\s?/, ""));
        index += 1;
      }
      out.push(`<blockquote>${blocks(inner.join("\n"))}</blockquote>`);
      continue;
    }

    if (listMarker(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      let current = "";
      while (index < lines.length) {
        const marker = listMarker(lines[index] ?? "");
        const lineAt = lines[index] ?? "";
        if (marker) {
          if (current) items.push(current);
          current = lineAt.slice(marker[0].length);
        } else if (current && /^\s{2,}\S/.test(lineAt)) {
          current += `\n${lineAt.trim()}`;
        } else {
          break;
        }
        index += 1;
      }
      if (current) items.push(current);
      const tag = ordered ? "ol" : "ul";
      const rendered = items.map((item) => {
        // a one-paragraph item must not be wrapped in <p>; a multi-paragraph one keeps its blocks
        const html = /\n\s*\n/.test(item) ? blocks(item) : inline(item.replace(/\n/g, "<br />"));
        return `<li>${html}</li>`;
      });
      out.push(`<${tag} class="md-list">${rendered.join("")}</${tag}>`);
      continue;
    }

    const paragraph: string[] = [];
    while (
      index < lines.length &&
      !isBlank(lines[index] ?? "") &&
      !/^#{1,6}\s+/.test(lines[index] ?? "") &&
      !listMarker(lines[index] ?? "") &&
      !/^\s*>\s?/.test(lines[index] ?? "") &&
      !wholeToken(lines[index] ?? "")
    ) {
      paragraph.push((lines[index] ?? "").trim());
      index += 1;
    }
    if (paragraph.length === 0) {
      index += 1;
      continue;
    }
    out.push(`<p>${inline(paragraph.join("\n")).replace(/\n/g, "<br />")}</p>`);
  }

  return out.join("\n");
}

export interface RenderResult {
  html: string;
  /** How many links were neutered to `#`; the UI can disclose that instead of hiding it. */
  suppressedLinks: number;
}

export function renderMarkdownDetailed(source: string): RenderResult {
  const { text, parts } = extract((source ?? "").replace(FORGED_SENTINELS, "\uFFFD"));
  let html = blocks(text);

  // A token that ended up nested inside something rebuilt is still restored here; any
  // leftover pattern is dropped rather than shown.
  html = html.replace(new RegExp(`${OPEN}(\\d+)${CLOSE}`, "g"), (_m, digits: string) => parts[Number(digits)] ?? "");
  const suppressedLinks = (html.match(/href="#"/g) ?? []).length;

  return { html, suppressedLinks };
}

export function renderMarkdown(source: string): string {
  return renderMarkdownDetailed(source).html;
}

/**
 * Strip Markdown down to the words a reader actually sees - used for word counts and
 * snippets without rendering anything.
 */
export function plainText(source: string): string {
  return (source ?? "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\$\$([\s\S]*?)\$\$/g, " $1 ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/\$([^$\n]+)\$/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_~>]/g, "")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function wordCount(source: string): number {
  const text = plainText(source);
  return text ? text.split(/\s+/).length : 0;
}
