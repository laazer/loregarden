import { entriesFromPaste, splitUrls, urlsFromPaste } from "../referenceLibrary";

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

describe("entriesFromPaste", () => {
  it("names each link and keeps what the article said about it", () => {
    expect(entriesFromPaste(`Here are 3 open-source tools worth bookmarking today:\n${ARTICLE}`)).toEqual([
      {
        url: "https://github.com/twentyhq/twenty",
        name: "Twenty",
        why: "The Open-Source CRM Built for AI Agents\nReplaces: Salesforce / HubSpot Enterprise ($150+/user/mo).\nWhy it's trending: A modern, fast CRM, easily deployable via Docker.",
      },
      {
        url: "https://github.com/Automattic/harper",
        name: "Harper",
        why: "Offline, Privacy-First AI Grammar & Writing Engine\nReplaces: Grammarly Premium / Copy.ai subscriptions.",
      },
    ]);
  });

  it("drops an introduction above unnumbered entries by the shape of the next one", () => {
    const text = `Tools worth a look:\nTwenty – a CRM\nGitHub: https://github.com/a/twenty\nHarper – grammar\nGitHub: https://github.com/a/harper`;
    expect(entriesFromPaste(text).map((entry) => [entry.name, entry.why])).toEqual([
      ["Twenty", "a CRM"],
      ["Harper", "grammar"],
    ]);
  });

  it("reads a markdown list with the description on the link's line", () => {
    const text = "- [Twenty](https://github.com/a/twenty) — a CRM for agents\n- **Harper**: https://github.com/a/harper";
    expect(entriesFromPaste(text)).toEqual([
      { url: "https://github.com/a/twenty", name: "Twenty", why: "a CRM for agents" },
      { url: "https://github.com/a/harper", name: "Harper", why: "" },
    ]);
  });

  it("gives a plain list of URLs no names or reasons", () => {
    expect(entriesFromPaste("Some links:\nhttps://github.com/a/one\nhttps://github.com/a/two")).toEqual([
      { url: "https://github.com/a/one", name: "", why: "" },
      { url: "https://github.com/a/two", name: "", why: "" },
    ]);
  });

  it("finds nothing in text with no web link", () => {
    expect(entriesFromPaste("no links here, ftp://nope")).toEqual([]);
  });
});
