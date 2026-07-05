import "@testing-library/jest-dom";

Object.defineProperty(globalThis, "import", {
  value: {
    meta: {
      env: {
        VITE_API_BASE: "http://127.0.0.1:8000",
      },
    },
  },
  configurable: true,
});
