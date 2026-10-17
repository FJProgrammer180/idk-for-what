import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatNumber, formatWithUncertainty, isFiniteNumber } from "./format";

/**
 * The rule these tests protect: the printed precision follows the reported error, and a value
 * that is not a number is never dressed up as one.
 */

describe("formatWithUncertainty", () => {
  it("prints as many digits as the error estimate justifies", () => {
    expect(formatWithUncertainty(1.4142135623730951, 1.8189894035458565e-12)).toBe("1.414213562373 ± 1.82e-12");
  });

  it("stops early when the error is coarse", () => {
    // an error of 0.3 says nothing about the third decimal, so it is not printed
    expect(formatWithUncertainty(3.14159, 0.3)).toBe("3.14 ± 3e-1");
  });

  it("keeps a value without an error estimate visibly unquantified (no fake ±)", () => {
    expect(formatWithUncertainty(2.5, null)).toBe("2.5");
    expect(formatWithUncertainty(2.5, 0)).toBe("2.5");
  });

  it("uses exponential form for extreme magnitudes instead of a wall of zeros", () => {
    // no error estimate -> the full stored double, not a rounded guess
    expect(formatWithUncertainty(2.988746073288406e-11, null)).toBe("2.988746073e-11");
    expect(formatWithUncertainty(0, null)).toBe("0");
  });

  it("labels non-finite values rather than printing NaN as if it were a measurement", () => {
    expect(formatWithUncertainty(Number.NaN, null)).toBe("NaN");
    expect(formatWithUncertainty(Number.POSITIVE_INFINITY, null)).toBe("+∞");
    expect(formatWithUncertainty(null, null)).toBe("—");
    expect(formatWithUncertainty(undefined, null)).toBe("—");
  });
});

describe("formatNumber", () => {
  it("keeps significant digits for small numbers and switches to exponent notation", () => {
    expect(formatNumber(0.000123456)).toBe("0.000123456");
    expect(formatNumber(1234.5678)).toBe("1234.57");
    expect(formatNumber(1e20)).toBe("1e20"); // the plus sign is noise in an exponent
  });

  it("never rounds a small non-zero value to zero", () => {
    expect(formatNumber(1e-9)).toBe("1e-9");
  });
});

describe("durations and sizes", () => {
  it("formats durations in the unit a reader expects", () => {
    expect(formatDuration(0.4)).toBe("<1 ms");
    expect(formatDuration(138.615)).toBe("139 ms");
    expect(formatDuration(2500)).toBe("2.50 s");
    expect(formatDuration(95_000)).toBe("1m 35s");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });

  it("formats byte counts base-1024", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.00 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.00 MB");
  });

  it("isFiniteNumber rejects the strings that look numeric", () => {
    expect(isFiniteNumber("1.5")).toBe(false);
    expect(isFiniteNumber(1.5)).toBe(true);
    expect(isFiniteNumber(Number.NaN)).toBe(false);
  });
});
