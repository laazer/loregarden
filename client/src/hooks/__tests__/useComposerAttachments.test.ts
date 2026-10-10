import { classifyAttachment } from "../useComposerAttachments";

function file(name: string, type: string, bytes = 10): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

describe("classifyAttachment", () => {
  it("accepts the image types the server accepts", () => {
    expect(classifyAttachment(file("a.png", "image/png"))).toEqual({ kind: "image" });
    expect(classifyAttachment(file("a.webp", "image/webp"))).toEqual({ kind: "image" });
  });

  it("treats text by type or by a known extension the browser left untyped", () => {
    expect(classifyAttachment(file("notes", "text/plain"))).toEqual({ kind: "text" });
    expect(classifyAttachment(file("main.py", ""))).toEqual({ kind: "text" });
  });

  it("refuses other types, empty files and files over the kind's limit", () => {
    expect(classifyAttachment(file("a.zip", "application/zip"))).toHaveProperty("error");
    expect(classifyAttachment(file("a.bmp", "image/bmp"))).toHaveProperty("error");
    expect(classifyAttachment(file("empty.txt", "text/plain", 0))).toHaveProperty("error");
    expect(classifyAttachment(file("big.txt", "text/plain", 512 * 1024 + 1))).toHaveProperty("error");
    expect(classifyAttachment(file("ok.png", "image/png", 512 * 1024 + 1))).toEqual({
      kind: "image",
    });
  });
});
