import { describe, expect, it } from "vitest";
import { isTrustworthy, needsCaveat, runStatus, valueNoun, verificationStatus } from "./status";

/**
 * These strings are the product. If a stopped-early solve is ever labelled "Converged" or a
 * never-checked run is implied to be correct, the honesty claim of the whole project is void, so
 * the wording is pinned here rather than left to whatever the badge colour happens to suggest.
 */

describe("run status wording", () => {
  it("never calls an unconverged run converged", () => {
    const meta = runStatus("completed_unconverged");
    expect(meta.label).toBe("Stopped early");
    expect(meta.tone).toBe("warning");
    expect(meta.meaning.toLowerCase()).toContain("iterate");
    expect(meta.meaning).not.toMatch(/converged successfully/i);
  });

  it("names the value by what it actually is", () => {
    expect(valueNoun("completed_unconverged")).toBe("last iterate");
    expect(valueNoun("completed_unverified")).toBe("unverified value");
    expect(valueNoun("failed")).toBe("no value");
    expect(valueNoun("completed")).toBe("computed value");
  });

  it("requires a caveat for every non-clean completion", () => {
    expect(needsCaveat("completed_unconverged")).toBe(true);
    expect(needsCaveat("completed_unverified")).toBe(true);
    expect(needsCaveat("failed")).toBe(true);
    expect(needsCaveat("completed")).toBe(false);
    expect(needsCaveat("running")).toBe(false);
  });

  it("treats an unknown status as unknown instead of assuming success", () => {
    const meta = runStatus("quantum_completed");
    expect(meta.label).toBe("quantum completed");
    expect(meta.tone).toBe("neutral");
    expect(meta.meaning).toContain("does not recognise");
  });

  it("only calls a result trustworthy when it converged AND was independently verified", () => {
    expect(isTrustworthy("completed", "passed")).toBe(true);
    expect(isTrustworthy("completed", "unavailable")).toBe(false);
    expect(isTrustworthy("completed_unconverged", "passed")).toBe(false);
    expect(isTrustworthy("completed", "failed")).toBe(false);
  });
});

describe("verification wording", () => {
  it("does not upgrade a missing check to a pass", () => {
    expect(verificationStatus("not_requested").label).toBe("Not verified");
    expect(verificationStatus(null).label).toBe("Not verified");
    expect(verificationStatus("skipped").tone).toBe("neutral");
  });

  it("says a failed cross-check is a contradiction, not a warning", () => {
    const meta = verificationStatus("failed");
    expect(meta.tone).toBe("danger");
    expect(meta.meaning).toMatch(/disagree/i);
  });

  it("keeps 'inconclusive' distinct from 'failed'", () => {
    const meta = verificationStatus("inconclusive");
    expect(meta.tone).toBe("warning");
    expect(meta.meaning).not.toMatch(/wrong/i);
  });
});
