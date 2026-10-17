import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiFailure, api, apiUpload, downloadFile } from "./api";

/**
 * The client's contract with the API: CSRF echoed on writes, the server's own error envelope
 * surfaced verbatim, and no silent `null` when a response is unusable.
 */

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "plab_csrf=token-123";
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "plab_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("request shaping", () => {
  it("prefixes /api/v1 and sends cookies, without a backend host in the URL", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await api.get("/health/ready");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/health/ready");
    expect(init.credentials).toBe("include");
    expect(url).not.toMatch(/127\.0\.0\.1|localhost/);
  });

  it("replays the CSRF cookie on writes only", async () => {
    // a fresh Response per call: a Response body can only be read once
    fetchMock.mockImplementation(async () => jsonResponse({ ok: true }, 201));
    await api.post("/reports", { format: "markdown" });
    const getHeaders = () => {
      const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]!;
      return new Headers((call[1] as RequestInit).headers as Record<string, string>);
    };
    expect(getHeaders().get("X-CSRF-Token")).toBe("token-123");
    expect(getHeaders().get("content-type")).toBe("application/json");

    fetchMock.mockImplementation(async () => jsonResponse({ items: [] }));
    await api.get("/reports");
    const last = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]![1] as RequestInit;
    expect(new Headers(last.headers as Record<string, string>).get("X-CSRF-Token")).toBeNull();
  });

  it("does not set a content-type for multipart uploads (the browser owns the boundary)", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ dataset_id: "dat_1" }, 201));
    const form = new FormData();
    form.append("file", new Blob(["a,b\n1,2\n"], { type: "text/csv" }), "x.csv");
    await apiUpload("/datasets", form);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers as Record<string, string>).get("content-type")).toBeNull();
    expect(new Headers(init.headers as Record<string, string>).get("X-CSRF-Token")).toBe("token-123");
  });

  it("treats 204 as no content instead of parsing a body", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.del("/datasets/dat_1")).resolves.toBeUndefined();
  });
});

describe("failure handling", () => {
  it("unwraps the backend's error envelope and keeps the request id", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ error: { code: "forbidden", message: "not your run", details: null, request_id: "req_9" } }, 403),
    );
    const error = await api.get("/runs/run_1").catch((cause: unknown) => cause as ApiFailure);
    expect(error).toBeInstanceOf(ApiFailure);
    expect((error as ApiFailure).message).toBe("not your run");
    expect((error as ApiFailure).code).toBe("forbidden");
    expect((error as ApiFailure).status).toBe(403);
    expect((error as ApiFailure).requestId).toBe("req_9");
    expect((error as ApiFailure).retryable).toBe(false);
  });

  it("flattens FastAPI validation details into a readable message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ loc: ["body", "parameters", "tolerance"], msg: "Input should be greater than 0", type: "greater_than" }] }, 422),
    );
    const error = (await api.post("/compute/run", {}).catch((cause: unknown) => cause)) as ApiFailure;
    expect(error.status).toBe(422);
    expect(error.message).toContain("parameters.tolerance");
    expect(error.message).toContain("greater than 0");
  });

  it("classifies retryable conditions", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ error: { code: "rate_limited", message: "slow down" } }, 429));
    const limited = (await api.get("/compute/run").catch((cause: unknown) => cause)) as ApiFailure;
    expect(limited.retryable).toBe(true);
    expect(limited.unauthenticated).toBe(false);
  });

  it("says plainly when the backend is unreachable, instead of throwing a bare TypeError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await api.get("/health").catch((cause: unknown) => cause)) as ApiFailure;
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
    expect(error.message).toContain("backend running");
  });

  it("surfaces a non-JSON error body rather than swallowing it", async () => {
    fetchMock.mockResolvedValue(new Response("<html>502 bad gateway</html>", { status: 502, headers: { "content-type": "text/html" } }));
    const error = (await api.get("/health").catch((cause: unknown) => cause)) as ApiFailure;
    expect(error.message).toContain("502 bad gateway");
  });
});

describe("downloads", () => {
  it("keeps the filename the server chose", async () => {
    fetchMock.mockResolvedValue(
      new Response("a,b\n1,2\n", { status: 200, headers: { "content-disposition": 'attachment; filename="run_1.csv"' } }),
    );
    const { filename, blob } = await downloadFile("/datasets/dat_1/export", "fallback.csv");
    expect(filename).toBe("run_1.csv");
    expect(await blob.text()).toContain("a,b");
  });

  it("falls back to a generated name when no disposition header is sent", async () => {
    fetchMock.mockResolvedValue(new Response("# doc", { status: 200 }));
    const { filename } = await downloadFile("/reports/rep_1/download?format=markdown", "report.md");
    expect(filename).toBe("report.md");
  });
});
