import { renderHook } from "@testing-library/react";

import type { TicketTreeNode } from "../../api/types";
import { useExpandSearchMatches } from "../useExpandSearchMatches";

function node(id: string, children: TicketTreeNode[] = []): TicketTreeNode {
  return {
    id,
    external_id: id,
    title: id,
    state: "in_progress",
    priority: 1,
    work_item_type: "feature",
    workflow_stage_name: "",
    workflow_stage_status: "pending",
    child_count: children.length,
    children,
  };
}

/** What a search for `1239` returned live: the match three levels under its initiative. */
const searched = (): TicketTreeNode[] => [
  node("init-pre-tinkercg-3", [
    node("lg-milestone-that-491", [node("lg-milestone-that-493", [node("lg-milestone-that-1239")])]),
  ]),
];

type Props = { search: string; tree: TicketTreeNode[] | undefined };

function render(initial: Props) {
  const expandPath = jest.fn();
  const hook = renderHook(({ search, tree }: Props) => useExpandSearchMatches(search, tree, expandPath), {
    initialProps: initial,
  });
  return { expandPath, rerender: hook.rerender };
}

test("a search opens every ancestor down to its match", () => {
  const { expandPath } = render({ search: "1239", tree: searched() });

  expect(expandPath).toHaveBeenCalledTimes(1);
  expect(new Set(expandPath.mock.calls[0][0])).toEqual(
    new Set(["init-pre-tinkercg-3", "lg-milestone-that-491", "lg-milestone-that-493"]),
  );
});

test("a refetch of the same search does not reopen what the operator closed", () => {
  const { expandPath, rerender } = render({ search: "1239", tree: searched() });

  rerender({ search: "1239", tree: searched() });

  expect(expandPath).toHaveBeenCalledTimes(1);
});

test("it waits for the searched tree rather than settling on a loading one", () => {
  const { expandPath, rerender } = render({ search: "1239", tree: undefined });
  expect(expandPath).not.toHaveBeenCalled();

  rerender({ search: "1239", tree: searched() });

  expect(expandPath).toHaveBeenCalledTimes(1);
});

test("no search opens nothing, and searching again after clearing opens again", () => {
  const { expandPath, rerender } = render({ search: "  ", tree: searched() });
  expect(expandPath).not.toHaveBeenCalled();

  rerender({ search: "1239", tree: searched() });
  rerender({ search: "", tree: searched() });
  rerender({ search: "1239", tree: searched() });

  expect(expandPath).toHaveBeenCalledTimes(2);
});
