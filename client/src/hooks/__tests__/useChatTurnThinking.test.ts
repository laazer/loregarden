/**
 * The hook that binds a chat panel to the turn it is watching.
 *
 * The interesting behaviour is the handoff: the socket carries what comes
 * next, the REST read carries what already happened, and exactly one of them
 * should be working at a time.
 */

import { act, renderHook, waitFor } from "@testing-library/react";

import { useChatTurnThinking } from "../useChatTurnThinking";

const mockChatTurnThinking = jest.fn();
jest.mock("../../api/client", () => ({
  API_BASE: "http://test",
  api: {
    chatTurnThinking: (turnId: string) => mockChatTurnThinking(turnId),
  },
}));

type Handlers = {
  onFrame: (frame: unknown) => void;
  onStatus: (status: string) => void;
  onDone?: () => void;
};

const sockets: Array<{ handlers: Handlers; closed: boolean }> = [];

jest.mock("../../lib/chatThinkingSocket", () => {
  const actual = jest.requireActual("../../lib/chatThinkingSocket");
  return {
    ...actual,
    ChatThinkingSocket: class {
      private readonly entry: { handlers: Handlers; closed: boolean };
      constructor(_url: string, handlers: Handlers) {
        this.entry = { handlers, closed: false };
        sockets.push(this.entry);
      }
      open() {}
      close() {
        this.entry.closed = true;
      }
    },
  };
});

beforeEach(() => {
  sockets.length = 0;
  mockChatTurnThinking.mockResolvedValue({
    turn_id: "t1",
    content: "from the database",
    answer: "",
    activity: "Read · a.py",
    seq: 1,
  });
});

function latest() {
  return sockets[sockets.length - 1];
}

it("reports nothing, and opens nothing, when no turn is in flight", () => {
  const { result } = renderHook(() => useChatTurnThinking(null));

  expect(result.current).toEqual({
    content: "",
    answer: "",
    activity: "",
    isStreaming: false,
  });
  expect(sockets).toHaveLength(0);
  expect(mockChatTurnThinking).not.toHaveBeenCalled();
});

it("starts from the stored transcript so a panel opened mid-turn is not blank", async () => {
  const { result } = renderHook(() => useChatTurnThinking("t1"));

  await waitFor(() => expect(result.current.content).toBe("from the database"));
  expect(result.current.activity).toBe("Read · a.py");
});

it("stops reading once the socket is carrying the turn", async () => {
  renderHook(() => useChatTurnThinking("t1"));
  await waitFor(() => expect(mockChatTurnThinking).toHaveBeenCalled());

  act(() => latest().handlers.onStatus("open"));
  const readsWhenLive = mockChatTurnThinking.mock.calls.length;

  act(() => {
    latest().handlers.onFrame({ turn_id: "t1", content: "live", activity: "", seq: 2 });
  });

  expect(mockChatTurnThinking).toHaveBeenCalledTimes(readsWhenLive);
});

it("shows pushed frames as they arrive", async () => {
  const { result } = renderHook(() => useChatTurnThinking("t1"));
  act(() => latest().handlers.onStatus("open"));

  act(() => {
    latest().handlers.onFrame({ turn_id: "t1", content: "one", activity: "Thinking", seq: 2 });
  });
  await waitFor(() => expect(result.current.content).toBe("one"));

  act(() => {
    latest().handlers.onFrame({ turn_id: "t1", content: "one two", activity: "Thinking", seq: 3 });
  });
  await waitFor(() => expect(result.current.content).toBe("one two"));
});

it("ignores a read that lands behind what the socket already pushed", async () => {
  const { result } = renderHook(() => useChatTurnThinking("t1"));
  act(() => latest().handlers.onStatus("open"));
  act(() => {
    latest().handlers.onFrame({ turn_id: "t1", content: "ahead", activity: "", seq: 9 });
  });

  // The socket drops; the poll resumes and returns a stale row.
  act(() => latest().handlers.onStatus("closed"));
  await waitFor(() => expect(mockChatTurnThinking).toHaveBeenCalled());

  expect(result.current.content).toBe("ahead");
});

it("stops streaming when the turn settles", async () => {
  const { result } = renderHook(() => useChatTurnThinking("t1"));
  act(() => latest().handlers.onStatus("open"));
  expect(result.current.isStreaming).toBe(true);

  act(() => latest().handlers.onDone?.());

  await waitFor(() => expect(result.current.isStreaming).toBe(false));
});

it("drops the previous turn's transcript when the turn changes", async () => {
  const { result, rerender } = renderHook(({ id }) => useChatTurnThinking(id), {
    initialProps: { id: "t1" },
  });
  act(() => latest().handlers.onStatus("open"));
  act(() => {
    latest().handlers.onFrame({ turn_id: "t1", content: "old turn", activity: "", seq: 4 });
  });

  mockChatTurnThinking.mockResolvedValue({
    turn_id: "t2",
    content: "",
    answer: "",
    activity: "",
    seq: 0,
  });
  rerender({ id: "t2" });

  await waitFor(() => expect(result.current.content).toBe(""));
  expect(sockets[0].closed).toBe(true);
});
