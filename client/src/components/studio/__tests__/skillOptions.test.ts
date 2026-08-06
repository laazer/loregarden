import { notifyStrippedSkills, skillOptions } from "../skillOptions";
import { useToastStore } from "../../../state/toastStore";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("puts the empty skill option first", () => {
  expect(skillOptions(["autopilot", "plan"], "plan")[0]).toEqual({
    value: "",
    label: "— none —",
  });
});

it("appends the selected unlisted skill without replacing listed skills", () => {
  expect(skillOptions(["autopilot", "plan"], "verify")).toEqual([
    { value: "", label: "— none —" },
    { value: "autopilot", label: "autopilot" },
    { value: "plan", label: "plan" },
    { value: "verify", label: "verify (unlisted)" },
  ]);
});

it("does not append an unlisted option for empty or listed values", () => {
  expect(skillOptions(["autopilot", "plan"], "")).toEqual([
    { value: "", label: "— none —" },
    { value: "autopilot", label: "autopilot" },
    { value: "plan", label: "plan" },
  ]);
  expect(skillOptions(["autopilot", "plan"], "plan").filter((option) => option.label.endsWith("(unlisted)"))).toEqual([]);
});

it("pushes one warning toast when publish or restore strips skills", () => {
  notifyStrippedSkills("Workflow published", { stripped_skills: ["consult", "verify"] });

  const [toast] = useToastStore.getState().toasts;
  expect(useToastStore.getState().toasts).toHaveLength(1);
  expect(toast.tone).toBe("warning");
  expect(toast.title).toBe("Workflow published");
  expect(toast.message).toContain("consult");
  expect(toast.message).toContain("verify");
});

it("does not toast when no skills were stripped", () => {
  notifyStrippedSkills("Workflow published", { stripped_skills: [] });
  notifyStrippedSkills("Version restored", {});

  expect(useToastStore.getState().toasts).toHaveLength(0);
});
