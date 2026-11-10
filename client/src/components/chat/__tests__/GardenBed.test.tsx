import { render } from "@testing-library/react";

import { StudioChatMessages } from "../../studio/StudioChat";
import { GardenBed } from "../GardenBed";

describe("GardenBed", () => {
  it("grows in the walker's track while a reply is on its way, hidden from assistive tech", () => {
    render(<StudioChatMessages messages={[]} isThinking thinkingMessage="Baxter is thinking…" />);

    const bed = document.querySelector(".lg-chat-loading-track .lg-garden-bed");
    expect(bed).not.toBeNull();
    expect(bed?.closest("[aria-hidden]")).not.toBeNull();
  });

  it("is gone once the reply has arrived", () => {
    render(<StudioChatMessages messages={[{ id: "a1", role: "assistant", content: "Done" }]} />);

    expect(document.querySelector(".lg-garden-bed")).toBeNull();
  });

  it("sprouts each plant later the further along the track it sits, following the walker", () => {
    const { container } = render(<GardenBed />);
    const plants = Array.from(container.querySelectorAll<SVGElement>(".lg-garden-plant"));
    const positions = plants.map((plant) => parseFloat(plant.style.left));
    const delays = plants.map((plant) => parseFloat(plant.style.getPropertyValue("--lg-sprout-delay")));

    expect(plants.length).toBeGreaterThan(1);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    for (let i = 1; i < delays.length; i += 1) {
      expect(delays[i]).toBeGreaterThan(delays[i - 1]);
    }
    // The walker's outward crossing is 46% of a 3.6s pace; nothing may sprout after he has turned back.
    expect(Math.max(...delays)).toBeLessThanOrEqual(0.46 * 3.6);
  });
});
