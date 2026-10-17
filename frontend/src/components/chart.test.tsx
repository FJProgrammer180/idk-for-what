import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SeriesChart } from "./chart";

/**
 * jsdom has no layout, so recharts renders nothing measurable; what these tests pin is the
 * disclosure text around the chart: whether every point is drawn, and what is said when there
 * is nothing honest to draw at all.
 */

describe("SeriesChart", () => {
  it("says so when nothing is thinned", () => {
    render(
      <SeriesChart
        series={[{ label: "x", x: [0, 1, 2], y: [1, 0.5, 0.2] }]}
        xAxisLabel="t [s]"
        yAxisLabel="x"
      />,
    );
    expect(screen.getByText(/All 3 computed points are drawn/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is smoothed or interpolated/)).toBeInTheDocument();
  });

  it("discloses the sampling when a dense series has to be thinned", () => {
    const count = 5000;
    const axis = Array.from({ length: count }, (_value, index) => index);
    render(<SeriesChart series={[{ label: "theta", x: axis, y: axis.map((entry) => Math.sin(entry)) }]} />);
    const caption = screen.getByText(/Every \d+-th sample is drawn/);
    expect(caption).toBeInTheDocument();
    expect(caption.textContent).toContain("the computation itself used every point");
  });

  it("refuses to draw an empty axis for a run with no series", () => {
    render(<SeriesChart series={[]} />);
    expect(screen.getByText(/no plottable series/i)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("drops non-finite pairs and says which points were left out", () => {
    render(<SeriesChart series={[{ label: "x", x: [0, 1, 2, 3], y: [1, Number.NaN, 0.5, 0.2] }]} />);
    const caption = screen.getByText(/3 of 4 points are finite/);
    expect(caption).toBeInTheDocument();
    expect(caption.textContent).toContain("a gap in the line means a gap in the computation");
  });
});
