import { ApiError } from "../client";
import { useToastStore } from "../../state/toastStore";
import { createQueryClient } from "../queryClient";

async function runFailingMutation(meta?: Record<string, unknown>) {
  const client = createQueryClient();
  const mutation = client
    .getMutationCache()
    .build(client, { mutationFn: async () => Promise.reject(new ApiError(500, "worktree is dirty")), meta });
  await mutation.execute(undefined).catch(() => {});
}

beforeEach(() => {
  useToastStore.getState().clear();
});

it("toasts any mutation that does not complete", async () => {
  await runFailingMutation({ errorTitle: "Start run" });

  const [toast] = useToastStore.getState().toasts;
  expect(toast.title).toBe("Start run failed");
  expect(toast.message).toBe("worktree is dirty");
});

it("still reports a mutation nobody named", async () => {
  await runFailingMutation();

  expect(useToastStore.getState().toasts[0].title).toBe("Action failed");
});

it("stays quiet for a mutation that renders its own failure", async () => {
  await runFailingMutation({ suppressErrorToast: true });

  expect(useToastStore.getState().toasts).toHaveLength(0);
});
