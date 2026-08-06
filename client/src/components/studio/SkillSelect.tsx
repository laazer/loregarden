import { SKILL_SELECT_TITLE, skillOptions } from "./skillOptions";

interface SkillSelectProps {
  className: string;
  disabled: boolean;
  skills: readonly string[] | undefined;
  value: string;
  onChange: (skillName: string) => void;
}

export function SkillSelect({ className, disabled, skills, value, onChange }: SkillSelectProps) {
  return (
    <select
      className={className}
      value={value}
      disabled={disabled}
      title={SKILL_SELECT_TITLE}
      onChange={(event) => onChange(event.target.value)}
    >
      {skillOptions(skills, value).map((option) => (
        <option key={option.value || "__none__"} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
