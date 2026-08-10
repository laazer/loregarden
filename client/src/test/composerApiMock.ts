/** Stand-in for `api/composerApi` — the `@` path lookup and `/note` post-its. */
export const composerApi = {
  editorSearch: jest.fn().mockResolvedValue([]),
  notes: jest.fn().mockResolvedValue([]),
  createNote: jest.fn(),
  updateNote: jest.fn(),
  deleteNote: jest.fn(),
};
