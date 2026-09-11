import { fireEvent, render, screen } from "@testing-library/react";

import { TicketStudioComposer } from "../TicketStudioChat";

/**
 * The Studio composer's Stop must work in exactly the state it exists for.
 *
 * `StudioChatComposer` gates its Stop on `!disabled`, and the panel passes the
 * busy state as `disabled` to hold off its toolbar. Forwarding that through
 * disabled the one control that unlocks a hung session — so `disabled` now
 * gates the toolbar only.
 */
function renderComposer(props: Partial<Parameters<typeof TicketStudioComposer>[0]> = {}) {
  const onStop = jest.fn();
  const onSubmit = jest.fn();
  const onGenerateTickets = jest.fn();
  render(
    <TicketStudioComposer
      value=""
      onChange={jest.fn()}
      onSubmit={onSubmit}
      onStop={onStop}
      placeholder="Message"
      modelLabel="Model · stub"
      onModelClick={jest.fn()}
      onReviewBrief={jest.fn()}
      onGenerateTickets={onGenerateTickets}
      {...props}
    />,
  );
  return { onStop, onSubmit, onGenerateTickets };
}

it("offers a working Stop while the scoper is thinking", () => {
  // The panel's real busy render: thinking, so the toolbar is disabled too.
  const { onStop } = renderComposer({ isSending: true, disabled: true });

  const stop = screen.getByRole("button", { name: /stop/i });
  expect(stop).toBeEnabled();

  fireEvent.click(stop);

  expect(onStop).toHaveBeenCalledTimes(1);
});

it("still holds off the toolbar actions while thinking", () => {
  const { onGenerateTickets } = renderComposer({ isSending: true, disabled: true });

  const generate = screen.getByRole("button", { name: /generate tickets/i });
  expect(generate).toBeDisabled();

  fireEvent.click(generate);

  expect(onGenerateTickets).not.toHaveBeenCalled();
});

it("shows Send, not Stop, when nothing is in flight", () => {
  renderComposer({ isSending: false });

  expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
});
