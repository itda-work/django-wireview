// Which `.html` files to open as Django templates beyond `**/templates/**`: the
// ones in a directory the project's template engine searches (`template_dirs`,
// from its loaders, so a DIRS entry named anything or set from the environment
// counts). A choice the user made is never undone: `files.associations` naming a
// file `html`, or a document they switched back by hand.

/** A `files.associations` glob as a regular expression, matched case-insensitively as VS Code matches them. */
export function globToRegExp(glob: string): RegExp {
  let source = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*") {
      if (glob[i + 1] === "*") {
        // `**/` is any number of directories, none included; a lone `**` anything at all
        if (glob[i + 2] === "/") {
          source += "(?:.*/)?";
          i += 2;
        } else {
          source += ".*";
          i += 1;
        }
      } else {
        source += "[^/]*";
      }
    } else if (ch === "?") {
      source += "[^/]";
    } else if (ch === "{") {
      const close = glob.indexOf("}", i);
      if (close === -1) {
        source += "\\{";
        continue;
      }
      source += `(?:${glob
        .slice(i + 1, close)
        .split(",")
        .map((part) => globToRegExp(part).source.slice(1, -1))
        .join("|")})`;
      i = close;
    } else if (ch === "[") {
      const close = glob.indexOf("]", i + 1);
      if (close === -1) {
        source += "\\[";
        continue;
      }
      source += `[${glob.slice(i + 1, close).replace(/^!/, "^").replace(/\\/g, "\\\\")}]`;
      i = close;
    } else {
      source += ch.replace(/[.+^$()|\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${source}$`, "i");
}

/**
 * Whether `files.associations` gives this file the `html` language. A pattern with
 * a slash is matched against the path (absolute, or from the workspace folder), one
 * without against the file's name, as VS Code does.
 */
export function associatedWithHtml(associations: Record<string, unknown>, path: string, folder: string | undefined): boolean {
  const posix = path.replace(/\\/g, "/");
  const name = posix.slice(posix.lastIndexOf("/") + 1);
  const root = folder?.replace(/\\/g, "/").replace(/\/$/, "");
  const relative = root && posix.toLowerCase().startsWith(`${root.toLowerCase()}/`) ? posix.slice(root.length + 1) : undefined;
  return Object.entries(associations).some(([pattern, language]) => {
    if (language !== "html") return false;
    const glob = globToRegExp(pattern.replace(/\\/g, "/"));
    if (!pattern.includes("/")) return glob.test(name);
    return glob.test(posix) || (relative !== undefined && glob.test(relative));
  });
}

export interface TemplateDocument {
  languageId: string;
  scheme: string;
  path: string;
  /** Whether a template directory holds it: the project's loaders would find it. */
  inTemplateDirectory: boolean;
  /** The extension switched it once already: whatever it is now, the user chose. */
  switchedBefore: boolean;
}

/** Whether to open a document as `django-html`. */
export function opensAsTemplate(
  document: TemplateDocument,
  associations: Record<string, unknown>,
  folder: string | undefined,
): boolean {
  if (document.languageId !== "html" || document.scheme !== "file") return false;
  if (!document.inTemplateDirectory || document.switchedBefore) return false;
  return !associatedWithHtml(associations, document.path, folder);
}
