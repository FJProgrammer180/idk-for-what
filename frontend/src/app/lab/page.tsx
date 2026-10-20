"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Atom, Beaker, Info, Save, Sigma, Play } from "lucide-react";
import { api, ApiFailure } from "@/lib/api";
import { useSession } from "@/lib/session";
import { errorMessage } from "@/lib/resources";
import { buildParams, fieldsForMethod, fieldsForSimulation, validateFields, type ParamField } from "@/lib/params";
import { ParamForm } from "@/components/param-form";
import { RunView } from "@/components/run-view";
import { Alert, Badge, Button, Card, Field, SectionTitle, Spinner } from "@/components/ui";
import type { EphemeralRun, MethodSpec, RunPayload, SimulationSpec, VerificationRecord } from "@/lib/types";

type Mode = "numerical" | "simulation";

/**
 * The compute lab: pick a registered method or model, fill in its documented inputs, run it
 * without saving.
 *
 * Two things are deliberate. First, the run is *ephemeral* and the page says so — an unsaved run
 * cannot appear in a report, which is the whole point of the report pipeline. Second, the
 * precondition check runs here only to produce a faster, friendlier version of the server's own
 * refusal; the server still decides, and its answer (including "refused", "not bracketed", "odd
 * panel count") is rendered verbatim.
 */
