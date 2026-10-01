/**
 * The themed form primitives. What is pinned is the contract the theme gate
 * points people at: which class each variant paints with, that a caller's class
 * is kept beside it, and that a button never submits a form by accident.
 */

import { createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";

import { Button } from "../Button";
import { Input } from "../Input";
import { Select } from "../Select";
import { Textarea } from "../Textarea";

describe("Button", () => {
  it("does not submit the form it sits in", () => {
    const onSubmit = jest.fn((event: { preventDefault: () => void }) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button variant="secondary">Cancel</Button>
      </form>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("still submits when asked to", () => {
    render(<Button variant="primary" type="submit">Save</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "submit");
  });

  it.each([
    ["primary", "btn-primary"],
    ["secondary", "btn-secondary"],
    ["plain", "ui-button-plain"],
  ] as const)("paints the %s variant with %s", (variant, expected) => {
    render(<Button variant={variant}>Go</Button>);
    expect(screen.getByRole("button")).toHaveClass(expected);
  });

  it("keeps the caller's class and the compact size beside the variant", () => {
    render(
      <Button variant="plain" compact className="tab-btn">
        Go
      </Button>,
    );
    expect(screen.getByRole("button")).toHaveClass("ui-button-plain", "btn-compact", "tab-btn");
  });

  it("forwards its ref to the button", () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <Button variant="plain" ref={ref}>
        Go
      </Button>,
    );
    expect(ref.current).toBe(screen.getByRole("button"));
  });
});

describe("Input", () => {
  it.each(["checkbox", "radio"])("gives a %s the accent fill", (type) => {
    render(<Input type={type} aria-label="Pick" />);
    expect(screen.getByLabelText("Pick")).toHaveClass("ui-check");
  });

  it("leaves a text field to the field floor", () => {
    render(<Input type="text" aria-label="Name" className="input" />);
    const field = screen.getByLabelText("Name");
    expect(field).not.toHaveClass("ui-check");
    expect(field).toHaveClass("input");
  });
});

describe("Select and Textarea", () => {
  it("render the element with everything they are given", () => {
    render(
      <>
        <Select aria-label="Stage" className="filter-select" defaultValue="b">
          <option value="a">A</option>
          <option value="b">B</option>
        </Select>
        <Textarea aria-label="Notes" rows={3} />
      </>,
    );
    expect(screen.getByLabelText("Stage")).toHaveValue("b");
    expect(screen.getByLabelText("Stage")).toHaveClass("filter-select");
    expect(screen.getByLabelText("Notes")).toHaveAttribute("rows", "3");
  });
});
