/**
 * Parameter modelling for the compute forms.
 *
 * The client mirrors the backend's *documented* preconditions (a bracket must bracket, Simpson
 * needs an even panel count, a matrix must be rectangular and match its right-hand side) so a
 * user gets the same complaint they would get from the server - but this module never replaces
 * the server's judgement: it only refuses to send input the engine is documented to reject.
 */

import type { MethodSpec, SimulationParameterSpec } from "./types";

export type FieldKind = "number" | "integer" | "expression" | "expression-list" | "vector" | "matrix" | "text" | "select" | "boolean";

export interface ParamField {
  key: string;
  kind: FieldKind;
  label: string;
  required: boolean;
  unit?: string;
  min?: number;
  max?: number;
  description?: string;
  options?: (string | number)[];
  placeholder?: string;
  defaultValue?: unknown;
  advanced: boolean;
}

interface Hint {
  kind?: FieldKind;
  label?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  description?: string;
  options?: (string | number)[];
}

const KNOWN: Record<string, Hint> = {
  expression: { kind: "expression", placeholder: "cos(x) - x", description: "One variable, named x. Parsed by the safe evaluator; no code, no imports." },
  f: { kind: "expression", placeholder: "x^2 - 2" },
  a: { kind: "number", label: "Bracket start (a)" },
  b: { kind: "number", label: "Bracket end (b)" },
  x0: { kind: "number", label: "Initial guess x₀" },
  tol: { kind: "number", min: 0, label: "Tolerance", placeholder: "1e-12" },
  tolerance: { kind: "number", min: 0, label: "Tolerance", placeholder: "1e-12" },
  h: { kind: "number", min: 0, label: "Step size h" },
  matrix: { kind: "matrix", placeholder: "[[2,1],[1,3]]", description: "Rows as nested JSON arrays. Must be square and non-singular for a direct solve." },
  vector: { kind: "vector", placeholder: "[3,5]", description: "One finite number per row of the matrix." },
  points: { kind: "vector", label: "Sample points x", placeholder: "[0, 0.5, 1]" },
  values: { kind: "vector", label: "Observed values y", placeholder: "[1, 0.4, 0.1]" },
  n: { kind: "vector", label: "Group sizes", placeholder: "[8, 12]" },
  domain: { kind: "vector", label: "Integration interval [a, b]", placeholder: "[0, 1]" },
  panels: { kind: "integer", label: "Panels n (even)", min: 2, description: "Simpson needs an even number of panels; an odd value is refused rather than silently changed." },
  expressions: { kind: "expression-list", label: "Right-hand sides", placeholder: "y2\n-y1", description: "One line per state, in t and the state names." },
  t_span: { kind: "vector", label: "Time span [t0, t1]", placeholder: "[0, 6.283]" },
  y0: { kind: "vector", label: "Initial state", placeholder: "[1, 0]" },
  state_names: { kind: "text", label: "State names", placeholder: "x, v", description: "Optional readable names for the states; symbols in the right-hand sides are matched against these." },
  constants: { kind: "text", label: "Constants (JSON object)", placeholder: '{"k": 0.35}' },
  solver: { kind: "select", options: ["RK45", "DOP853", "LSODA", "Radau", "BDF"], description: "DOP853 for smooth non-stiff problems, Radau/BDF when stiffness shows up." },
  max_iterations: { kind: "integer", min: 1, max: 5000, label: "Iteration budget", description: "Reaching this cap is reported as a stopped-early run, not as convergence." },
  n_points: { kind: "integer", min: 2, max: 20000, label: "Output points", description: "How finely the solution is sampled for the table and the chart; the solver's own steps are unaffected." },
  rtol: { kind: "number", min: 0, label: "Relative tolerance", placeholder: "1e-10" },
  atol: { kind: "number", min: 0, label: "Absolute tolerance", placeholder: "1e-12" },
  duration: { kind: "number", min: 0, label: "Integration window", description: "When omitted this is derived as ten natural periods of the model." },
};

export function humanizeKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/^./, (char) => char.toUpperCase())
    .replace(/([a-z])([A-Z])/g, "$1 $2");
}

