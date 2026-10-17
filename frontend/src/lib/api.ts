/**
 * Thin, typed fetch wrapper around the backend.
 *
 * Rules this file exists to enforce:
 *  - every request goes to the same origin under `/api/v1` (next.config.mjs proxies it),
 *    so the browser never learns - or needs - a backend host;
 *  - mutating requests replay the double-submit CSRF cookie the backend sets;
 *  - a non-2xx response becomes an `ApiFailure` carrying the server's own code and
 *    message. Nothing here guesses a human-readable message when the server gave none, and
 *    nothing silently returns `null` where the caller expects data.
 */

export const API_PREFIX = "/api/v1";
const CSRF_COOKIE = "plab_csrf";
const CSRF_HEADER = "X-CSRF-Token";

export class ApiFailure extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  /** `request_id` from the server envelope: quote it when reporting a failure in the logs. */
  readonly requestId: string | null;

  constructor(
    message: string,
    status: number,
    code = "unknown_error",
    details: unknown = null,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiFailure";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** True when retrying later could plausibly succeed (queue still draining, rate limit). */
  get retryable(): boolean {
    return this.status === 429 || this.status === 503 || this.status >= 500;
  }

  /** A 401 means the session cookie is gone or expired - the UI should show the login screen. */
  get unauthenticated(): boolean {
    return this.status === 401;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
  return match && match[1] ? decodeURIComponent(match[1]) : null;
}

const IDEMPOTENT = new Set(["GET", "HEAD", "OPTIONS"]);

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Send the session cookie (always on for same-origin, kept explicit for clarity). */
  credentials?: RequestCredentials;
  headers?: Record<string, string>;
}

function describeFastApiDetail(detail: unknown): { message: string; code: string } {
  // RequestValidationError -> [{loc, msg, type}]; AppError -> {code, message, details}
  if (typeof detail === "string") return { message: detail, code: "request_error" };
  if (Array.isArray(detail)) {
    const parts = detail.map((entry) => {
      if (!entry || typeof entry !== "object") return String(entry);
      const record = entry as Record<string, unknown>;
      const loc = Array.isArray(record.loc)
        ? record.loc.filter((part) => part !== "body").join(".")
        : "";
      const msg = typeof record.msg === "string" ? record.msg : "invalid value";
      return loc ? `${loc}: ${msg}` : msg;
    });
    return { message: parts.join("; ") || "the request was rejected", code: "validation_error" };
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    const message = typeof record.message === "string" ? record.message : "";
    const code = typeof record.code === "string" ? record.code : "api_error";
    return { message: message || "the request was rejected", code };
  }
  return { message: "the request was rejected", code: "api_error" };
}

async function toFailure(response: Response): Promise<ApiFailure> {
  let payload: unknown = null;
  const text = await response.text().catch(() => "");
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const record = payload as Record<string, unknown>;
    // AppError envelope: {"error": {"code", "message", "details", "request_id"}} - the request id
    // is inside the error object, so it is read there first and kept on the failure for logs.
    const nested = record.error;
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      const inner = nested as Record<string, unknown>;
      const requestId = typeof inner.request_id === "string" ? inner.request_id : typeof record.request_id === "string" ? record.request_id : null;
      const message = typeof inner.message === "string" ? inner.message : `HTTP ${response.status}`;
      const code = typeof inner.code === "string" ? inner.code : `http_${response.status}`;
      return new ApiFailure(message, response.status, code, inner.details ?? null, requestId);
    }
    const requestId = typeof record.request_id === "string" ? record.request_id : null;
    const message = typeof record.message === "string" ? record.message : undefined;
    const code = typeof record.code === "string" ? record.code : `http_${response.status}`;
    const details = record.details ?? record.detail ?? null;
    if (message) return new ApiFailure(message, response.status, code, details, requestId);
    if (details !== null) {
      const { message: derived, code: derivedCode } = describeFastApiDetail(details);
      return new ApiFailure(derived, response.status, code ?? derivedCode, details, requestId);
    }
    return new ApiFailure(`the server responded ${response.status}`, response.status, code, details, requestId);
  }
  if (typeof payload === "string" && payload.trim()) {
    return new ApiFailure(payload.trim().slice(0, 400), response.status, `http_${response.status}`, null);
  }
  return new ApiFailure(
    response.statusText ? `HTTP ${response.status} ${response.statusText}` : `HTTP ${response.status}`,
    response.status,
    `http_${response.status}`,
    null,
  );
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  const isJsonBody = options.body !== undefined && method !== "GET" && method !== "DELETE";
  if (isJsonBody) headers.set("content-type", "application/json");
  if (!IDEMPOTENT.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers.set(CSRF_HEADER, token);
  }

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`, {
      method,
      headers,
      credentials: options.credentials ?? "include",
      body: isJsonBody ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiFailure(
      "the API could not be reached - is the backend running? (uvicorn app.main:app --port 8000)",
      0,
      "network_error",
      error instanceof Error ? error.message : String(error),
    );
  }

  if (!response.ok) throw await toFailure(response);
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return (await response.json()) as T;
  return (await response.text()) as unknown as T;
}

/**
 * Multipart upload (`POST /datasets`). Same CSRF rule as every other mutation, but the body is
 * FormData so the browser sets the boundary; no content-type is claimed here on purpose.
 */
export async function apiUpload<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  const headers = new Headers();
  const token = readCookie(CSRF_COOKIE);
  if (token) headers.set(CSRF_HEADER, token);
  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method: "POST",
      headers,
      credentials: "include",
      body: form,
      signal,
    });
  } catch (error) {
    throw new ApiFailure(
      "the upload did not reach the API",
      0,
      "network_error",
      error instanceof Error ? error.message : String(error),
    );
  }
  if (!response.ok) throw await toFailure(response);
  return (await response.json()) as T;
}

/** Download a binary/text artifact without losing the filename the server chose. */
export async function downloadFile(
  path: string,
  fallbackFilename: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${API_PREFIX}${path}`, { credentials: "include", cache: "no-store" });
  if (!response.ok) throw await toFailure(response);
  const disposition = response.headers.get("content-disposition") ?? "";
  const quoted = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return {
    blob: await response.blob(),
    filename: quoted && quoted[1] ? decodeURIComponent(quoted[1]) : fallbackFilename,
  };
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => apiRequest<T>(path, { signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<T>(path, { method: "POST", body, signal }),
  patch: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PUT", body }),
  del: <T>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
};
