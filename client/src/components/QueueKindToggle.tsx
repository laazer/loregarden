/**
 * Which of the machine's two queues the board is showing.
 *
 * They are genuinely peer pools rather than a thing and its detail — lanes
 * ration how many agents run at once, docker rations the containers those
 * agents start — so this is a switch between equals, in the card header where
 * the board's own title is. An earlier cut hung Docker off the sidebar's panel
 * tabs, which read as an accessory to the lane board rather than an alternative
 * to it.
 *
 * `role="tablist"` rather than a radiogroup: each option swaps the panel beside
 * it, which is what tabs mean. It carries its own `aria-label` because the rail
 * has a tablist too, and two unlabelled ones are indistinguishable to anyone
 * navigating by landmark.
 */

import "./QueueKindToggle.css";

export type QueueKind = "agents" | "docker";

const OPTIONS: { key: QueueKind; label: string; hint: string }[] = [
  { key: "agents", label: "Agent lanes", hint: "How many agents run at once" },
  { key: "docker", label: "Docker", hint: "Container capacity on this machine" },
];

export function QueueKindToggle({
  value,
  onChange,
}: {
  value: QueueKind;
  onChange: (kind: QueueKind) => void;
}) {
  return (
    <div className="queue-kind-toggle" role="tablist" aria-label="Queue type">
      {OPTIONS.map((option) => (
        <button
          key={option.key}
          type="button"
          role="tab"
          aria-selected={value === option.key}
          title={option.hint}
          className={`queue-kind-btn${value === option.key ? " active" : ""}`}
          onClick={() => onChange(option.key)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
