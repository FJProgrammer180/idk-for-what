"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, CircleDot, Plus, Send, Sparkles, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { errorMessage, useAgentEvents, useResource } from "@/lib/resources";
import { formatRelative } from "@/lib/format";
import { Markdown } from "@/components/markdown";
import { Alert, Badge, Button, Card, EmptyState, Field, SectionTitle, Spinner } from "@/components/ui";
import type { AgentEvent, AgentReply, AgentSessionDetail, AgentSessionRow, Page } from "@/lib/types";

/**
 * The agent workspace.
 *
 * What is shown here is the planner's own record: the plan it built, the tools it called with
 * which arguments, what each returned, and the answer derived from those results. The provider
 * banner states plainly when the planner is the bundled deterministic rule planner rather than a
 * language model, and a clarification request from the planner is rendered as the refusal it is
 * instead of an invented answer.
 */
export default function AgentPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-muted">loading agent…</p>}>
      <AgentWorkspace />
    </Suspense>
  );
}

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  reply?: AgentReply;
  pending?: boolean;
}

function AgentWorkspace() {
  const searchParams = useSearchParams();
  const { provider } = useSession();
  const querySession = searchParams.get("session");
  const [sessionId, setSessionId] = useState<string | null>(querySession);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const scroller = useRef<HTMLDivElement | null>(null);

  const sessions = useResource<Page<AgentSessionRow>>("/agent/sessions?page_size=20");
  const detail = useResource<AgentSessionDetail>(sessionId ? `/agent/sessions/${sessionId}` : null, { silent: true });
  const events = useAgentEvents(sessionId, { active: busy });

  const active = detail.data?.status ?? null;
  const feed = useMemo(() => (busy ? events.events : detail.data?.events ?? []), [busy, events.events, detail.data]);

  useEffect(() => {
    if (querySession && querySession !== sessionId) setSessionId(querySession);
  }, [querySession, sessionId]);

  // A freshly opened session gets its recorded transcript replayed as turns, so reloading the
  // page shows the same conversation (from stored events) rather than an empty box.
  const replayed = useRef<string | null>(null);
  useEffect(() => {
    const data = detail.data;
    if (!data || replayed.current === data.session_id) return;
    replayed.current = data.session_id;
    const replay: ChatTurn[] = [];
    for (const event of data.events ?? []) {
      if (event.event_type === "user_message") {
        replay.push({ id: `u-${event.sequence}`, role: "user", text: String(event.payload?.text ?? event.payload?.content ?? "") });
      }
      if (event.event_type === "assistant_message" || event.event_type === "agent_answer") {
        replay.push({
          id: `a-${event.sequence}`,
          role: "assistant",
          text: String(event.payload?.answer_markdown ?? event.payload?.text ?? event.payload?.content ?? ""),
        });
      }
    }
    setTurns((current) => (current.length > 0 ? current : replay));
  }, [detail.data]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [turns, feed]);

  const pick = useCallback((next: string) => {
    setSessionId(next);
    setTurns([]);
    setFailure(null);
    replayed.current = null;
  }, []);

  async function newSession() {
    setCreating(true);
    setFailure(null);
    try {
      const created = await api.post<AgentSessionRow>("/agent/sessions", { title: `Session ${new Date().toISOString().slice(0, 16).replace("T", " ")}` });
      pick(created.session_id);
      await sessions.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setCreating(false);
    }
  }

  async function send() {
    const content = draft.trim();
    if (!content || !sessionId) return;
    setBusy(true);
    setFailure(null);
    const turnId = `local-${Date.now()}`;
    setTurns((current) => [...current, { id: turnId, role: "user", text: content }, { id: `${turnId}-a`, role: "assistant", text: "", pending: true }]);
    setDraft("");
    try {
      const reply = await api.post<AgentReply>(`/agent/sessions/${sessionId}/messages`, { content, wait: true });
      setTurns((current) =>
        current.map((turn) =>
          turn.id === `${turnId}-a`
            ? { ...turn, pending: false, text: reply.answer_markdown, reply }
            : turn,
        ),
      );
      await Promise.all([detail.reload(), sessions.reload()]);
    } catch (cause) {
      setTurns((current) => current.filter((turn) => turn.id !== `${turnId}-a`));
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const toolCount = (detail.data?.usage?.tool_calls as number | undefined) ?? undefined;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Research agent</h1>
          <p className="text-[13px] text-muted">
            Plans over the registered tool set only: schema-validated calls into the numerical, simulation, dataset and
            reporting services. No code execution, no fabricated citations.
          </p>
        </div>
        <Button size="sm" intent="primary" onClick={newSession} disabled={creating}>
          {creating ? <Spinner /> : <Plus size={15} />} New session
        </Button>
      </header>

      {provider?.is_mock ? (
        <Alert tone="info">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone="warning" dot>
              {provider.provider}
            </Badge>
            <span>
              This is the deterministic rule planner shipped with the repo, not a language model. {provider.note}
            </span>
          </p>
          {provider.research_integration_configured ? null : (
            <p className="text-[12.5px] text-muted">
              No retrieval integration is configured, so literature questions are answered as “not performed” with the
              bundled reference list, rather than with invented citations.
            </p>
          )}
        </Alert>
      ) : null}

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
        <Card>
          <SectionTitle title="Sessions" hint={`${sessions.data?.total ?? 0} total`} />
          {sessions.loading ? (
            <p className="flex items-center gap-2 text-[13px] text-muted">
              <Spinner /> loading…
            </p>
          ) : (sessions.data?.items ?? []).length === 0 ? (
            <EmptyState title="No sessions" hint="Create one to start a planning conversation." />
          ) : (
            <ul className="space-y-1">
              {(sessions.data?.items ?? []).map((row) => (
                <li key={row.session_id}>
                  <button
                    type="button"
                    onClick={() => pick(row.session_id)}
                    className={`w-full rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors ${
                      row.session_id === sessionId ? "bg-accent-soft font-medium" : "hover:bg-surface-2"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate">{row.title}</span>
                      <Badge tone={row.status === "completed" ? "success" : row.status === "failed" ? "danger" : "neutral"}>
                        {row.status}
                      </Badge>
                    </span>
                    <span className="mt-0.5 block text-[11px] text-muted">
                      {row.event_count ?? 0} event(s) · {formatRelative(row.updated_at ?? row.created_at)}
                      {row.plan?.length ? ` · ${row.plan.length}-step plan` : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-3">
          <Card className="flex h-[58vh] min-h-72 flex-col">
            <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
              {!sessionId ? (
                <EmptyState title="Choose or create a session" hint="Each session keeps its own plan, tool calls and answer." action={null} />
              ) : turns.length === 0 ? (
                <EmptyState
                  title="Ask something computational"
                  hint='Try: "solve x^2-3 between 1 and 2 with tolerance 1e-12", "simulate a damped pendulum at 30 degrees", or "fit g from my falling-body dataset".'
                  action={null}
                />
              ) : (
                turns.map((turn) => (
                  <div key={turn.id} className={turn.role === "user" ? "ml-auto max-w-[85%]" : "mr-auto max-w-[95%]"}>
                    {turn.role === "user" ? (
                      <div className="rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[13.5px] text-white dark:text-[#0b0e16]">{turn.text}</div>
                    ) : (
                      <div className="panel px-3.5 py-2.5">
                        {turn.pending ? (
                          <p className="flex items-center gap-2 text-[13px] text-muted">
                            <Spinner /> planning and calling tools…
                          </p>
                        ) : turn.text ? (
                          <Markdown source={turn.text} />
                        ) : (
                          <p className="text-[13px] text-muted">The planner returned no answer text.</p>
                        )}
                        {turn.reply?.plan?.length ? (
                          <ol className="mt-2 space-y-1 border-t border-border pt-2 text-[12px]">
                            {turn.reply.plan.map((step) => (
                              <li key={`${step.index}-${step.tool}`} className="flex items-start gap-2">
                                <CircleDot size={11} className={step.status === "completed" ? "mt-1 text-emerald-500" : "mt-1 text-amber-500"} />
                                <span className="mono text-[11.5px] text-muted">{step.tool}</span>
                                <span className="min-w-0 flex-1">{step.description}</span>
                                {step.detail ? <span className="mono text-[11px] text-muted">{step.detail}</span> : null}
                              </li>
                            ))}
                          </ol>
                        ) : null}
                        {turn.reply?.error ? (
                          <Alert tone="warning" className="mt-2">
                            {typeof turn.reply.error === "string" ? turn.reply.error : turn.reply.error.message}
                          </Alert>
                        ) : null}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>

            <div className="mt-3 border-t border-border pt-3">
              <Field label="Message" htmlFor="composer" hint={sessionId ? undefined : "open a session first"}>
                <textarea
                  id="composer"
                  className="field min-h-20 resize-y"
                  value={draft}
                  disabled={!sessionId || busy}
                  placeholder="Describe the problem in plain language. The planner only answers with tools that exist."
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) void send();
                  }}
                />
              </Field>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11.5px] text-muted">⌘/Ctrl + Enter sends · {draft.trim().length}/6000 characters</span>
                <Button intent="primary" size="sm" onClick={send} disabled={!sessionId || busy || draft.trim().length < 3}>
                  {busy ? <Spinner /> : <Send size={14} />} {busy ? "working…" : "Send"}
                </Button>
              </div>
            </div>
          </Card>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <SectionTitle
                title="Execution trace"
                hint={busy ? "live, polled from the session event log" : "recorded events for this session"}
                icon={<Wrench size={15} />}
                actions={toolCount !== undefined ? <Badge tone="neutral">{toolCount} tool call(s)</Badge> : null}
              />
              {feed.length === 0 ? (
                <p className="text-[12.5px] text-muted">No events recorded yet.</p>
              ) : (
                <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1 text-[12px]">
                  {feed.slice(-40).map((event) => (
                    <EventRow key={`${event.sequence}-${event.event_type}`} event={event} />
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <SectionTitle title="Plan of record" hint="the last plan the planner stored" icon={<Sparkles size={15} />} />
              {(detail.data?.plan ?? []).length === 0 ? (
                <p className="text-[12.5px] text-muted">No plan yet.</p>
              ) : (
                <ol className="space-y-1.5 text-[12.5px]">
                  {(detail.data?.plan ?? []).map((step) => (
                    <li key={`${step.index}-${step.tool}`} className="flex items-start gap-2">
                      <span className="mono text-[11px] text-muted">{step.index}</span>
                      <span className="min-w-0 flex-1">
                        <span className="mono text-[11.5px]">{step.tool}</span> — {step.description}
                      </span>
                      <Badge tone={step.status === "completed" ? "success" : step.status === "skipped" ? "neutral" : "warning"}>{step.status}</Badge>
                    </li>
                  ))}
                </ol>
              )}
              {detail.data?.usage && Object.keys(detail.data.usage).length > 0 ? (
                <p className="mt-3 border-t border-border pt-2 text-[11.5px] text-muted">
                  usage:{" "}
                  <span className="mono">
                    {Object.entries(detail.data.usage)
                      .map(([key, value]) => `${key}=${value}`)
                      .join(" ")}
                  </span>
                </p>
              ) : null}
              {sessionId ? (
                <p className="mt-2 text-[11.5px] text-muted">
                  raw log:{" "}
                  <Link href={`/api/v1/agent/sessions/${sessionId}/events?after=0`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                    /agent/sessions/{sessionId.slice(0, 12)}…/events
                  </Link>
                </p>
              ) : null}
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function EventRow({ event }: { event: AgentEvent }) {
  const summary = summarise(event);
  return (
    <li className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5">
      <span className="mono text-[11px] text-muted">{event.sequence}</span>
      <span className="min-w-0 flex-1">
        <span className="mono text-[11.5px]">{event.event_type}</span>
        {summary ? <span className="block truncate text-muted">{summary}</span> : null}
      </span>
      {event.status && event.status !== "info" ? (
        <Badge tone={event.status === "completed" ? "success" : event.status === "failed" ? "danger" : "warning"}>{event.status}</Badge>
      ) : null}
    </li>
  );
}

function summarise(event: AgentEvent): string {
  const payload = event.payload ?? {};
  if (typeof payload.text === "string") return payload.text;
  if (typeof payload.message === "string") return payload.message;
  if (typeof payload.summary === "string") return payload.summary;
  if (typeof payload.tool === "string") return `${payload.tool}${payload.ok === false ? " failed" : ""}`;
  const keys = Object.keys(payload);
  return keys.length > 0 ? keys.slice(0, 4).join(", ") : "";
}
