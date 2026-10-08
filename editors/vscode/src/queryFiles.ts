// The disk under src/core/queries.ts: reading the record files, and a file's
// digest and stat for the hints. No vscode here, so that the extension and
// test/queries-driver.ts (the library's contract test) read the same way.
import { createHash } from "node:crypto";
import { closeSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs";

import type { Files } from "./core/queries.ts";

export const nodeFiles: Files = {
  list(directory) {
    try {
      return readdirSync(directory);
    } catch {
      return undefined;
    }
  },
  size(path) {
    try {
      const stat = statSync(path);
      return stat.isFile() ? stat.size : undefined;
    } catch {
      return undefined;
    }
  },
  read(path, start, end) {
    let fd: number | undefined;
    try {
      fd = openSync(path, "r");
      const buffer = Buffer.alloc(Math.max(0, end - start));
      let filled = 0;
      while (filled < buffer.length) {
        const count = readSync(fd, buffer, filled, buffer.length - filled, start + filled);
        if (count === 0) break;
        filled += count;
      }
      return buffer.subarray(0, filled);
    } catch {
      return undefined;
    } finally {
      if (fd !== undefined) closeSync(fd);
    }
  },
};

/** `\r\n` and `\r` made `\n`, as the server's digest does to the source. */
function newlines(bytes: Buffer): Buffer {
  if (!bytes.includes(13)) return bytes;
  const out = Buffer.alloc(bytes.length);
  let length = 0;
  for (let index = 0; index < bytes.length; index++) {
    const byte = bytes[index];
    if (byte === 13) {
      out[length++] = 10;
      if (bytes[index + 1] === 10) index++;
    } else {
      out[length++] = byte;
    }
  }
  return out.subarray(0, length);
}

/** SHA-256 of the bytes with their line ends made `\n`: for UTF-8, the server's digest of the decoded source. */
export function digestOf(bytes: Buffer): string {
  return createHash("sha256").update(newlines(bytes)).digest("hex");
}

/** A file's `[mtime_ns, size]` as decimal strings, from a bigint stat: exact, as the server wrote them. */
export function statOf(path: string): [string, string] | undefined {
  try {
    const stat = statSync(path, { bigint: true });
    return [stat.mtimeNs.toString(), stat.size.toString()];
  } catch {
    return undefined;
  }
}

/** Files' digests, read again only when their stat changes. */
export class Digests {
  private readonly known = new Map<string, { stat: string; digest: string }>();

  of(path: string): string | undefined {
    const stat = statOf(path);
    if (!stat) {
      this.known.delete(path);
      return undefined;
    }
    const key = stat.join(":");
    const cached = this.known.get(path);
    if (cached?.stat === key) return cached.digest;
    let digest: string;
    try {
      digest = digestOf(readFileSync(path));
    } catch {
      return undefined;
    }
    this.known.set(path, { stat: key, digest });
    if (this.known.size > 500) this.known.delete(this.known.keys().next().value as string);
    return digest;
  }
}
