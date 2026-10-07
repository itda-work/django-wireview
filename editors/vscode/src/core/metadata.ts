// The shape `manage.py wireview_lsp` prints (wireview/management/commands/wireview_lsp.py).
// Described in docs/features/editor-support.md; versioned by its own `version` field.

export interface ParameterMeta {
  type: string | null;
  default: unknown;
  has_default: boolean;
  kind: string;
}

export interface MethodMeta {
  is_handler: boolean;
  is_async: boolean;
  parameters: Record<string, ParameterMeta>;
  docstring: string | null;
  file_path: string;
  line_number: number;
}

/** A method the framework defines, described once at the top (2.0): never a handler. */
export type FrameworkMethodMeta = Omit<MethodMeta, "is_handler">;

export interface FieldMeta {
  type: string;
  annotation: string | null;
  default: unknown;
  required: boolean;
  description: string | null;
  in_state: boolean;
}

export interface PropertyMeta {
  type: string | null;
  is_async: boolean;
  docstring: string | null;
  file_path: string;
  line_number: number;
}

export interface SlotMeta {
  required?: boolean;
  doc?: string;
}

export interface ComponentMeta {
  name: string;
  fqn: string;
  app_key: string;
  module: string;
  file_path: string;
  line_number: number;
  kind: "component" | "live_component";
  docstring: string | null;
  template_name: string;
  template_path: string | null;
  fields: Record<string, FieldMeta>;
  accepts_extra_kwargs: boolean;
  properties: Record<string, PropertyMeta>;
  /** 1.x: every public method. 2.0: the component's own; `expandMethods` adds the inherited ones back. */
  methods: Record<string, MethodMeta>;
  /** 2.0: owner -> names of the methods it inherits from the framework, described in `Metadata.framework_methods`. */
  inherited_methods?: Record<string, string[]>;
  slots: Record<string, SlotMeta>;
  subscriptions: string[];
  subscriptions_is_dynamic: boolean;
  temporary_assigns: string[];
}

export interface FunctionComponentMeta {
  name: string;
  fqn: string;
  module: string;
  file_path: string;
  line_number: number;
  docstring: string | null;
  template_name: string;
  template_path: string | null;
  parameters: Record<string, ParameterMeta>;
  slots: Record<string, SlotMeta>;
}

export interface HookMeta {
  static_path: string;
  file_path: string;
  line_number: number;
}

export interface ModifierMeta {
  description: string;
  has_argument: boolean;
  argument: "number" | "text" | null;
}

export interface TagMeta {
  docstring: string | null;
  file_path: string;
  line_number: number;
  /** The tag a block tag runs to. None: not a block, or one whose end could not be read. */
  end: string | null;
  intermediate: string[];
}

export interface FilterMeta {
  docstring: string | null;
  file_path: string;
  line_number: number;
  argument: "none" | "optional" | "required";
  /** Whether `{% filter %}` refuses it (Django's escape and safe). Metadata written by another tool may not say. */
  forbidden_in_filter_tag?: boolean;
}

export interface LibraryMeta {
  module?: string;
  /** The module's file, for `{% load %}` to go to. Absent on the builtins. */
  file_path?: string;
  tags: Record<string, TagMeta>;
  filters: Record<string, FilterMeta>;
}

export interface Metadata {
  version: string;
  wireview_version: string;
  generated_at: string;
  components: Record<string, ComponentMeta>;
  /** 2.0: owner (a framework class's dotted path) -> method name -> what it is. */
  framework_methods?: Record<string, Record<string, FrameworkMethodMeta>>;
  function_components: Record<string, FunctionComponentMeta>;
  hooks: Record<string, HookMeta>;
  modifiers: Record<string, ModifierMeta>;
  template_dirs: string[];
  template_builtins: LibraryMeta;
  template_libraries: Record<string, LibraryMeta>;
}

/**
 * The newest version this extension reads. It reads every major in `READS`
 * from the minor given there: 1.1 lists each component's methods in full, 2.0
 * lists the framework's once (`expandMethods` makes the two alike).
 */
export const METADATA_MAJOR = 2;
export const METADATA_MINOR = 0;
const READS: Record<number, number> = { 1: 1, [METADATA_MAJOR]: METADATA_MINOR };

/** The versions this extension reads, as a reader is told: "1.1 to 2.x". */
export const READABLE_VERSIONS = (() => {
  const oldest = Math.min(...Object.keys(READS).map(Number));
  return `${oldest}.${READS[oldest]} to ${METADATA_MAJOR}.x`;
})();

export type VersionCheck = { ok: true } | { ok: false; reason: "older" | "newer" | "unreadable"; version: string };

/** Whether the extension can read metadata of this version. */
export function checkVersion(metadata: unknown): VersionCheck {
  const version = typeof metadata === "object" && metadata !== null ? (metadata as { version?: unknown }).version : undefined;
  const match = typeof version === "string" ? /^(\d+)\.(\d+)$/.exec(version) : null;
  if (!match) return { ok: false, reason: "unreadable", version: String(version) };
  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (major > METADATA_MAJOR) return { ok: false, reason: "newer", version: match[0] };
  const least = READS[major];
  if (least === undefined || minor < least) return { ok: false, reason: "older", version: match[0] };
  return { ok: true };
}

/**
 * The metadata with each component's inherited methods back in its `methods`,
 * as 1.x listed them: the readers ask a component, not the top, about a name.
 * 1.x metadata comes back as it is. The input is not changed; an entry the
 * components share is one object.
 */
export function expandMethods(metadata: Metadata): Metadata {
  const described = metadata.framework_methods;
  if (!described) return metadata;
  const shared = new Map<string, MethodMeta>();
  const components: Record<string, ComponentMeta> = {};
  for (const [key, component] of Object.entries(metadata.components ?? {})) {
    const methods: Record<string, MethodMeta> = {};
    for (const [owner, names] of Object.entries(component.inherited_methods ?? {})) {
      for (const name of names) {
        const meta = described[owner]?.[name];
        if (!meta) continue;
        const id = `${owner}\0${name}`;
        let method = shared.get(id);
        if (!method) shared.set(id, (method = { ...meta, is_handler: false }));
        methods[name] = method;
      }
    }
    components[key] = { ...component, methods: { ...methods, ...component.methods } };
  }
  return { ...metadata, components };
}
