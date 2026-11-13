/**
 * The model delete dialog's body: read-only text (the path, what is shared,
 * which links go too) with no control in it, so it is its own tab stop. It
 * must be reachable by name, absent when no model is chosen, and never swallow
 * the dialog's Escape.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ModelDeleteModal } from "../ModelDeleteModal";
import type { LocalModel } from "../../../api/diskTypes";

jest.mock("../../../api/diskApi", () => ({
  diskApi: { deleteModel: jest.fn() },
}));

const BODY = "What deleting this model does";

const MODEL: LocalModel = {
  id: "ollama:llama3.1:8b",
  source: "ollama",
  name: "llama3.1:8b",
  path: "/Users/me/.ollama/models/manifests/registry.ollama.ai/library/llama3.1/8b",
  size_bytes: 4_900_000_000,
  frees_bytes: 4_900_000_000,
  modified_at: "2026-11-01T00:00:00Z",
  links_to: null,
  broken: false,
  linked_by: ["llama3.1-8b"],
};

function renderModal(model: LocalModel | null) {
  const onClose = jest.fn();
  render(
    <ModelDeleteModal model={model} onClose={onClose} onDeleted={jest.fn()} />,
  );
  return { onClose };
}

describe("the model delete body's keyboard path", () => {
  it("is a named group and a tab stop stepping on to Cancel — AC3", async () => {
    renderModal(MODEL);

    const body = screen.getByRole("group", { name: BODY });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveTextContent(MODEL.path);
    expect(body).toHaveTextContent("these go too: llama3.1-8b");

    // The footer follows the body, Cancel first.
    body.focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("renders nothing when no model is chosen — AC8", () => {
    renderModal(null);

    expect(screen.queryByRole("group", { name: BODY })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lets Escape from inside the body still close the dialog — AC9", () => {
    const { onClose } = renderModal(MODEL);

    const body = screen.getByRole("group", { name: BODY });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
