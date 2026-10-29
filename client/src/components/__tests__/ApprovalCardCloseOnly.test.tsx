import { fireEvent, screen, waitFor } from "@testing-library/react";
import { useLocation } from "react-router-dom";

import type { Approval, SurveyCard } from "../../api/chatTypes";
import { api } from "../../api/client";
import type { ReviewNowResult } from "../../api/referenceLibraryApi";
import { useToastStore } from "../../state/toastStore";
import { renderWithRouter } from "../../test/renderWithRouter";
import { ApprovalCard } from "../ApprovalCard";
import { RouterBridgeSync } from "../RouterBridgeSync";

jest.mock("../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

const CARD: Approval = {
  id: "appr_survey",
  title: "Survey clone failed: aqueli",
  level: "high",
  workspace_slug: "loregarden",
  stage_key: "",
  stage_name: "",
  impact: "Cloning failed.",
  ticket_id: "",
  ticket_external_id: "",
  kind: "human_action",
  prepared_action: null,
  status: "pending",
  run_id: "",
  tool_name: "reference-survey",
  tool_input_json: "{}",
  cli_adapter: "",
};

function survey(kind: SurveyCard["kind"], overrides: Partial<SurveyCard> = {}): Approval {
  return {
    ...CARD,
    close_only: kind !== "admission",
    survey_card: {
      kind,
      source_id: "src_1",
      source_name: "aqueli",
      retryable: kind === "clone_failed" || kind === "pass_abandoned",
      retry_held: "",
      ...overrides,
    },
  };
}

function renderCard(approval: Approval, handlers: { onIgnore?: (reason: string) => void } = {}) {
  const onApprove = jest.fn();
  const onReject = jest.fn();
  renderWithRouter(
    <>
      <RouterBridgeSync />
      <Path />
      <ApprovalCard approval={approval} onApprove={onApprove} onReject={onReject} onIgnore={handlers.onIgnore} />
    </>,
  );
  return { onApprove, onReject };
}

function Path() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

const OPEN_SOURCE = { name: "Open aqueli in the reference library" };

const buttonNames = () => screen.getAllByRole("button").map((b) => b.textContent);

const STARTED: ReviewNowResult = {
  started: true,
  ordinal: 3,
  carrier: "lg-reference-sources-9",
  waiting: "",
  refused: "",
  workspaces_added: [],
  initiatives_linked: [],
};

beforeEach(() => {
  jest.resetAllMocks();
  useToastStore.setState({ toasts: [] });
});

describe("a card whose Approve and Reject would only close it", () => {
  it.each(["clone_failed", "pass_abandoned"] as const)(
    "a %s card offers Retry now, the source and Dismiss, and no Approve or Reject",
    (kind) => {
      renderCard(survey(kind));
      expect(buttonNames()).toEqual(["Open source", "Retry now", "Dismiss"]);
      fireEvent.click(screen.getByRole("button", OPEN_SOURCE));
      expect(screen.getByTestId("path")).toHaveTextContent("/memory/library/src_1");
    },
  );

  it.each(["licence_refused", "licence_disagreement", "pass_not_queued", "write_did_not_land"] as const)(
    "a %s card has nothing to retry: the source and Dismiss only",
    (kind) => {
      renderCard(survey(kind));
      expect(buttonNames()).toEqual(["Open source", "Dismiss"]);
    },
  );

  it("a workspace-wide survey card has no source to open", () => {
    renderCard(survey("no_carrier_parent", { source_id: null, source_name: "" }));
    expect(buttonNames()).toEqual(["Dismiss"]);
  });

  it("Retry now reviews the source once, holds while in flight, and reports what happened", async () => {
    let finish: (result: ReviewNowResult) => void = () => {};
    mockApi.reviewSourceNow.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderCard(survey("clone_failed"));

    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    const pending = await screen.findByRole("button", { name: "Retrying…" });
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(mockApi.reviewSourceNow).toHaveBeenCalledTimes(1);
    expect(mockApi.reviewSourceNow).toHaveBeenCalledWith("src_1", {
      question: "",
      workspace_slugs: [],
      initiative_ids: [],
    });

    finish(STARTED);
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.title).toBe("Reviewing aqueli"));
    expect(screen.getByRole("button", { name: "Retry now" })).toBeEnabled();
  });

  it("a failed retry is reported, not swallowed", async () => {
    mockApi.reviewSourceNow.mockRejectedValue(new Error("network down"));
    renderCard(survey("clone_failed"));
    fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.tone).toBe("error"));
  });

  it("a held retry says why and cannot be clicked", () => {
    renderCard(survey("pass_abandoned", { retry_held: "Pass 4 is reading aqueli now." }));
    expect(screen.getByRole("note")).toHaveTextContent("Pass 4 is reading aqueli now.");
    expect(screen.getByRole("button", { name: "Retry now" })).toBeDisabled();
  });

  it("Dismiss opens the reason dialog where the surface has one", () => {
    const onIgnore = jest.fn();
    const { onReject } = renderCard(survey("clone_failed"), { onIgnore });
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(dismiss).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(dismiss);
    expect(dismiss).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onReject).not.toHaveBeenCalled();
  });

  it("Dismiss closes the card directly where the surface has no Ignore flow", () => {
    const { onReject, onApprove } = renderCard({ ...CARD, close_only: true, survey_card: null });
    expect(buttonNames()).toEqual(["Dismiss"]);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onApprove).not.toHaveBeenCalled();
  });
});

describe("cards whose answer does something keep it", () => {
  it("a survey admission card keeps Approve and Reject: approving admits the source", () => {
    renderCard(survey("admission"));
    expect(buttonNames()).toEqual(["Approve", "Reject"]);
  });

  it("a hand-over with a prepared action keeps Approve and Reject", () => {
    renderCard({
      ...CARD,
      title: "Human action — publish",
      tool_name: "",
      close_only: false,
      prepared_action: { tier: "manual", attempted: "", prepared: "", command: "", script_path: "", captures: [] },
    } as Approval);
    expect(buttonNames()).toEqual(expect.arrayContaining(["Approve", "Reject"]));
  });
});