function kindFromDefault(value: unknown): FieldKind {
  if (Array.isArray(value)) {
    const first = value[0];
    if (Array.isArray(first)) return "matrix";
    if (typeof first === "string") return "expression-list";
    return "vector";
  }
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return Number.isInteger(value) && Math.abs(value) >= 1 ? "integer" : "number";
  if (typeof value === "string") return /sin|cos|exp|log|\bx\b|\^/.test(value) ? "expression" : "text";
  if (value && typeof value === "object") return "text";
  return "text";
}

export function fieldsForMethod(spec: MethodSpec): ParamField[] {
  const requires = new Set(spec.requires);
  const build = (key: string, required: boolean): ParamField => {
    const hint = KNOWN[key] ?? {};
    return {
      key,
      kind: hint.kind ?? "text",
      label: hint.label ?? humanizeKey(key),
      required,
      min: hint.min,
      max: hint.max,
      placeholder: hint.placeholder,
      description: hint.description,
      options: hint.options,
      advanced: !required,
    };
  };
  return [...spec.requires.map((key) => build(key, true)), ...spec.optional.filter((key) => !spec.requires.includes(key)).map((key) => build(key, false))];
}

export function fieldsForSimulation(
  parameters: Record<string, SimulationParameterSpec>,
): ParamField[] {
  return Object.entries(parameters).map(([key, spec]) => {
    const hint = KNOWN[key] ?? {};
    const kind: FieldKind = spec.enum && spec.enum.length > 0 ? "select" : (hint.kind ?? kindFromDefault(spec.default));
    return {
      key,
      kind,
      label: hint.label ?? humanizeKey(key),
      required: false,
      unit: spec.unit ?? undefined,
      min: spec.min ?? undefined,
      max: spec.max ?? undefined,
      description: spec.description,
      options: spec.enum ?? undefined,
      placeholder: hint.placeholder,
      defaultValue: spec.default,
      advanced: false,
    } satisfies ParamField;
  });
}

// --- validation -------------------------------------------------------------

export type FieldErrors = Record<string, string>;

function parseJsonList(raw: string): number[] | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return null;
    const numbers = parsed.map((entry) => (typeof entry === "number" ? entry : Number(entry)));
    return numbers.every((entry) => Number.isFinite(entry)) ? numbers : null;
  } catch {
    return null;
  }
}

export function parseMatrix(raw: string): number[][] | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    const rows = parsed.map((row) => (Array.isArray(row) ? row.map((cell) => Number(cell)) : null));
    if (rows.some((row) => row === null)) return null;
    const numeric = rows as number[][];
    if (numeric.some((row) => row.some((cell) => !Number.isFinite(cell)))) return null;
    // a jagged matrix is refused here too: `buildParams` feeds this parser directly, and a
    // caller that skipped validation must not be able to send a non-rectangular array
    if (numeric.some((row) => row.length !== numeric[0]!.length)) return null;
    return numeric;
  } catch {
    return null;
  }
}

export function numberValue(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  // accept the scientific notation people type (1e-12) and the unicode minus some editors insert
  if (/\s/.test(trimmed)) return null; // "1e-12 3" is two tokens, not a number
  const normalised = trimmed.replace(/−/g, "-");
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(normalised)) return null;
  const value = Number(normalised);
  return Number.isFinite(value) ? value : null;
}

/**
 * Structural pre-flight. Returns one message per field that cannot possibly be accepted, plus a
 * single `_form` entry for cross-field rules (bracket width, vector length).
 */
