/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jsdom",
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
    "(^|/)viteEnv$": "<rootDir>/src/test/viteEnvMock.ts",
  },
};
