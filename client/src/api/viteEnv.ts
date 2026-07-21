// The single place `import.meta` is read.
//
// `import.meta` is syntax, not a property of globalThis, and swc emits it verbatim into
// this project's CommonJS Jest output — where evaluating it throws. Keeping it isolated
// here lets jest.config.cjs map this one module to a mock, so the ~120 modules that reach
// the API base transitively stay loadable under Jest.
export const VITE_API_BASE: string | undefined = import.meta.env.VITE_API_BASE;
