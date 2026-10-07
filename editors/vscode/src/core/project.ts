// What the project's metadata answers: which component a name is, which tags a
// template sees, which component draws a template.
import * as nodePath from "node:path";

import { expandMethods } from "./metadata.ts";
import type { ComponentMeta, FilterMeta, FunctionComponentMeta, LibraryMeta, Metadata, TagMeta } from "./metadata.ts";

export interface TagEntry {
  name: string;
  meta: TagMeta;
  /** The library `{% load %}` takes to get it. Null for a builtin. */
  library: string | null;
}

export interface FilterEntry {
  name: string;
  meta: FilterMeta;
  library: string | null;
}

/** What a template may use: the builtins and what it loaded. */
export interface Visible {
  tags: Map<string, TagEntry>;
  filters: Map<string, FilterEntry>;
  /** End tag -> the block tags it closes. */
  ends: Map<string, Set<string>>;
  /** A tag between a block's start and end (`else`) -> the block tags it belongs to. */
  intermediates: Map<string, Set<string>>;
}

/** One library a `{% load %}` brings in: all of it, or the names of `{% load a b from library %}`. */
export interface Load {
  library: string;
  names: string[] | null;
}

const WIREVIEW_LIBRARY = /[\\/]wireview[\\/]templatetags[\\/]wireview\.py$/;

/** Whether a tag is the one django-wireview registers, not another library's of the same name. */
export function isWireviewTag(entry: TagEntry | undefined): boolean {
  return Boolean(entry && (entry.library === "wireview" || WIREVIEW_LIBRARY.test(entry.meta.file_path)));
}

export function samePath(a: string, b: string): boolean {
  if (process.platform === "win32") return nodePath.normalize(a).toLowerCase() === nodePath.normalize(b).toLowerCase();
  return nodePath.normalize(a) === nodePath.normalize(b);
}

const EMPTY_LIBRARY: LibraryMeta = { tags: {}, filters: {} };

export class Project {
  readonly metadata: Metadata;
  readonly builtins: LibraryMeta;
  readonly libraries: Record<string, LibraryMeta>;
  private readonly componentsByName = new Map<string, ComponentMeta>();
  private readonly visibleCache = new Map<string, Visible>();

  constructor(metadata: Metadata) {
    // 2.0 describes the framework's methods once; the components get them back here
    this.metadata = expandMethods(metadata);
    this.builtins = metadata.template_builtins ?? EMPTY_LIBRARY;
    this.libraries = metadata.template_libraries ?? {};
    // As Component._resolve() looks a name up: the full path, app:Name, then the name alone
    const components = Object.values(this.metadata.components ?? {});
    for (const component of components) this.componentsByName.set(component.name, component);
    for (const component of components) this.componentsByName.set(component.app_key, component);
    for (const component of components) this.componentsByName.set(component.fqn, component);
  }

  get components(): ComponentMeta[] {
    return Object.values(this.metadata.components ?? {});
  }

  get functionComponents(): FunctionComponentMeta[] {
    return Object.values(this.metadata.function_components ?? {});
  }

  /** Whether the project's Django engine was read: without it nothing can be said about tags. */
  get knowsTags(): boolean {
    return Object.keys(this.builtins.tags).length > 0;
  }

  component(name: string): ComponentMeta | undefined {
    return this.componentsByName.get(name);
  }

  functionComponent(name: string): FunctionComponentMeta | undefined {
    const direct = this.metadata.function_components?.[name];
    return direct ?? this.functionComponents.find((candidate) => candidate.fqn === name);
  }

  /** The components that render this template file: whoever `this` is in it. */
  owners(templatePath: string): ComponentMeta[] {
    return this.components.filter((component) => component.template_path && samePath(component.template_path, templatePath));
  }

  /** The function components that render this template file: their parameters are its variables. */
  functionOwners(templatePath: string): FunctionComponentMeta[] {
    return this.functionComponents.filter((fc) => fc.template_path && samePath(fc.template_path, templatePath));
  }

  /** The file a template name loads, as the loaders search: the first directory that has it. */
  resolveTemplate(name: string, isFile: (path: string) => boolean): string | undefined {
    if (!name || name.startsWith("/") || name.includes("..")) return undefined;
    for (const directory of this.metadata.template_dirs ?? []) {
      const candidate = nodePath.join(directory, name);
      if (isFile(candidate)) return candidate;
    }
    return undefined;
  }

  /** The name a loader would find this file under, if it is in a template directory. */
  templateName(filePath: string): string | undefined {
    for (const directory of this.metadata.template_dirs ?? []) {
      const relative = nodePath.relative(directory, filePath);
      if (relative && !relative.startsWith("..") && !nodePath.isAbsolute(relative)) {
        return relative.split(nodePath.sep).join("/");
      }
    }
    return undefined;
  }

  /** The file a `{% load %}` name is: the module the library comes from. */
  libraryFile(name: string): string | undefined {
    return this.libraries[name]?.file_path || undefined;
  }

  /** What a template sees after these loads, in their order: a later one overrides an earlier one, as `Parser.add_library` does. */
  visible(loads: readonly Load[]): Visible {
    const key = JSON.stringify(loads.map((load) => [load.library, load.names]));
    const cached = this.visibleCache.get(key);
    if (cached) return cached;

    const visible: Visible = { tags: new Map(), filters: new Map(), ends: new Map(), intermediates: new Map() };
    const add = (library: string | null, source: LibraryMeta, only?: Set<string>) => {
      for (const [name, meta] of Object.entries(source.tags)) {
        if (!only || only.has(name)) visible.tags.set(name, { name, meta, library });
      }
      for (const [name, meta] of Object.entries(source.filters)) {
        if (!only || only.has(name)) visible.filters.set(name, { name, meta, library });
      }
    };
    add(null, this.builtins);
    for (const load of loads) {
      if (this.libraries[load.library]) add(load.library, this.libraries[load.library], load.names ? new Set(load.names) : undefined);
    }
    for (const entry of visible.tags.values()) {
      if (!entry.meta.end) continue;
      addTo(visible.ends, entry.meta.end, entry.name);
      for (const between of entry.meta.intermediate) addTo(visible.intermediates, between, entry.name);
    }
    this.visibleCache.set(key, visible);
    return visible;
  }

  /** The libraries that register a tag of this name, or end or continue a block with it. */
  librariesWithTag(name: string): string[] {
    return Object.entries(this.libraries)
      .filter(([, library]) =>
        Object.entries(library.tags).some(
          ([tagName, tag]) => tagName === name || tag.end === name || tag.intermediate.includes(name),
        ),
      )
      .map(([libraryName]) => libraryName);
  }

  librariesWithFilter(name: string): string[] {
    return Object.entries(this.libraries)
      .filter(([, library]) => name in library.filters)
      .map(([libraryName]) => libraryName);
  }
}

function addTo(map: Map<string, Set<string>>, key: string, value: string): void {
  const existing = map.get(key);
  if (existing) existing.add(value);
  else map.set(key, new Set([value]));
}
