import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AutopilotView } from "../../../api/initiativeApi";
import { ApiError } from "../../../api/http";
import { AutopilotTimingButton } from "../AutopilotTiming";

function view(overrides: Partial<AutopilotView> = {}): AutopilotView {
  return {
    enabled: false,
    max_parallel: 3,
    paused_reason: "",
    in_flight: 0,
    next_up: [],
    available: true,
    recent: [],
    stops: [],
    schedule: {},
    stops_at: null,
    next_start: null,
    ...overrides,
  };
}

function renderButton(v: AutopilotView, onSave = jest.fn().mockResolvedValue(undefined)) {
  render(<AutopilotTimingButton view={v} busy={false} onSave={onSave} />);
  return onSave;
}

async function openDialog() {
  const button = screen.getByRole("button", { name: "Timer & schedule" });
  await userEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  return screen.getByRole("dialog");
}

test("a run-for timer saved with Save and turn on sends the stop and the switch together", async () => {
  const onSave = renderButton(view());
  await openDialog();

  await userEvent.click(screen.getByRole("radio", { name: "Run for" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "How long each run lasts" }), "240");
  await userEvent.click(screen.getByRole("button", { name: "Save and turn on" }));

  expect(onSave).toHaveBeenCalledWith({
    enabled: true,
    stops: [{ kind: "run_for", minutes: 240 }],
    schedule: { start_at: null, window: null },
  });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a weekly window is sent in the browser's zone with the days picked", async () => {
  const onSave = renderButton(view({ enabled: true }));
  await openDialog();

  await userEvent.click(screen.getByRole("checkbox", { name: "Every week, in a window" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Fri" }));
  // While on there is nothing to turn on.
  expect(screen.queryByRole("button", { name: "Save and turn on" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Save timer" }));

  expect(onSave).toHaveBeenCalledWith({
    stops: [],
    schedule: {
      start_at: null,
      window: {
        days: [0, 1, 2, 3],
        start: "22:00",
        end: "06:00",
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      },
    },
  });
});

test("a window with no days cannot be saved", async () => {
  const onSave = renderButton(view());
  await openDialog();

  await userEvent.click(screen.getByRole("checkbox", { name: "Every week, in a window" }));
  for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri"]) {
    await userEvent.click(screen.getByRole("checkbox", { name: day }));
  }

  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save timer" })).toBeDisabled();
  expect(onSave).not.toHaveBeenCalled();
});

test("a refused save keeps the dialog open with its fields and the reason", async () => {
  const onSave = renderButton(view(), jest.fn().mockRejectedValue(new ApiError(409, "the start time is already past")));
  await openDialog();

  await userEvent.click(screen.getByRole("radio", { name: "Run for" }));
  await userEvent.click(screen.getByRole("button", { name: "Save timer" }));

  expect(onSave).toHaveBeenCalled();
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Run for" })).toBeChecked();
});

test("the dialog opens on what is saved", async () => {
  renderButton(
    view({
      stops: [{ kind: "run_for", minutes: 60 }],
      schedule: { window: { days: [5, 6], start: "09:00:00", end: "17:00:00", timezone: "UTC" } },
    }),
  );
  await openDialog();

  expect(screen.getByRole("radio", { name: "Run for" })).toBeChecked();
  expect(screen.getByRole("combobox", { name: "How long each run lasts" })).toHaveValue("60");
  expect(screen.getByRole("checkbox", { name: "Sat" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Mon" })).not.toBeChecked();
  expect(screen.getByLabelText("Opens")).toHaveValue("09:00");
});
