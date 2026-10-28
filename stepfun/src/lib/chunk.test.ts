import { describe, expect, it } from "vitest";
import { chunkText, normalizeText } from "./chunk";

describe("normalizeText", () => {
  it("normalizes line endings and repeated blank lines", () => {
    expect(normalizeText("alpha \r\n\r\n\r\n beta")).toBe("alpha\n\n beta");
  });
});

describe("chunkText", () => {
  it("keeps short text in one chunk", () => {
    expect(chunkText("seekdb hybrid search")).toEqual(["seekdb hybrid search"]);
  });

  it("creates bounded chunks with overlap", () => {
    const text = Array.from({ length: 80 }, (_, index) => `sentence-${index}.`).join(" ");
    const chunks = chunkText(text, { maxCharacters: 160, overlapCharacters: 30 });
    expect(chunks.length).toBeGreaterThan(2);
    expect(chunks.every((chunk) => chunk.length <= 160)).toBe(true);
    expect(chunks[0].slice(-20)).not.toBe(chunks[1].slice(0, 20));
  });

  it("rejects invalid overlap", () => {
    expect(() =>
      chunkText("content", { maxCharacters: 100, overlapCharacters: 100 }),
    ).toThrow(/overlapCharacters/);
  });
});
