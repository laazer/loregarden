import { webPlatform } from "../web";

describe("webPlatform.openInTab", () => {
  afterEach(() => jest.restoreAllMocks());

  it("opens by name, so the next open finds the same tab, and cuts the opener link", async () => {
    const tab = { opener: window, focus: jest.fn() } as unknown as Window;
    const open = jest.spyOn(window, "open").mockReturnValue(tab);
    await webPlatform.openInTab("http://127.0.0.1:8101", "loregarden-instance-a");
    expect(open).toHaveBeenCalledWith("http://127.0.0.1:8101", "loregarden-instance-a");
    // "noopener" would put the tab where the name can no longer find it.
    expect(open.mock.calls[0]).toHaveLength(2);
    expect(tab.opener).toBeNull();
    expect(tab.focus).toHaveBeenCalled();
  });

  it("rejects when the browser blocks the tab, instead of doing nothing", async () => {
    jest.spyOn(window, "open").mockReturnValue(null);
    await expect(webPlatform.openInTab("http://x", "t")).rejects.toThrow(/blocked/);
  });
});