export default function LabPage() {
  const router = useRouter();
  const { capabilities } = useSession();
  const [mode, setMode] = useState<Mode>("numerical");
  const [selected, setSelected] = useState<string>("bisection");
  const [values, setValues] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<{ payload: RunPayload; verification: VerificationRecord | null; inputs: Record<string, unknown> } | null>(null);
  const [chartHint, setChartHint] = useState<string | null>(null);

  const methods = capabilities?.numerical.methods ?? [];
  const simulations = capabilities?.simulations ?? [];
  const activeMethod = useMemo<MethodSpec | null>(
    () => (mode === "numerical" ? methods.find((entry) => entry.method === selected) ?? methods[0] ?? null : null),
    [mode, methods, selected],
  );
  const activeSimulation = useMemo<SimulationSpec | null>(
    () => (mode === "simulation" ? simulations.find((entry) => entry.key === selected) ?? simulations[0] ?? null : null),
    [mode, simulations, selected],
  );

  useEffect(() => {
    if (mode === "numerical" && !methods.some((entry) => entry.method === selected)) {
      setSelected(methods[0]?.method ?? "bisection");
    }
    if (mode === "simulation" && !simulations.some((entry) => entry.key === selected)) {
      setSelected(simulations[0]?.key ?? "projectile_motion");
    }
  }, [mode, methods, simulations, selected]);

  const fields: ParamField[] = useMemo(() => {
    if (mode === "numerical" && activeMethod) return fieldsForMethod(activeMethod);
    if (mode === "simulation" && activeSimulation) return fieldsForSimulation(activeSimulation.parameters ?? {});
    return [];
  }, [mode, activeMethod, activeSimulation]);

  const defaults: Record<string, unknown> = useMemo(() => {
    if (mode !== "simulation" || !activeSimulation) return {};
    const out: Record<string, unknown> = {};
    for (const [key, spec] of Object.entries(activeSimulation.parameters ?? {})) {
      if (spec.default !== null && spec.default !== undefined) out[key] = spec.default;
    }
    return out;
  }, [mode, activeSimulation]);

  // The server's own worked example, so the first run anyone does here is a correct one.
  const [example, setExample] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    setExample(null);
    if (mode !== "numerical" || !activeMethod) return;
    let cancelled = false;
    api
      .get<{ example_parameters?: Record<string, unknown> }>(`/compute/methods/${activeMethod.method}`)
      .then((detail) => {
        if (!cancelled && detail.example_parameters) setExample(detail.example_parameters);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [mode, activeMethod]);

  useEffect(() => {
    setValues({});
    setErrors({});
    setResult(null);
    setFailure(null);
    setChartHint(null);
  }, [mode, selected]);

  function switchMode(next: Mode) {
    setMode(next);
    setSelected(next === "numerical" ? "bisection" : "projectile_motion");
  }

  async function loadExample() {
    if (!example) return;
    const flat: Record<string, string> = {};
    for (const [key, value] of Object.entries(example)) {
      flat[key] = typeof value === "string" ? value : JSON.stringify(value);
    }
    setValues(flat);
    setErrors({});
  }

  async function run() {
    setFailure(null);
    const params = buildParams(fields, values);
    const validation = validateFields(fields, values);
    setErrors(validation);
    if (Object.keys(validation).length > 0) {
      setFailure("Fix the highlighted inputs first — these are the method's documented preconditions.");
      return;
    }
    setBusy(true);
    try {
      const response = await api.post<EphemeralRun>("/compute/run", {
        kind: mode,
        method: mode === "numerical" ? activeMethod?.method : activeSimulation?.key,
        parameters: params,
      });
      setResult({ payload: response.result, verification: response.verification, inputs: response.inputs });
      setChartHint("Ephemeral runs are not stored, so there is no run id to chart against; the series below come from the response itself.");
    } catch (cause) {
      const failureMessage = errorMessage(cause);
      setFailure(failureMessage);
      if (cause instanceof ApiFailure && cause.requestId) {
        setFailure(`${failureMessage} (request ${cause.requestId})`);
      }
    } finally {
      setBusy(false);
    }
  }

  async function saveAsExperiment() {
    if (!result) return;
    setBusy(true);
    setFailure(null);
    try {
      const created = await api.post<{ id: string }>("/experiments", {
        title: `${mode === "numerical" ? activeMethod?.title : activeSimulation?.title} (from lab)`,
        kind: mode,
        problem_type: activeMethod?.problem_type ?? "simulation",
        method: mode === "numerical" ? activeMethod?.method : activeSimulation?.key,
        parameters: result.inputs,
        notes: "Created from the compute lab; the run stored here is the one the server verified.",
      });
      router.push(`/experiments/${created.id}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const activeTitle = mode === "numerical" ? activeMethod?.title : activeSimulation?.title;
  const assumptions = mode === "numerical" ? (activeMethod?.assumptions ?? []) : (activeSimulation?.assumptions ?? []);
  const limitations = mode === "simulation" ? (activeSimulation?.limitations ?? []) : [];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Compute lab</h1>
          <p className="max-w-2xl text-[13px] text-muted">
            Ephemeral runs: numbers, convergence record and the independent cross-check, with nothing
            written to your experiment history until you choose to store it.
          </p>
        </div>
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1">
          {(
            [
              ["numerical", "Numerical method", <Sigma key="a" size={14} />],
              ["simulation", "Physical model", <Beaker key="b" size={14} />],
            ] as const
          ).map(([value, label, icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => switchMode(value)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors ${
                mode === value ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
            >
              {icon}
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[1.05fr_1fr]">
        <Card className="space-y-3">
          <Field label={mode === "numerical" ? "Method" : "Model"} htmlFor="picker">
            <select id="picker" className="field" value={selected} onChange={(event) => setSelected(event.target.value)}>
              {mode === "numerical"
                ? methods.map((entry) => (
                    <option key={entry.method} value={entry.method}>
                      {entry.title} · {entry.problem_type}
                    </option>
                  ))
                : simulations.map((entry) => (
                    <option key={entry.key} value={entry.key}>
                      {entry.title}
                      {entry.category ? ` · ${entry.category}` : ""}
                    </option>
                  ))}
            </select>
          </Field>

          {assumptions.length > 0 || limitations.length > 0 ? (
            <div className="rounded-xl border border-border bg-surface-2 p-3 text-[12px] leading-relaxed">
              <p className="mb-1 flex items-center gap-1.5 font-medium">
                <Info size={13} className="text-accent" /> What this {mode === "numerical" ? "method" : "model"} assumes
              </p>
              {typeof assumptions === "string" ? (
                <p className="text-muted">{assumptions}</p>
              ) : (
                <ul className="list-disc space-y-0.5 pl-4 text-muted">
                  {(assumptions as string[]).map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
              {limitations.length > 0 ? (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-muted">
                  {limitations.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <ParamForm fields={fields} values={values} onChange={(key, next) => setValues((current) => ({ ...current, [key]: next }))} errors={errors} disabled={busy} defaults={defaults} />

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button intent="primary" onClick={run} disabled={busy}>
              {busy ? <Spinner /> : <Play size={15} />}
              {busy ? "computing…" : "Run"}
            </Button>
            {example ? (
              <Button intent="ghost" size="sm" onClick={loadExample}>
                load the worked example
              </Button>
            ) : null}
            {result ? (
              <Button intent="secondary" size="sm" onClick={saveAsExperiment} disabled={busy}>
                <Save size={14} /> store as an experiment
              </Button>
            ) : null}
          </div>

          {failure ? <Alert tone="danger">{failure}</Alert> : null}
        </Card>

        <div className="space-y-4">
          {!result && !busy ? (
            <Card>
              <SectionTitle title="Nothing computed yet" icon={<Atom size={15} />} hint="Fill the inputs and press Run. A refusal is a result too — it will be shown as one." />
              <p className="text-[12.5px] leading-relaxed text-muted">
                {mode === "numerical"
                  ? "Every method here is a real implementation in the backend registry (no eval, no external solver black box), and each one's precondition is enforced rather than papered over: bisection needs a bracket, Simpson needs an even panel count, a direct solver ignores an iteration budget it cannot use."
                  : "Each model integrates a documented system and reports the window it integrated over. Parameters you do not name are taken from the model's own defaults, and the run records which ones those were."}
              </p>
            </Card>
          ) : null}

          {chartHint ? (
            <p className="text-[11.5px] text-muted">
              <Badge tone="neutral">ephemeral</Badge> {chartHint}
            </p>
          ) : null}

          {result ? (
            <RunView
              payload={result.payload}
              verification={result.verification}
              status={result.payload.status ?? null}
              runId={null}
              inputs={result.inputs}
              compact
            />
          ) : null}

          {busy ? (
            <Card className="flex items-center gap-2 text-[13px] text-muted">
              <Spinner /> the server is computing and cross-checking; long ODE runs stay here until they finish
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
