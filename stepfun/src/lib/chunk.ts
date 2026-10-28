export interface ChunkOptions {
  maxCharacters?: number;
  overlapCharacters?: number;
}

const DEFAULT_MAX_CHARACTERS = 700;
const DEFAULT_OVERLAP_CHARACTERS = 100;

export function normalizeText(value: string): string {
  return value
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function findBoundary(text: string, start: number, targetEnd: number): number {
  if (targetEnd >= text.length) {
    return text.length;
  }

  const lowerBound = start + Math.floor((targetEnd - start) * 0.65);
  const candidates = ["\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", "，", ", "];

  for (const marker of candidates) {
    const index = text.lastIndexOf(marker, targetEnd);
    if (index >= lowerBound) {
      return index + marker.length;
    }
  }

  return targetEnd;
}

export function chunkText(value: string, options: ChunkOptions = {}): string[] {
  const text = normalizeText(value);
  if (!text) {
    return [];
  }

  const maxCharacters = options.maxCharacters ?? DEFAULT_MAX_CHARACTERS;
  const overlapCharacters = options.overlapCharacters ?? DEFAULT_OVERLAP_CHARACTERS;

  if (maxCharacters < 100) {
    throw new Error("maxCharacters must be at least 100");
  }
  if (overlapCharacters < 0 || overlapCharacters >= maxCharacters) {
    throw new Error("overlapCharacters must be between 0 and maxCharacters");
  }

  const chunks: string[] = [];
  let start = 0;

  while (start < text.length) {
    const end = findBoundary(text, start, start + maxCharacters);
    const chunk = text.slice(start, end).trim();
    if (chunk) {
      chunks.push(chunk);
    }
    if (end >= text.length) {
      break;
    }

    const nextStart = Math.max(0, end - overlapCharacters);
    start = nextStart > start ? nextStart : end;
  }

  return chunks;
}

export async function documentFingerprint(name: string, content: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${name}\0${content}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest).slice(0, 12))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
