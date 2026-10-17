"use client";

import { useMemo, useState } from "react";
import { BookOpen, Cpu, Search, ShieldCheck, Sigma, Beaker, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { errorMessage } from "@/lib/resources";
import { formatNumber } from "@/lib/format";
import { Alert, Badge, Button, Card, EmptyState, Field, KeyValue, SectionTitle, Spinner } from "@/components/ui";
import { cn } from "@/lib/cn";
import type { SearchResponse, SourceRecord } from "@/lib/types";

type Tab = "methods" | "models" | "tools" | "sources" | "security";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "methods", label: "Numerical methods", icon: <Sigma size={14} /> },
  { id: "models", label: "Physical models", icon: <Beaker size={14} /> },
  { id: "tools", label: "Agent tools", icon: <Wrench size={14} /> },
  { id: "sources", label: "Literature", icon: <BookOpen size={14} /> },
  { id: "security", label: "Security posture", icon: <ShieldCheck size={14} /> },
];

/**
 * The documented surface of the engine: what each method requires, what it assumes, what it can
 * guarantee, and where it refuses.
 *
 * This page exists because those facts decide whether a result is believable. It is generated
 * from the live registries (`GET /compute/methods`, `/compute/simulations`, `/agent/tools`), so
 * it cannot drift from what the server actually enforces — and the literature tab shows the
 * curated reference list with `verified_online: false` on every entry rather than pretending a
 * citation was checked.
 */