export function validateFields(fields: ParamField[], values: Record<string, string>): FieldErrors {
  const errors: FieldErrors = {};
  const numbers: Record<string, number> = {};
  const lists: Record<string, number[]> = {};

  for (const field of fields) {
    const raw = values[field.key] ?? "";
    if (!raw.trim()) {
      if (field.required) errors[field.key] = `${field.label} is required by this method.`;
      continue;
    }

    switch (field.kind) {
      case "number":
      case "integer": {
        const value = numberValue(raw);
        if (value === null) {
          errors[field.key] = "Enter one finite number (scientific notation like 1e-12 is fine).";
        } else if (field.kind === "integer" && !Number.isInteger(value)) {
          errors[field.key] = "This must be a whole number.";
        } else if (field.min !== undefined && value < field.min) {
          errors[field.key] = `Must be at least ${field.min}.`;
        } else if (field.max !== undefined && value > field.max) {
          errors[field.key] = `Must be at most ${field.max}.`;
        } else {
          numbers[field.key] = value;
        }
        break;
      }
      case "vector": {
        const list = parseJsonList(raw);
        if (!list || list.length === 0) errors[field.key] = "Expected a JSON array of numbers, e.g. [0, 1].";
        else lists[field.key] = list;
        break;
      }
      case "matrix": {
        const matrix = parseMatrix(raw);
        if (!matrix) errors[field.key] = "Expected nested JSON arrays, e.g. [[2,1],[1,3]].";
        else if (matrix.some((row) => row.length !== matrix[0]!.length)) {
          errors[field.key] = "Every row must have the same number of entries.";
        } else {
          lists[field.key] = matrix.flat();
        }
        break;
      }
      case "expression":
      case "expression-list": {
        if (/(<|>|=|;|\bimport\b|\bdef\b|__|lambda|\.decode|open\()/i.test(raw) && !/^=/.test(raw.trim())) {
          errors[field.key] =
            "The safe parser accepts arithmetic and known functions only: no comparisons, attribute access, imports or dunder names. Write lhs - rhs for an equation.";
        }
        break;
      }
      case "select": {
        if (field.options && !field.options.includes(raw)) {
          errors[field.key] = `Choose one of: ${field.options.join(", ")}.`;
        }
        break;
      }
      case "boolean":
      case "text":
      default:
        break;
    }
  }

  // cross-field rules that mirror the engine's documented preconditions
  const a = numbers.a;
  const b = numbers.b;
  if (a !== undefined && b !== undefined) {
    if (a >= b) errors.a = "A bracket needs a < b.";
  }
  const panelCount = numbers.panels;
  if (panelCount !== undefined && panelCount % 2 !== 0) {
    errors.panels = "Simpson's rule pairs panels, so n must be even. Omit 'panels' to let the rule adapt, or use an even value.";
  }
  if (lists.t_span && lists.t_span.length !== 2) errors.t_span = "A time span is exactly [t0, t1].";
  if (lists.t_span && lists.t_span.length === 2 && lists.t_span[0]! >= lists.t_span[1]!) {
    errors.t_span = "t0 must be smaller than t1.";
  }
  if (lists.y0 && numbers.n_points === undefined && lists.y0.length === 0) errors.y0 = "Give at least one initial value.";
  if (lists.vector && lists.matrix) {
    const side = Math.round(Math.sqrt(lists.matrix.length));
    if (side * side !== lists.matrix.length) {
      errors.matrix = "The matrix has to be square for this solver.";
    } else if (lists.vector.length !== side) {
      errors.vector = `The right-hand side needs exactly ${side} entries to match the ${side}×${side} matrix.`;
    }
  }

  return errors;
}

/** Build the JSON body for `POST /compute/run` from the form values. */
export function buildParams(fields: ParamField[], values: Record<string, string>): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = (values[field.key] ?? "").trim();
    if (!raw) continue;
    switch (field.kind) {
      case "number":
      case "integer": {
        const value = numberValue(raw);
        if (value !== null) params[field.key] = field.kind === "integer" ? Math.round(value) : value;
        break;
      }
      case "vector": {
        const list = parseJsonList(raw);
        if (list) params[field.key] = list;
        break;
      }
      case "matrix": {
        const matrix = parseMatrix(raw);
        if (matrix) params[field.key] = matrix;
        break;
      }
      case "expression-list": {
        const lines = raw
          .split(/\n|,(?=\s*["'`a-z0-9(])/i)
          .map((line) => line.trim())
          .filter(Boolean);
        params[field.key] = lines.length > 0 ? lines : [raw];
        break;
      }
      case "boolean":
        params[field.key] = /^(1|true|yes|on)$/i.test(raw);
        break;
      default:
        params[field.key] = raw;
    }
  }
  return params;
}

/** Pretty-print a stored parameter value for a read-only display. */
export function formatParamValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "non-finite";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
