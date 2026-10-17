/**
 * Number formatting that keeps the difference between "a result" and "an iterate".
 *
 * Computational physics output is meaningless without its error estimate, so the UI prints
 * `value ± error` together whenever the backend supplied both, and never rounds a number into
 * looking more certain than the reported error allows.
 */

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Format with significant digits implied by `error`: if the error estimate is 4e-7 the value
 * is shown to ~8 significant digits, if it is 0.3 the value is shown with two decimals. With
 * no error estimate, 10 significant digits are used and the caller should say so.
 */
export function formatWithUncertainty(value: unknown, error?: unknown): string {
  if (!isFiniteNumber(value)) return formatUnknown(value);
  const digits = significantDigits(error);
  const text = fixedOrExponential(value, digits);
  if (!isFiniteNumber(error) || error <= 0) return text;
  return `${text} ± ${exponential(error, 2)}`;
}

function significantDigits(error: unknown): number {
  // No error estimate means nothing justifies extra precision, but the value is still the
  // exact double the solver returned: print it wide rather than rounding it into looking neat.
  if (!isFiniteNumber(error) || error <= 0) return 10;
  // The value is shown to the decimal place the error lives in (error 0.3 -> tenths), plus one
  // guard digit, which is the usual convention for a reported uncertainty.
  const magnitude = Math.floor(Math.log10(Math.abs(error)));
  return Math.max(0, Math.min(12, 1 - magnitude));
}

function fixedOrExponential(value: number, digits: number): string {
  const magnitude = value === 0 ? -1 : Math.floor(Math.log10(Math.abs(value)));
  if (magnitude < -5 || magnitude >= 12) return exponential(value, Math.max(2, Math.min(9, digits)));
  return trimZeros(value.toFixed(Math.min(12, digits)));
}

function exponential(value: number, digits: number): string {
  if (value === 0) return "0";
  const [mantissa, exponent] = value
    .toExponential(Math.max(1, Math.min(12, digits)))
    .split("e") as [string, string];
  const tidy = mantissa.includes(".") ? mantissa.replace(/\.?0+$/, "") : mantissa;
  return `${tidy}e${exponent.replace("+", "")}`;
}

function trimZeros(text: string): string {
  if (!text.includes(".")) return text;
  return text.replace(/\.?0+$/, "");
}

function formatUnknown(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return Number.isNaN(value) ? "NaN" : value === Infinity ? "+∞" : value === -Infinity ? "−∞" : String(value);
  return String(value);
}

/** Short, readable number for table cells. Keeps the exponent for tiny/huge magnitudes. */
export function formatNumber(value: unknown, digits = 6): string {
  if (!isFiniteNumber(value)) return formatUnknown(value);
  if (value === 0) return "0";
  const magnitude = Math.floor(Math.log10(Math.abs(value)));
  if (magnitude < -4 || magnitude >= 9) return exponential(value, digits);
  return trimZeros(value.toFixed(Math.max(0, digits - 1 - magnitude)));
}

export function formatPercent(value: unknown, digits = 2): string {
  if (!isFiniteNumber(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDuration(ms: unknown): string {
  if (!isFiniteNumber(ms) || ms < 0) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 2 : 0)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = (ms % 60_000) / 1000;
  return `${minutes}m ${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Jakarta",
  }).format(date);
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const delta = Date.now() - then;
  const future = delta < 0;
  const abs = Math.abs(delta);
  const units: [number, string][] = [
    [1000, "s"],
    [60_000, "min"],
    [3_600_000, "h"],
    [86_400_000, "d"],
  ];
  if (abs < 45_000) return abs < 5_000 ? "just now" : `${Math.round(abs / 1000)}s${future ? " ahead" : " ago"}`;
  for (let i = units.length - 1; i >= 0; i -= 1) {
    const [size, label] = units[i]!;
    if (abs >= size) return `${Math.round(abs / size)}${label}${future ? " ahead" : " ago"}`;
  }
  return `${Math.round(abs / 1000)}s ago`;
}

/** Bytes -> human string, base 1024 because artifacts are files on disk. */
export function formatBytes(value: unknown): string {
  if (!isFiniteNumber(value) || value < 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : size < 10 ? 2 : 1)} ${units[unit]}`;
}

export function truncate(text: string, max = 160): string {
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** True for keys whose values are physical numbers, used to build "result" tables. */
export function looksNumeric(value: unknown): boolean {
  return isFiniteNumber(value);
}

export function withUnits(value: string, unit?: string | null): string {
  if (!unit) return value;
  return `${value} ${unit}`;
}
