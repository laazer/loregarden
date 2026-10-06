import { render, screen } from "@testing-library/react";

import { PRIMARY_ARTIFACT_TABS } from "../../../lib/appNavigation";
import { stubScrolling } from "../../../test/scrollStubs";
import { ArtifactTabBar } from "../ArtifactTabBar";

test("a deep-linked tab past the strip's edge is centred in the strip, and nothing around it moves", () => {
  // The strip shows 0..300 sideways; the selected tab sits at 500..560. Centring
  // an end tab asks for more than the strip can scroll, and `scrollIntoView`
  // carried that remainder into the app frame.
  const scrolling = stubScrolling(".tab-bar-scroll { overflow-x: auto; }", (element) => {
    if (element.matches(".tab-bar-scroll")) return { left: 0, right: 300 };
    if (element.matches(".tab-btn.active")) return { left: 500, right: 560 };
    return null;
  });
  try {
    render(
      <ArtifactTabBar
        artifactTab={PRIMARY_ARTIFACT_TABS[PRIMARY_ARTIFACT_TABS.length - 1]}
        selectedId="t1"
        hasRunErrors={false}
        artifactCount={0}
        approvalCount={0}
        hasPr={false}
      />,
    );

    expect(scrolling.scrollTo.mock.instances).toEqual([
      screen.getByRole("tablist", { name: "Artifact views" }),
    ]);
    // Tab centre 530, strip centre 150.
    expect(scrolling.scrollTo).toHaveBeenCalledWith({ left: 380, behavior: "auto" });
    expect(scrolling.scrollIntoView).not.toHaveBeenCalled();
  } finally {
    scrolling.restore();
  }
});
