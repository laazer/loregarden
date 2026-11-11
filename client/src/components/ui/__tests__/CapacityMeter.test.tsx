/**
 * The meter's job is to be honest about a bounded resource.
 *
 * Two of these tests exist for failure modes that would look completely normal
 * on screen: a pool nobody has measured drawn as an empty bar (which reads as
 * "all free" — the opposite of the truth), and a state conveyed only by the
 * colour of the fill.
 */

import { render, screen } from "@testing-library/react";

import { CapacityMeter } from "../CapacityMeter";

const cpus = (value: number) => `${value} cpus`;

describe("CapacityMeter", () => {
  it("reports the level with its units, not a bare percentage", () => {
    render(<CapacityMeter label="CPU" used={3} total={5} format={cpus} />);

    const meter = screen.getByRole("meter", { name: "CPU" });
    expect(meter).toHaveAttribute("aria-valuenow", "3");
    expect(meter).toHaveAttribute("aria-valuemax", "5");
    // "3 cpus of 5 cpus" rather than "60" — the number alone says nothing about
    // what is being measured.
    expect(meter).toHaveAttribute("aria-valuetext", "3 cpus of 5 cpus");
  });

  it("draws no bar for an unmeasured ceiling, and says so", () => {
    render(<CapacityMeter label="CPU" used={0} total={0} format={cpus} />);

    // An empty bar would read as "completely free", which is the most dangerous
    // thing this component could claim about a pool nobody has measured.
    expect(screen.getByText("not measured")).toBeInTheDocument();
    const meter = screen.getByRole("meter", { name: "CPU" });
    expect(meter).toHaveAttribute("data-tone", "unknown");
    expect(meter).not.toHaveAttribute("aria-valuenow");
  });

  it("states the level in text as well as in colour", () => {
    const { rerender } = render(<CapacityMeter label="CPU" used={5} total={5} format={cpus} />);
    // A full pool and a roomy one must be distinguishable without seeing the
    // fill — roughly one man in twelve cannot separate the two hues.
    expect(screen.getByText("5 cpus of 5 cpus")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "CPU" })).toHaveAttribute("data-tone", "full");

    rerender(<CapacityMeter label="CPU" used={1} total={5} format={cpus} />);
    expect(screen.getByRole("meter", { name: "CPU" })).toHaveAttribute("data-tone", "normal");
  });

  it("warns while there is still a little room left", () => {
    render(<CapacityMeter label="CPU" used={4} total={5} format={cpus} />);
    expect(screen.getByRole("meter", { name: "CPU" })).toHaveAttribute("data-tone", "tight");
  });

  it("does not overflow its track when the ledger is briefly over-booked", () => {
    // A reconciliation lag can show more held than the ceiling for a moment.
    render(<CapacityMeter label="CPU" used={9} total={5} format={cpus} />);
    const fill = screen
      .getByRole("meter", { name: "CPU" })
      .querySelector(".capacity-meter-fill") as HTMLElement;
    expect(fill.style.transform).toBe("scaleX(1)");
  });

  it("counts load it does not book, so a nearly unbooked machine under load reads full", () => {
    // The ledger booked 0.25 of 7 cpus while outside processes kept ~10 busy:
    // a meter of bookings alone said "almost idle" while admission said "0 free".
    render(
      <CapacityMeter
        label="CPU"
        used={0.25}
        external={9.75}
        externalLabel="outside the ledger"
        total={7}
        format={cpus}
      />,
    );

    const meter = screen.getByRole("meter", { name: "CPU" });
    expect(meter).toHaveAttribute("data-tone", "full");
    expect(meter).toHaveAttribute(
      "aria-valuetext",
      "0.25 cpus of 7 cpus, plus 9.75 cpus outside the ledger",
    );
    expect(screen.getByText("+ 9.75 cpus outside the ledger")).toBeInTheDocument();
    const outside = meter.querySelector(".capacity-meter-fill--external") as HTMLElement;
    expect(outside.style.transform).toBe("scaleX(1)");
  });

  it("draws no outside segment and no note when there is no outside load", () => {
    render(<CapacityMeter label="CPU" used={1} external={0} total={5} format={cpus} />);
    const meter = screen.getByRole("meter", { name: "CPU" });
    expect(meter.querySelector(".capacity-meter-fill--external")).toBeNull();
    expect(meter).toHaveAttribute("aria-valuetext", "1 cpus of 5 cpus");
  });
});
