import { fireEvent, render, screen } from "@testing-library/react";

import { StudioChatComposer } from "../StudioChat";

/**
 * The composer stays editable while a turn is in flight so `/stop` can be typed
 * at all. That makes the Enter-to-stop path reachable with a draft in the box,
 * where it would otherwise throw the draft away and kill the run.
 */
function renderComposer(props: Partial<Parameters<typeof StudioChatComposer>[0]> = {}) {
  const onSubmit = jest.fn();
  const onStop = jest.fn();
  const onChange = jest.fn();
  render(
    <StudioChatComposer
      value=""
      onChange={onChange}
      onSubmit={onSubmit}
      onStop={onStop}
      placeholder="Message"
      {...props}
    />,
  );
  return { onSubmit, onStop, onChange, textarea: screen.getByPlaceholderText("Message") };
}

function pressEnter(textarea: HTMLElement) {
  fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
}

it("stops the in-flight turn on Enter when the draft is empty", () => {
  const { onStop, textarea } = renderComposer({ value: "", isSending: true });

  pressEnter(textarea);

  expect(onStop).toHaveBeenCalledTimes(1);
});

it("does not kill the run on Enter when the user has typed a next message", () => {
  const { onStop, onSubmit, textarea } = renderComposer({
    value: "also add tests for the parser",
    isSending: true,
  });

  pressEnter(textarea);

  expect(onStop).not.toHaveBeenCalled();
  // Nor is it sent — an ordinary send is still blocked while a turn is running.
  expect(onSubmit).not.toHaveBeenCalled();
});

it("leaves the Stop control working with a draft in the box", () => {
  const { onStop } = renderComposer({ value: "a draft", isSending: true });

  fireEvent.click(screen.getByRole("button", { name: /stop/i }));

  expect(onStop).toHaveBeenCalledTimes(1);
});

it("sends on Enter when no turn is in flight", () => {
  const { onSubmit, onStop, textarea } = renderComposer({ value: "ship it", isSending: false });

  pressEnter(textarea);

  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(onStop).not.toHaveBeenCalled();
});

it("keeps the composer editable while sending", () => {
  const { textarea } = renderComposer({ value: "", isSending: true });

  expect(textarea).not.toBeDisabled();
});
