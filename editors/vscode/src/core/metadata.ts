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
  methods: Record<string, MethodMeta>;
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
  function_components: Record<string, FunctionComponentMeta>;
  hooks: Record<string, HookMeta>;
  modifiers: Record<string, ModifierMeta>;
  template_dirs: string[];
  template_builtins: LibraryMeta;
  template_libraries: Record<string, LibraryMeta>;
}

/** The major version this extension reads, and the minor it needs at least. */
export const METADATA_MAJOR = 1;
export const METADATA_MINOR = 1;

export type VersionCheck = { ok: true } | { ok: false; reason: "older" | "newer" | "unreadable"; version: string };

/** Whether the extension can read metadata of this version. */
export function checkVersion(metadata: unknown): VersionCheck {
  const version = typeof metadata === "object" && metadata !== null ? (metadata as { version?: unknown }).version : undefined;
  const match = typeof version === "string" ? /^(\d+)\.(\d+)$/.exec(version) : null;
  if (!match) return { ok: false, reason: "unreadable", version: String(version) };
  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (major > METADATA_MAJOR) return { ok: false, reason: "newer", version: match[0] };
  if (major < METADATA_MAJOR || minor < METADATA_MINOR) return { ok: false, reason: "older", version: match[0] };
  return { ok: true };
}
