// Jest stand-in for src/api/viteEnv.ts (mapped in jest.config.cjs).
// Mirrors the value the old globalThis.import shim in setup.ts used to supply.
export const VITE_API_BASE: string | undefined = "http://127.0.0.1:8000";
