import { pushToast } from "../../state/toastStore";

export interface SkillOption {
  value: string;
  label: string;
}

export const SKILL_SELECT_TITLE =
  "The skill list shows this workspace's default skill set. A name not in the list may still resolve.";

export function skillOptions(skills: readonly string[] | undefined, selectedSkill: string): SkillOption[] {
  const listedSkills = skills ?? [];
  const options: SkillOption[] = [
    { value: "", label: "— none —" },
    ...listedSkills.map((skill) => ({ value: skill, label: skill })),
  ];
  if (selectedSkill && !listedSkills.includes(selectedSkill)) {
    options.push({ value: selectedSkill, label: `${selectedSkill} (unlisted)` });
  }
  return options;
}

export function notifyStrippedSkills(title: string, response: { stripped_skills?: string[] }): void {
  const strippedSkills = response.stripped_skills ?? [];
  if (!strippedSkills.length) return;
  pushToast({
    tone: "warning",
    title,
    message: `Removed skills that resolve nowhere: ${strippedSkills.join(", ")}. Those stages now run without a skill.`,
  });
}
