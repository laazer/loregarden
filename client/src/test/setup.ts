import "@testing-library/jest-dom";
import { configure } from "@testing-library/react";
import { TextDecoder, TextEncoder } from "node:util";

Object.defineProperty(globalThis, "TextEncoder", { value: TextEncoder });
Object.defineProperty(globalThis, "TextDecoder", { value: TextDecoder });

// waitFor/findBy give up after 1 s by default. Under a loaded pre-push (0.4 of
// 2 granted cpus) BaxterChatPage's handoff test was still drawing its loading
// state at 1 s and failed a push whose diff it cannot import, while passing
// twice on the same tree in 3-4 min runs. Same reasoning as testTimeout in
// jest.config.cjs: four tests had already raised it by hand (4000, 10_000).
// A condition that never becomes true still fails, 4 s later.
configure({ asyncUtilTimeout: 5_000 });
