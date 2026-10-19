import { act, fireEvent, render, screen } from "@testing-library/react";

import { copyText } from "../../lib/clipboard";
import { pushToast } from "../../state/toastStore";
import { CopyValueButton } from "../CopyValueButton";

jest.mock("../../lib/clipboard", () => ({ copyText: jest.fn() }));
jest.mock("../../state/toastStore", () => ({
  ...jest.requireActual("../../state/toastStore"),
  pushToast: jest.fn(),
}));

const mockCopy = copyText as jest.MockedFunction<typeof copyText>;

beforeEach(() => jest.clearAllMocks());

describe("the value as its own copy button", () => {
  const renderId = () =>
    render(
      <CopyValueButton value="lg-milestone-that-1239" what="ticket id">
        lg-milestone-that-1239
      </CopyValueButton>,
    );

  test("shows the id itself, and clicking it copies the id", async () => {
    mockCopy.mockResolvedValue();
    renderId();

    const button = screen.getByRole("button", { name: "Copy ticket id" });
    expect(button).toHaveTextContent("lg-milestone-that-1239");
    await act(async () => fireEvent.click(button));

    expect(mockCopy).toHaveBeenCalledWith("lg-milestone-that-1239");
    expect(screen.getByRole("button", { name: "Copied ticket id" })).toBeInTheDocument();
  });

  test("a click does not reach the header behind it", async () => {
    mockCopy.mockResolvedValue();
    const onHeaderClick = jest.fn();
    render(
      // ux-ok: a test double for a clickable header, not a rendered surface
      <div onClick={onHeaderClick} role="presentation">
        <CopyValueButton value="lg-1" what="ticket id">
          lg-1
        </CopyValueButton>
      </div>,
    );

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy ticket id" })));

    expect(onHeaderClick).not.toHaveBeenCalled();
  });

  test("a copy that fails says so instead of claiming it copied", async () => {
    mockCopy.mockRejectedValue(new Error("denied"));
    renderId();

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy ticket id" })));

    expect(pushToast).toHaveBeenCalledWith(expect.objectContaining({ tone: "error" }));
    expect(screen.queryByRole("button", { name: "Copied ticket id" })).not.toBeInTheDocument();
  });
});

test("with no face given it is still the icon button it was", () => {
  render(<CopyValueButton value="x" what="ticket number" />);

  const button = screen.getByRole("button", { name: "Copy ticket number" });
  expect(button).toHaveTextContent("");
  expect(button).not.toHaveClass("copy-value-btn--text");
});
