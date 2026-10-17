import { describe, expect, it } from "vitest";
import { buildParams, numberValue, validateFields, fieldsForMethod, parseMatrix, type ParamField } from "./params";
import type { MethodSpec } from "./types";

const spec: MethodSpec = {
  method: "bisection",
  title: "Bisection method",
  problem_type: "root_finding",
  requires: ["expression", "a", "b"],
  optional: ["tolerance", "max_iterations"],
  assumptions: "f continuous on [a, b] and f(a)*f(b) <= 0 (bracketed sign change)",
  order: "linear",
};

const fields = fieldsForMethod(spec);

describe("field derivation", () => {
  it("marks the registry's required inputs as required and the rest optional", () => {
    expect(fields.filter((field) => field.required).map((field) => field.key)).toEqual(["expression", "a", "b"]);
    expect(fields.find((field) => field.key === "tolerance")?.advanced).toBe(true);
  });

  it("types the known parameters so the right widget is rendered", () => {
    expect(fields.find((field) => field.key === "expression")?.kind).toBe("expression");
    expect(fields.find((field) => field.key === "a")?.kind).toBe("number");
    expect(fields.find((field) => field.key === "max_iterations")?.kind).toBe("integer");
  });
});

describe("preconditions the engine enforces", () => {
  it("refuses a bracket that does not bracket", () => {
    const errors = validateFields(fields, { expression: "x^2-2", a: "2", b: "1", tolerance: "" });
    expect(errors.a).toContain("a < b");
  });

  it("accepts a valid bracket", () => {
    expect(validateFields(fields, { expression: "x^2-2", a: "1", b: "2" })).toEqual({});
  });

  it("requires an even panel count for Simpson rather than promoting it", () => {
    const simpson: ParamField[] = [
      { key: "panels", kind: "integer", label: "Panels n (even)", required: false, min: 2, advanced: false },
      { key: "domain", kind: "vector", label: "Integration interval", required: true, advanced: true },
    ];
    expect(validateFields(simpson, { panels: "101", domain: "[0,1]" }).panels).toContain("must be even");
    expect(validateFields(simpson, { panels: "102", domain: "[0,1]" })).not.toHaveProperty("panels");
  });

  it("refuses an expression that the safe parser would reject anyway", () => {
    const errors = validateFields(fields, { expression: "import os", a: "1", b: "2" });
    expect(errors.expression).toContain("safe parser");
  });

  it("does not invent a value for a missing required input", () => {
    const errors = validateFields(fields, { expression: "", a: "1", b: "2" });
    expect(errors.expression).toContain("required");
  });

  it("matches a right-hand side to the matrix size", () => {
    const linear: ParamField[] = [
      { key: "matrix", kind: "matrix", label: "Matrix", required: true, advanced: false },
      { key: "vector", kind: "vector", label: "Vector", required: true, advanced: false },
    ];
    const errors = validateFields(linear, { matrix: "[[1,2],[3,4]]", vector: "[1]" });
    expect(errors.vector).toContain("2 entries");
    expect(validateFields(linear, { matrix: "[[1,2],[3,4]]", vector: "[1,2]" })).toEqual({});
    expect(validateFields(linear, { matrix: "[[1,2,3],[4,5,6]]", vector: "[1,2]" }).matrix).toContain("square");
  });

  it("requires an increasing time span", () => {
    const ode: ParamField[] = [
      { key: "t_span", kind: "vector", label: "Time span", required: true, advanced: false },
      { key: "y0", kind: "vector", label: "Initial state", required: true, advanced: false },
    ];
    expect(validateFields(ode, { t_span: "[1,0]", y0: "[1]" }).t_span).toContain("smaller");
    expect(validateFields(ode, { t_span: "[0,1]", y0: "[1]" })).toEqual({});
  });
});

describe("value parsing", () => {
  it("accepts scientific notation and rejects anything that is not one number", () => {
    expect(numberValue("1e-12")).toBe(1e-12);
    expect(numberValue("-0.5")).toBe(-0.5);
    expect(numberValue("1e-12 3")).toBeNull();
    expect(numberValue("abc")).toBeNull();
    expect(numberValue("Infinity")).toBeNull();
    expect(numberValue("")).toBeNull();
  });

  it("parses a rectangular matrix and refuses a jagged one", () => {
    expect(parseMatrix("[[2,1],[1,3]]")).toEqual([[2, 1], [1, 3]]);
    expect(parseMatrix("[[2,1],[1]]")).toBeNull();
    expect(parseMatrix("not json")).toBeNull();
  });

  it("converts text fields into the JSON the API expects", () => {
    const params = buildParams(fields, { expression: "x^2-2", a: "1", b: "2", tolerance: "1e-12", max_iterations: "40" });
    expect(params).toEqual({ expression: "x^2-2", a: 1, b: 2, tolerance: 1e-12, max_iterations: 40 });
  });

  it("splits multi-line right-hand sides into a list", () => {
    const listField: ParamField[] = [{ key: "expressions", kind: "expression-list", label: "RHS", required: true, advanced: false }];
    expect(buildParams(listField, { expressions: "y2\n-y1" })).toEqual({ expressions: ["y2", "-y1"] });
  });

  it("omits empty optional inputs so the server's own default is used", () => {
    expect(buildParams(fields, { expression: "x", a: "0", b: "1", tolerance: "  " })).toEqual({ expression: "x", a: 0, b: 1 });
  });
});
