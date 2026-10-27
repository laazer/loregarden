import { splitUrls, urlsFromPaste } from "../referenceLibrary";

// The shape of a "tools worth bookmarking" post copied from a rendered page.
const ARTICLE = `1. Twenty – The Open-Source CRM Built for AI Agents
• Replaces: Salesforce / HubSpot Enterprise ($150+/user/mo).
• Why it's trending: A modern, fast CRM, easily deployable via Docker.
• GitHub: https://github.com/twentyhq/twenty
2. Harper – Offline, Privacy-First AI Grammar & Writing Engine
• Replaces: Grammarly Premium / Copy.ai subscriptions.
• GitHub: https://github.com/Automattic/harper
3. Repeat
• GitHub: https://github.com/twentyhq/twenty`;

describe("splitUrls", () => {
  it("keeps one-word pieces as written, so the server can name a bad one", () => {
    expect(splitUrls("https://github.com/a/one,ftp://nope\tgithub.com/a/two\n\nhttps://github.com/a/one")).toEqual([
      "https://github.com/a/one",
      "ftp://nope",
      "github.com/a/two",
    ]);
  });

  it("lifts only the http(s) URLs out of a pasted article, once each", () => {
    expect(splitUrls(ARTICLE)).toEqual(["https://github.com/twentyhq/twenty", "https://github.com/Automattic/harper"]);
  });

  it("drops the sentence and markdown around a URL but keeps its own parentheses", () => {
    expect(
      splitUrls(
        "See [docs](https://docs.example.dev/guide). Also (https://github.com/a/b), and https://en.wikipedia.org/wiki/Foo_(bar).",
      ),
    ).toEqual(["https://docs.example.dev/guide", "https://github.com/a/b", "https://en.wikipedia.org/wiki/Foo_(bar)"]);
  });
});

describe("urlsFromPaste", () => {
  it("leaves a plain list to paste as typed", () => {
    expect(urlsFromPaste("https://github.com/a/one\nhttps://github.com/a/two")).toBeNull();
  });

  it("leaves prose with no URL to paste as typed", () => {
    expect(urlsFromPaste("nothing to see here")).toBeNull();
  });

  it("rewrites prose to its URLs", () => {
    expect(urlsFromPaste(ARTICLE)).toEqual(["https://github.com/twentyhq/twenty", "https://github.com/Automattic/harper"]);
  });
});