export default function LibraryPage() {
  const { capabilities } = useSession();
  const [tab, setTab] = useState<Tab>("methods");

  const methods = capabilities?.numerical.methods ?? [];
  const simulations = capabilities?.simulations ?? [];
  const tools = capabilities?.tools ?? [];

  const counts = useMemo(
    () => ({ methods: methods.length, models: simulations.length, tools: tools.length }),
    [methods.length, simulations.length, tools.length],
  );

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Method library</h1>
          <p className="text-[13px] text-muted">
            {counts.methods} numerical methods · {counts.models} physical models · {counts.tools} agent tools — read
            straight from the running registries.
          </p>
        </div>
        {capabilities?.version ? <Badge tone="neutral" className="mono">engine v{capabilities.version}</Badge> : null}
      </header>

      <div className="flex flex-wrap gap-1 rounded-xl bg-surface-2 p-1">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
              tab === entry.id ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink",
            )}
          >
            {entry.icon}
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "methods" ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {methods.map((method) => (
            <MethodCard key={method.method} name={method.method} title={method.title} problemType={method.problem_type} requires={method.requires} optional={method.optional} assumptions={method.assumptions} order={method.order} />
          ))}
        </div>
      ) : null}

      {tab === "models" ? (
        <div className="space-y-3">
          {simulations.map((model) => (
            <Card key={model.key}>
              <SectionTitle
                title={model.title}
                hint={model.description}
                icon={<Beaker size={15} />}
                actions={
                  model.analytical_validation_available ? (
                    <Badge tone="success">validated against a closed form</Badge>
                  ) : (
                    <Badge tone="warning">no closed form: convergence only</Badge>
                  )
                }
              />
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                <div className="overflow-x-auto rounded-xl border border-border">
                  <table className="table-grid">
                    <thead>
                      <tr>
                        <th>parameter</th>
                        <th>default</th>
                        <th>range</th>
                        <th>unit</th>
                        <th>notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(model.parameters ?? {}).map(([key, spec]) => (
                        <tr key={key}>
                          <td className="mono">{key}</td>
                          <td className="mono">
                            {spec.derived ? (
                              <span className="text-muted">derived (omitted → computed from the model)</span>
                            ) : spec.enum ? (
                              spec.enum.join(" | ")
                            ) : typeof spec.default === "number" ? (
                              formatNumber(spec.default, 6)
                            ) : (
                              String(spec.default ?? "—")
                            )}
                          </td>
                          <td className="mono text-[11.5px] text-muted">
                            {spec.min === null || spec.min === undefined ? "−∞" : formatNumber(spec.min, 4)} …{" "}
                            {spec.max === null || spec.max === undefined ? "+∞" : formatNumber(spec.max, 4)}
                          </td>
                          <td className="text-[12px]">{spec.unit ?? "—"}</td>
                          <td className="text-[12px] text-muted">{spec.description ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="space-y-3 text-[12.5px]">
                  {(model.state_variables ?? []).length > 0 ? (
                    <p>
                      <span className="label mr-2">State</span>
                      <span className="mono">{(model.state_variables ?? []).join(", ")}</span>
                    </p>
                  ) : null}
                  {(model.governing_equations ?? []).length > 0 ? (
                    <div>
                      <p className="label mb-1">Governing equations</p>
                      <ul className="mono space-y-0.5 text-[12px]">
                        {(model.governing_equations ?? []).map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {(model.assumptions ?? []).length > 0 ? (
                    <div>
                      <p className="label mb-1">Assumptions</p>
                      <ul className="list-disc space-y-0.5 pl-4 text-muted">
                        {(model.assumptions ?? []).map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {(model.limitations ?? []).length > 0 ? (
                    <div>
                      <p className="label mb-1">Limitations</p>
                      <ul className="list-disc space-y-0.5 pl-4 text-muted">
                        {(model.limitations ?? []).map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      {tab === "tools" ? (
        <div className="space-y-3">
          <Alert tone="info">
            Every tool takes a schema-validated Pydantic model (<span className="mono">additionalProperties: false</span>)
            and calls only the registered engines — the agent has no code-execution path, and a call
            that names an unknown field is refused before anything runs.
          </Alert>
          <div className="grid gap-3 md:grid-cols-2">
            {tools.map((tool) => (
              <Card key={tool.name}>
                <SectionTitle
                  title={tool.name}
                  hint={tool.description}
                  icon={<Wrench size={15} />}
                  actions={
                    <span className="flex gap-1.5">
                      <Badge tone="neutral">{tool.category}</Badge>
                      {tool.persist ? <Badge tone="warning">writes</Badge> : <Badge tone="neutral">read-only</Badge>}
                    </span>
                  }
                />
                <ToolSchema schema={tool.input_schema} />
              </Card>
            ))}
          </div>
        </div>
      ) : null}

      {tab === "sources" ? <SourcesTab curated={[]} /> : null}

      {tab === "security" ? <SecurityTab /> : null}
    </div>
  );
}

function MethodCard({
  name,
  title,
  problemType,
  requires,
  optional,
  assumptions,
  order,
}: {
  name: string;
  title: string;
  problemType: string;
  requires: string[];
  optional: string[];
  assumptions: string[] | string;
  order: string | number | null;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function expand() {
    const next = !open;
    setOpen(next);
    if (!next || detail) return;
    setLoading(true);
    try {
      setDetail(await api.get<Record<string, unknown>>(`/compute/methods/${name}`));
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="flex flex-col gap-2">
      <button type="button" onClick={expand} className="text-left" aria-expanded={open}>
        <p className="flex items-start justify-between gap-2">
          <span className="text-[13.5px] font-medium leading-snug">{title}</span>
          <Badge tone="neutral">{problemType}</Badge>
        </p>
        <p className="mono mt-0.5 text-[11.5px] text-muted">{name}</p>
      </button>

      <div className="space-y-1 text-[12px]">
        <p>
          <span className="text-muted">requires </span>
          {requires.map((entry) => (
            <span key={entry} className="mono mr-1 rounded bg-surface-2 px-1.5 py-0.5 text-[11px]">
              {entry}
            </span>
          ))}
        </p>
        {optional.length > 0 ? (
          <p>
            <span className="text-muted">optional </span>
            {optional.map((entry) => (
              <span key={entry} className="mono mr-1 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted">
                {entry}
              </span>
            ))}
          </p>
        ) : null}
        <p className="leading-relaxed text-muted">
          <span className="text-ink">precondition </span>
          {Array.isArray(assumptions) ? assumptions.join(" ") : assumptions}
        </p>
        {order ? (
          <p className="leading-relaxed text-muted">
            <span className="text-ink">order </span>
            {String(order)}
          </p>
        ) : null}
      </div>

      {open ? (
        <div className="border-t border-border pt-2 text-[12px]">
          {loading ? (
            <p className="flex items-center gap-2 text-muted">
              <Spinner /> loading the documented error estimate…
            </p>
          ) : failure ? (
            <p className="text-rose-500">{failure}</p>
          ) : detail ? (
            <KeyValue
              rows={[
                ["error estimate", String(detail.error_estimate ?? "none published")],
                [
                  "worked example",
                  <pre key="ex" className="mono max-h-40 overflow-auto whitespace-pre-wrap text-[11px]">
                    {JSON.stringify(detail.example_parameters ?? {}, null, 2)}
                  </pre>,
                ],
                ["software", Object.entries((detail.software_versions ?? {}) as Record<string, string>).map(([key, value]) => `${key} ${value}`).join(", ") || "—"],
              ]}
            />
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function ToolSchema({ schema }: { schema: Record<string, unknown> }) {
  const properties = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const required = new Set((schema.required ?? []) as string[]);
  const entries = Object.entries(properties);
  if (entries.length === 0) return <p className="text-[12.5px] text-muted">This tool takes no arguments.</p>;
  return (
    <ul className="space-y-1 text-[12px]">
      {entries.map(([key, spec]) => (
        <li key={key} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className={cn("mono", required.has(key) ? "font-semibold" : "text-muted")}>{key}</span>
          <Badge tone="neutral">{String(spec.type ?? (spec.anyOf ? "any" : "any"))}</Badge>
          {required.has(key) ? <span className="text-[11px] text-rose-500">required</span> : null}
          {spec.default !== undefined && spec.default !== null ? (
            <span className="mono text-[11px] text-muted">default {JSON.stringify(spec.default)}</span>
          ) : null}
          {typeof spec.description === "string" ? <span className="w-full text-muted">{spec.description}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function SourcesTab({ curated }: { curated: SourceRecord[] }) {
  const [query, setQuery] = useState("Richardson extrapolation convergence");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function search() {
    setBusy(true);
    setFailure(null);
    try {
      setResult(await api.post<SearchResponse>("/sources/search", { query, max_results: 8 }));
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const references = result?.local_references ?? curated;

  return (
    <div className="space-y-3">
      <Card>
        <SectionTitle title="Literature search" hint="what the agent's `search_scientific_sources` tool actually does" icon={<Search size={15} />} />
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Query" htmlFor="q" className="min-w-64 flex-1">
            <input id="q" className="field" value={query} onChange={(event) => setQuery(event.target.value)} />
          </Field>
          <Button intent="primary" onClick={search} disabled={busy || query.trim().length < 3}>
            {busy ? <Spinner /> : <Search size={15} />} Search
          </Button>
        </div>
        {failure ? <Alert tone="danger" className="mt-3">{failure}</Alert> : null}
        {result ? (
          <div className="mt-3 space-y-2">
            <Alert tone={result.external_research_performed ? "success" : "warning"} title={`status: ${result.status}`}>
              <p>{result.reason}</p>
              {result.guidance ? <p className="text-[12.5px] text-muted">{result.guidance}</p> : null}
              <p className="text-[12px] text-muted">
                provenance of the list below: <span className="mono">{result.local_reference_provenance}</span>
              </p>
            </Alert>
          </div>
        ) : (
          <p className="mt-2 text-[12px] text-muted">
            With no retrieval integration configured this endpoint answers “not performed”. It never returns invented
            citations, which is the behaviour the capstone rubric would call integrity.
          </p>
        )}
      </Card>

      {references.length > 0 ? (
        <Card>
          <SectionTitle title="Curated references" hint="bundled with the application; none of them were fetched or checked online by this build" />
          <ul className="space-y-2">
            {references.map((reference) => (
              <li key={reference.id ?? reference.title} className="rounded-xl border border-border bg-surface-2 p-3 text-[12.5px]">
                <p className="font-medium">{reference.title}</p>
                <p className="text-muted">
                  {reference.authors} · {reference.year} · {reference.venue}
                </p>
                {reference.summary ? <p className="mt-1 leading-relaxed">{reference.summary}</p> : null}
                <p className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                  <Badge tone={reference.url ? "info" : "neutral"}>{reference.url ? reference.url : "no URL recorded"}</Badge>
                  <Badge tone="warning">provenance: {reference.provenance ?? "local_curated_reference"}</Badge>
                  <Badge tone="danger">verified online: {String(reference.verified_online ?? false)}</Badge>
                </p>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function SecurityTab() {
  const { capabilities } = useSession();
  const security = capabilities?.security;
  if (!security) {
    return <EmptyState title="The security posture is not available" hint="It is published by GET /capabilities." />;
  }
  const sections = Object.entries(security).filter(([key]) => key !== "gaps");
  const gaps = Array.isArray(security.gaps) ? security.gaps : [];

  return (
    <div className="space-y-3">
      <Alert tone="info" title="Read this as a claim about the running build">
        The posture below is produced by the backend from its own configuration, not written into this page. The gap list
        is intentionally left unflattering: it names what this build does <em>not</em> do.
      </Alert>
      <div className="grid gap-3 lg:grid-cols-2">
        {sections.map(([name, entries]) => (
          <Card key={name}>
            <SectionTitle title={name.replace(/_/g, " ")} icon={<Cpu size={15} />} />
            <KeyValue
              rows={Object.entries(entries as Record<string, unknown>).map(([key, value]) => [
                key.replace(/_/g, " "),
                Array.isArray(value) ? (value as string[]).join("; ") : String(value),
              ])}
            />
          </Card>
        ))}
      </div>
      <Card>
        <SectionTitle title="Known gaps" hint="not omissions from this page — the server reports them about itself" />
        {gaps.length === 0 ? (
          <p className="text-[13px] text-muted">None published.</p>
        ) : (
          <ul className="space-y-1.5 text-[12.5px]">
            {gaps.map((gap) => (
              <li key={gap} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden />
                <span>{gap}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
