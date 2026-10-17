"use client";

import { useMemo } from "react";
import { renderMarkdownDetailed } from "@/lib/markdown";
import { cn } from "@/lib/cn";
import { Alert } from "./ui";

/**
 * Renders model- or server-authored markdown as HTML.
 *
 * The sanitisation lives in `src/lib/markdown.ts` (escape-first, tag/attribute whitelist,
 * URL filtering, KaTeX with `trust: false`), which is what makes `dangerouslySetInnerHTML`
 * acceptable here. If the renderer had to suppress anything it says so instead of quietly
 * dropping content.
 */
export function Markdown({
  source,
  className,
  suppressWarnings = false,
}: {
  source: string;
  className?: string;
  suppressWarnings?: boolean;
}) {
  const { html, suppressedLinks } = useMemo(() => renderMarkdownDetailed(source), [source]);

  return (
    <>
      {/* eslint-disable-next-line react/no-danger -- content is sanitised by src/lib/markdown.ts */}
      <div className={cn("markdown", className)} dangerouslySetInnerHTML={{ __html: html }} />
      {suppressedLinks > 0 && !suppressWarnings ? (
        <Alert tone="warning" className="mt-2">
          {suppressedLinks} link{suppressedLinks === 1 ? "" : "s"} in this text pointed at a
          scheme this client refuses to make clickable (only http, https, mailto, tel and
          relative paths are allowed), so {suppressedLinks === 1 ? "it is" : "they are"} shown as
          inert text.
        </Alert>
      ) : null}
    </>
  );
}
