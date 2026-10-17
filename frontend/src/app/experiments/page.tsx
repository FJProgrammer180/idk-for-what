"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Beaker, Files, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { errorMessage, useResource } from "@/lib/resources";
import { formatDateTime, formatRelative } from "@/lib/format";
import { RunBadge } from "@/components/status-badges";
import { Alert, Badge, Button, Card, EmptyState, Field, SectionTitle, Spinner } from "@/components/ui";
import type { ExperimentSummary, Page, RunRow, Template } from "@/lib/types";

/**
 * Experiment history plus the bundled templates.
 *
 * A template is a *starting point with a documented expectation*: the `expected` string is what
 * the method should reproduce, so instantiating one and running it is a self-test of the whole
 * pipeline (solve → cross-check → report), not a demo with canned output. Nothing on this page
 * is prefilled with invented data: an account with no runs shows an empty state.
 */
export default function ExperimentsPage() {
  const router = useRouter();
  const { capabilities } = useSession();
  const experiments = useResource<Page<ExperimentSummary>>("/experiments?page_size=25");
  const templates = useResource<{ templates: Template[] }>("/experiments/templates");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const grouped = useMemo(() => {
    const list = templates.data?.templates ?? [];
    const byCategory = new Map<string, Template[]>();
    for (const template of list) {
      const category = template.tags[0] ?? template.kind;
      byCategory.set(category, [...(byCategory.get(category) ?? []), template]);
    }
    return [...byCategory.entries()];
  }, [templates.data]);

  async function instantiate(template: Template) {
    setBusyKey(template.key);
    setFailure(null);
    try {
      const created = await api.post<{ id: string }>(`/experiments/templates/${template.key}`, {
        parameters: template.example_parameters ?? {},
      });
      router.push(`/experiments/${created.id}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusyKey(null);
    }
  }

  async function blankRun() {
    // A blank experiment still needs a registered method: pick the first one the server offers
    // rather than letting the user invent a name the registry will reject.
    const method = capabilities?.numerical.methods?.[0];
    if (!method) {
      setFailure("The method registry is empty — is the backend running the same build as this UI?");
      return;
    }
    setBusyKey("__blank");
    setFailure(null);
    try {
      const created = await api.post<{ id: string }>("/experiments", {
        title: `Untitled ${method.title.toLowerCase()}`,
        kind: "numerical",
        problem_type: method.problem_type,
        method: method.method,
        parameters: {},
        notes: "Created blank; edit the inputs before running.",
      });
      router.push(`/experiments/${created.id}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusyKey(null);
    }
  }

  const items = (experiments.data?.items ?? []).filter((experiment) =>
    filter.trim() === ""
      ? true
      : `${experiment.title} ${experiment.method} ${experiment.problem_type}`.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Experiments</h1>
          <p className="text-[13px] text-muted">
            A stored spec (method + inputs) with its run history. Reports are built from these rows only.
          </p>
        </div>
        <Button intent="primary" size="sm" onClick={blankRun} disabled={busyKey === "__blank"}>
          {busyKey === "__blank" ? <Spinner /> : <Plus size={15} />} New experiment
        </Button>
      </header>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <Card>
        <SectionTitle
          title="Your experiments"
          hint={`${experiments.data?.total ?? 0} stored`}
          actions={
            <input
              className="field h-8 w-44 text-[12.5px]"
              placeholder="filter by title or method"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              aria-label="Filter experiments"
            />
          }
        />
        {experiments.loading ? (
          <p className="flex items-center gap-2 text-[13px] text-muted">
            <Spinner /> loading…
          </p>
        ) : items.length === 0 ? (
          <EmptyState
            title={filter ? "No experiment matches that filter" : "No experiments yet"}
            hint={filter ? undefined : "Instantiate a template below — each one runs, cross-checks and reports end to end."}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-grid">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Method</th>
                  <th>Runs</th>
                  <th>Latest outcome</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((experiment) => (
                  <tr key={experiment.id}>
                    <td>
                      <Link href={`/experiments/${experiment.id}`} className="font-medium hover:underline">
                        {experiment.title}
                      </Link>
                      {experiment.template_key ? (
                        <span className="ml-2 text-[11px] text-muted">from {experiment.template_key}</span>
                      ) : null}
                    </td>
                    <td className="mono text-[12px]">
                      {experiment.method}
                      <span className="ml-1 text-muted">· {experiment.kind}</span>
                    </td>
                    <td className="mono">{experiment.run_count}</td>
                    <td>{experiment.latest_run ? <LatestRun run={experiment.latest_run} /> : <span className="text-muted">never run</span>}</td>
                    <td className="whitespace-nowrap text-[12px] text-muted" title={formatDateTime(experiment.updated_at)}>
                      {formatRelative(experiment.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle
          title="Bundled templates"
          hint="Each carries the expectation it will be checked against, so a green run here means the whole pipeline worked."
          icon={<Files size={15} />}
        />
        {templates.loading ? (
          <p className="flex items-center gap-2 text-[13px] text-muted">
            <Spinner /> loading templates…
          </p>
        ) : (
          <div className="space-y-4">
            {grouped.map(([category, list]) => (
              <div key={category}>
                <p className="label mb-2">{category}</p>
                <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
                  {list.map((template) => (
                    <div key={template.key} className="flex flex-col gap-2 rounded-xl border border-border bg-surface-2 p-3">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-[13.5px] font-medium leading-snug">{template.title}</p>
                        <Badge tone="neutral">{template.kind}</Badge>
                      </div>
                      <p className="text-[12px] leading-relaxed text-muted">{template.description}</p>
                      {template.expected ? (
                        <p className="text-[11.5px] leading-relaxed">
                          <span className="text-muted">expected: </span>
                          <span className="mono">{template.expected}</span>
                        </p>
                      ) : null}
                      {(template.assumptions ?? []).length > 0 ? (
                        <p className="text-[11px] leading-relaxed text-muted">{template.assumptions[0]}</p>
                      ) : null}
                      <div className="mt-auto flex items-center gap-2 pt-1">
                        <Button size="sm" intent="secondary" onClick={() => instantiate(template)} disabled={busyKey === template.key}>
                          {busyKey === template.key ? <Spinner /> : <Beaker size={13} />} Instantiate
                        </Button>
                        <span className="mono text-[11px] text-muted">{template.method}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function LatestRun({ run }: { run: RunRow }) {
  const value = run.structured_output?.result?.root;
  return (
    <span className="flex flex-wrap items-center gap-2">
      <RunBadge status={run.status} />
      {typeof value === "number" && Number.isFinite(value) ? (
        <span className="mono text-[12px] text-muted">x = {value.toPrecision(10)}</span>
      ) : null}
    </span>
  );
}
