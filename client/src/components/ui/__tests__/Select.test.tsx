/**
 * Select opens a themed list instead of the OS menu, and stays a <select> to
 * everything that drives it: the value, `onChange(event.target.value)`, the
 * combobox role, and the keyboard.
 */

import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { Select } from "../Select";

function ScopePicker({ onChange = jest.fn() }: { onChange?: (value: string) => void }) {
  const [value, setValue] = useState("lg");
  return (
    <Select
      aria-label="Scope"
      value={value}
      onChange={(event) => {
        setValue(event.target.value);
        onChange(event.target.value);
      }}
    >
      <optgroup label="Workspace">
        <option value="lg">loregarden</option>
        <option value="bl">Blobert</option>
      </optgroup>
      <optgroup label="Global">
        <option value="">All workspaces</option>
        <option value="off" disabled>
          Loading initiatives…
        </option>
        <option value="init">Initiative: Always-on agents</option>
      </optgroup>
    </Select>
  );
}

const combobox = () => screen.getByRole("combobox", { name: "Scope" });
const openList = () => fireEvent.mouseDown(combobox(), { button: 0 });

describe("Select", () => {
  it("opens a themed listbox, not the OS menu, and keeps focus on the select", () => {
    render(<ScopePicker />);
    const mouseDown = fireEvent.mouseDown(combobox(), { button: 0 });

    expect(mouseDown).toBe(false); // default prevented: the native menu never opens
    expect(screen.getByRole("listbox", { name: "Scope" })).toBeInTheDocument();
    expect(combobox()).toHaveAttribute("aria-expanded", "true");
    expect(combobox()).toHaveFocus();
  });

  it("shows the optgroups as headings and marks the current option", () => {
    render(<ScopePicker />);
    openList();

    const listbox = screen.getByRole("listbox");
    expect(listbox).toHaveTextContent("Workspace");
    expect(listbox).toHaveTextContent("Global");
    expect(within(listbox).getAllByRole("option", { selected: true })).toHaveLength(1);
    expect(within(listbox).getByRole("option", { name: "loregarden", selected: true })).toBeInTheDocument();
  });

  it("commits a clicked option through the caller's onChange", () => {
    const onChange = jest.fn();
    render(<ScopePicker onChange={onChange} />);
    openList();

    fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Blobert" }));

    expect(onChange).toHaveBeenCalledWith("bl");
    expect(combobox()).toHaveValue("bl");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("walks past disabled options with the arrow keys and commits on Enter", () => {
    const onChange = jest.fn();
    render(<ScopePicker onChange={onChange} />);
    fireEvent.keyDown(combobox(), { key: "ArrowDown" }); // opens on loregarden
    fireEvent.keyDown(combobox(), { key: "ArrowDown" }); // Blobert
    fireEvent.keyDown(combobox(), { key: "ArrowDown" }); // All workspaces
    fireEvent.keyDown(combobox(), { key: "ArrowDown" }); // skips Loading…, lands on the initiative

    const active = combobox().getAttribute("aria-activedescendant");
    expect(document.getElementById(active!)).toHaveTextContent("Initiative: Always-on agents");

    fireEvent.keyDown(combobox(), { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("init");
  });

  it("closes on Escape without letting a dialog underneath see the press", () => {
    const documentEscape = jest.fn();
    document.addEventListener("keydown", documentEscape);
    try {
      render(<ScopePicker />);
      openList();
      fireEvent.keyDown(combobox(), { key: "Escape" });

      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
      expect(documentEscape).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener("keydown", documentEscape);
    }
  });

  it("closes on a press outside and changes nothing", () => {
    const onChange = jest.fn();
    render(<ScopePicker onChange={onChange} />);
    openList();
    fireEvent.mouseDown(document.body);

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("jumps by typed prefix", () => {
    render(<ScopePicker />);
    openList();
    fireEvent.keyDown(combobox(), { key: "i" });

    const active = combobox().getAttribute("aria-activedescendant");
    expect(document.getElementById(active!)).toHaveTextContent("Initiative: Always-on agents");
  });

  it("does not open when disabled", () => {
    render(
      <Select aria-label="Scope" disabled value="a" onChange={jest.fn()}>
        <option value="a">A</option>
      </Select>,
    );
    openList();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("leaves a multi-select as the inline list it already is", () => {
    render(
      <Select aria-label="Scope" multiple defaultValue={["a"]}>
        <option value="a">A</option>
      </Select>,
    );
    expect(screen.getByRole("listbox", { name: "Scope" }).tagName).toBe("SELECT");
  });
});

describe("Select driven as a plain <select>", () => {
  it("closes its list when the value changes by another route", () => {
    render(<ScopePicker />);
    openList();
    fireEvent.change(combobox(), { target: { value: "bl" } });

    expect(combobox()).toHaveValue("bl");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
