/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jsdom",
  // jsdom render tests are slow enough that the 5000 ms default leaves no
  // headroom: on a *passing* full run, five tests measured 4.2-4.9 s — 85-97%
  // of the budget — and any load on the box tips them over. They then fail on
  // time rather than on behaviour, rejecting pushes whose diff never touched
  // them (the mode lg-workflow-integrity-621 made pre-push sequential for).
  //
  // 20 s is not a guess: three suites had already reached for exactly that value
  // file-by-file (AppSidebarScrolling, AppSidebarViewActions, ViewPageGrid), and
  // it is ~10x the idle cost of the slowest affected test. Setting it centrally
  // is what stops the next slow suite needing its own copy.
  //
  // This does not paper over a hang: a deadlocked async test still fails, just
  // 15 s later. A *synchronous* test is not covered by any timeout — jest cannot
  // interrupt a blocked event loop — which is why STRESS-07.1 runs 60 s and
  // passes either way.
  testTimeout: 20_000,
  setupFilesAfterEnv: ["<rootDir>/src/test/setup.ts"],
  transform: {
    "^.+\\.tsx?$": [
      "@swc/jest",
      {
        jsc: {
          parser: {
            syntax: "typescript",
            tsx: true,
            decorators: true,
            dynamicImport: true,
          },
          transform: {
            react: {
              runtime: "automatic",
            },
          },
          loose: true,
        },
        module: {
          type: "commonjs",
        },
      },
    ],
  },
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json"],
  testMatch: ["<rootDir>/src/**/__tests__/**/*.(test|spec).(ts|tsx)"],
  moduleNameMapper: {
    "\\.(css|less|scss|sass)$": "<rootDir>/src/test/styleMock.ts",
    "\\.(png|jpg|jpeg|gif|webp|svg)$": "<rootDir>/src/test/fileMock.ts",
    "^react-markdown$": "<rootDir>/src/test/reactMarkdownMock.tsx",
    "^remark-gfm$": "<rootDir>/src/test/remarkGfmMock.ts",
    "^pixi\\.js$": "<rootDir>/src/test/pixiMock.ts",
    "^@xterm/(xterm|addon-fit)$": "<rootDir>/src/test/xtermMock.ts",
    "^@xyflow/react$": "<rootDir>/src/test/xyflowMock.tsx",
    "^@monaco-editor/react$": "<rootDir>/src/test/monacoMock.ts",
    "(^|/)viteEnv$": "<rootDir>/src/test/viteEnvMock.ts",
  },
};
