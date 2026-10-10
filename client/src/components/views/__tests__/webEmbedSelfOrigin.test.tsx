/**
 * The web embed refuses to frame loregarden itself.
 *
 * The sandbox gives the frame an opaque origin, the dev server refuses module
 * scripts to `Origin: null`, and the app inside the frame never runs — a white
 * sheet with no error. These pin that such a URL gets a refusal naming why,
 * and that every other loopback URL still gets a frame.
 */

import { render, screen } from "@testing-library/react";

import { isAppItself } from "../primitives/embedUrl";
import { ContainerPrimitiveHost } from "../primitives/registry";

const APP = { protocol: "http:", hostname: "localhost", port: "5173" };

describe("isAppItself", () => {
  it("matches the app's own origin, under any path", () => {
    expect(isAppItself("http://localhost:5173/views/v-1", APP)).toBe(true);
  });

  it("treats loopback names as one host", () => {
    expect(isAppItself("http://127.0.0.1:5173/", APP)).toBe(true);
    expect(isAppItself("http://[::1]:5173/", APP)).toBe(true);
  });

  it("does not match another port on loopback", () => {
    expect(isAppItself("http://127.0.0.1:3000/", APP)).toBe(false);
  });

  it("does not match another scheme on the same port", () => {
    expect(isAppItself("https://localhost:5173/", APP)).toBe(false);
  });

  it("does not match a non-loopback host on the same port", () => {
    expect(isAppItself("https://example.com:5173/", { ...APP, protocol: "https:" })).toBe(false);
  });
});

describe("a web embed pointed at the app", () => {
  // jsdom serves the test page from `http://localhost/`.
  it("renders no frame, and offers to open the app outside", () => {
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "web_embed", url: "http://127.0.0.1/views" }}
      />,
    );

    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.getByRole("button", { name: "Open outside" })).toBeInTheDocument();
  });

  it("still frames another loopback server", () => {
    const { container } = render(
      <ContainerPrimitiveHost
        containerId="c1"
        settings={{ primitive_id: "web_embed", url: "http://127.0.0.1:3000/" }}
      />,
    );

    expect(container.querySelector("iframe")).toHaveAttribute("src", "http://127.0.0.1:3000/");
  });
});
