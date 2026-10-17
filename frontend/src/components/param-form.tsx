"use client";

import { useMemo, useState } from "react";
import { ChevronDown, Info } from "lucide-react";
import type { ParamField } from "@/lib/params";
import { cn } from "@/lib/cn";
import { Badge, Field } from "./ui";

/**
 * Renders a method's or model's documented input surface as a form.
 *
 * The field list comes from the server (`requires` / `optional` / `parameters`), so a newly
 * registered method appears here without a frontend change; nothing is defaulted invisibly -
 * an empty optional box means "the engine's own documented default will be used", and saying so
 * is what keeps the run reproducible from what is on screen.
 */
export function ParamForm({
  fields,
  values,
  onChange,
  errors = {},
  disabled = false,
  defaults = {},
}: {
  fields: ParamField[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
  /** Server-side defaults, shown as placeholders so "blank" is not confused with "0". */
  defaults?: Record<string, unknown>;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const required = useMemo(() => fields.filter((field) => field.required), [fields]);
  const optional = useMemo(() => fields.filter((field) => !field.required), [fields]);
  const visible = showAdvanced ? fields : required.length > 0 ? required : fields;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {visible.map((field) => (
          <FieldInput
            key={field.key}
            field={field}
            value={values[field.key] ?? ""}
            defaultValue={defaults[field.key]}
            error={errors[field.key] ?? null}
            disabled={disabled}
            onChange={(next) => onChange(field.key, next)}
          />
        ))}
      </div>

      {optional.length > 0 ? (
        <button
          type="button"
          onClick={() => setShowAdvanced((value) => !value)}
          className="inline-flex items-center gap-1.5 text-[12.5px] text-muted hover:text-ink"
          aria-expanded={showAdvanced}
        >
          <ChevronDown size={13} className={cn("transition-transform", showAdvanced ? "rotate-180" : "")} />
          {showAdvanced
            ? "hide optional inputs"
            : `${optional.length} optional input${optional.length === 1 ? "" : "s"} (tolerances, budgets, solver choice)`}
        </button>
      ) : null}

      {visible.some((field) => !field.required) || !showAdvanced ? (
        <p className="flex items-start gap-1.5 text-[11.5px] leading-relaxed text-muted">
          <Info size={12} className="mt-0.5 shrink-0" />
          <span>
            A blank optional box is not a zero: the engine applies its own documented default and the
            run records what it used. Anything outside the accepted parameter list is refused by the
            server rather than ignored.
          </span>
        </p>
      ) : null}
    </div>
  );
}

function FieldInput({
  field,
  value,
  defaultValue,
  error,
  disabled,
  onChange,
}: {
  field: ParamField;
  value: string;
  defaultValue: unknown;
  error: string | null;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const id = `param-${field.key}`;
  const placeholder = field.placeholder ?? defaultPlaceholder(defaultValue);
  const hint = [field.unit ? `unit: ${field.unit}` : null, rangeHint(field)].filter(Boolean).join(" · ");

  return (
    <Field label={`${field.label}${field.unit && !hint ? ` [${field.unit}]` : ""}`} hint={hint} error={error} htmlFor={id} required={field.required}>
      {field.kind === "select" && field.options ? (
        <select id={id} className="field" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
          <option value="">default</option>
          {field.options.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      ) : field.kind === "boolean" ? (
        <select
          id={id}
          className="field"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">default</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : field.kind === "expression-list" || field.kind === "matrix" ? (
        <textarea
          id={id}
          className="field min-h-24 resize-y"
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          spellCheck={false}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          id={id}
          className="field"
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          spellCheck={false}
          inputMode={field.kind === "number" || field.kind === "integer" ? "decimal" : field.kind === "vector" ? "numeric" : "text"}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {field.description ? <p className="text-[11px] leading-snug text-muted">{field.description}</p> : null}
      {!field.required && value.trim() === "" && defaultValue !== undefined && defaultValue !== null ? (
        <p className="text-[11px] text-muted">
          engine default <span className="mono">{String(defaultValue)}</span>
          {field.kind === "number" || field.kind === "integer" ? "" : ""}
        </p>
      ) : null}
      {field.kind === "number" && field.min === 0 ? <Badge tone="neutral" className="ml-1">≥ 0</Badge> : null}
    </Field>
  );
}

function rangeHint(field: ParamField): string | null {
  if (field.min === undefined && field.max === undefined) return null;
  if (field.min !== undefined && field.max !== undefined) return `${field.min} … ${field.max}`;
  if (field.min !== undefined) return `min ${field.min}`;
  return `max ${field.max}`;
}

function defaultPlaceholder(defaultValue: unknown): string {
  if (defaultValue === undefined || defaultValue === null) return "";
  if (typeof defaultValue === "object") return JSON.stringify(defaultValue);
  return String(defaultValue);
}
