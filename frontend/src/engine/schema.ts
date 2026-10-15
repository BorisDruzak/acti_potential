export type Condition = {
  field: string;
  op: "eq" | "ne" | "in" | "not_in" | "present" | "absent";
  value?: unknown;
};
export type FieldType =
  | "text"
  | "textarea"
  | "integer"
  | "decimal"
  | "date"
  | "datetime"
  | "boolean"
  | "reference"
  | "multi_reference"
  | "user"
  | "department"
  | "document"
  | "file"
  | "group";
export type Field = {
  key: string;
  label: string;
  type: FieldType;
  description?: string;
  required?: boolean;
  default?: unknown;
  constraints?: Record<string, number | string | boolean>;
  reference_set_id?: string | null;
  reference_code?: string;
  system?: string | null;
  editable?: boolean;
  modes?: string[];
  read_roles?: string[];
  write_roles?: string[];
  filterable?: boolean;
  exportable?: boolean;
  visible_when?: Condition | null;
  required_when?: Condition | null;
  children?: Field[];
};
export type FormSchema = {
  sections: { key: string; title: string; fields: Field[] }[];
};
export type SchemaVersion = {
  id: string;
  document_type_id: string;
  version: number;
  state: string;
  schema: FormSchema;
};
export type Registry = {
  id: string;
  code: string;
  name: string;
  description?: string;
  document_type_ids: string[];
  permissions: string[];
};
export type DocumentType = {
  id: string;
  code: string;
  name: string;
  description?: string;
};
export type ReferenceSet = {
  id: string;
  code: string;
  name: string;
  registry_id?: string | null;
};
export type ReferenceItem = { id: string; label: string; active: boolean };
export type FileVersion = {
  id: string;
  version: number;
  name: string;
  size: number;
  mime: string;
  field_key?: string;
  created_at: string;
};
export type Card = {
  id: string;
  registry_id: string;
  document_type_id: string;
  schema_version_id: string;
  title?: string;
  reg_number?: string;
  reg_date?: string;
  status?: string;
  revision: number;
  current_version: number;
  values: Values;
  schema: FormSchema;
  files: FileVersion[];
  permissions: string[];
  updated_at?: string;
};
export type Values = Record<string, unknown>;
export const fieldTypes: [FieldType, string][] = [
  ["text", "Короткий текст"],
  ["textarea", "Многострочный текст"],
  ["integer", "Целое число"],
  ["decimal", "Десятичное число"],
  ["date", "Дата"],
  ["datetime", "Дата и время"],
  ["boolean", "Да / нет / не указано"],
  ["reference", "Справочник"],
  ["multi_reference", "Множественный выбор"],
  ["user", "Пользователь"],
  ["department", "Подразделение"],
  ["document", "Связанный документ"],
  ["file", "Файл / вложение"],
  ["group", "Повторяемая группа"],
];
export function conditionMatches(
  condition: Condition | null | undefined,
  values: Values,
): boolean {
  if (!condition) return true;
  const actual = values[condition.field];
  switch (condition.op) {
    case "eq":
      return actual === condition.value;
    case "ne":
      return actual !== condition.value;
    case "in":
      return Array.isArray(condition.value) && condition.value.includes(actual);
    case "not_in":
      return (
        Array.isArray(condition.value) && !condition.value.includes(actual)
      );
    case "present":
      return (
        actual !== null &&
        actual !== undefined &&
        actual !== "" &&
        (!Array.isArray(actual) || actual.length > 0)
      );
    case "absent":
      return (
        actual === null ||
        actual === undefined ||
        actual === "" ||
        (Array.isArray(actual) && actual.length === 0)
      );
    default:
      return false;
  }
}
export function initialValues(schema: FormSchema): Values {
  return Object.fromEntries(
    schema.sections
      .flatMap((section) => section.fields)
      .filter((field) => field.default !== undefined)
      .map((field) => [field.key, structuredClone(field.default)]),
  );
}
export function canRead(
  field: Field,
  mode: string,
  roles: string[],
  values: Values,
  displayMode = mode === "view" ? "viewer" : "editor",
) {
  const effectiveRoles = roles.includes("user") ? [...roles, "editor"] : roles;
  return (
    (!field.modes?.length || field.modes.includes(displayMode)) &&
    (!field.read_roles?.length ||
      field.read_roles.some((role) => effectiveRoles.includes(role))) &&
    conditionMatches(field.visible_when, values)
  );
}
export function canWrite(field: Field, mode: string, roles: string[]) {
  const effectiveRoles = roles.includes("user") ? [...roles, "editor"] : roles;
  return (
    mode !== "view" &&
    field.editable !== false &&
    (!field.write_roles?.length ||
      field.write_roles.some((role) => effectiveRoles.includes(role)))
  );
}
export function editableValues(
  schema: FormSchema,
  values: Values,
  mode: string,
  roles: string[],
  conditionValues = values,
): Values {
  return Object.fromEntries(
    schema.sections
      .flatMap((section) => section.fields)
      .filter(
        (field) =>
          canRead(field, mode, roles, conditionValues) &&
          canWrite(field, mode, roles) &&
          field.type !== "file" &&
          Object.hasOwn(values, field.key),
      )
      .map((field) => [
        field.key,
        field.type === "group" && Array.isArray(values[field.key])
          ? (values[field.key] as Values[]).map((row) =>
              editableValues(
                {
                  sections: [
                    { key: "row", title: "", fields: field.children || [] },
                  ],
                },
                row,
                mode,
                roles,
                conditionValues,
              ),
            )
          : values[field.key],
      ]),
  );
}
export function moveItem<T>(items: T[], index: number, direction: number): T[] {
  const target = index + direction;
  if (target < 0 || target >= items.length) return items;
  const next = [...items];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
export function prunePendingFiles<T>(
  pending: Record<string, T>,
  schema: FormSchema,
  values: Values,
  roles: string[],
  mode: "create" | "edit",
  previous?: Values,
): Record<string, T> {
  const allowed = new Set<string>();
  const clearedGroups = new Set<string>();
  for (const field of schema.sections.flatMap((section) => section.fields)) {
    if (!canRead(field, mode, roles, values) || !canWrite(field, mode, roles))
      continue;
    if (field.type === "file") allowed.add(field.key);
    if (field.type !== "group") continue;
    const rows = Array.isArray(values[field.key])
      ? (values[field.key] as Values[])
      : [];
    const oldRows =
      previous && Array.isArray(previous[field.key])
        ? (previous[field.key] as Values[])
        : [];
    if (rows.length < oldRows.length) clearedGroups.add(field.key);
    rows.forEach((_, index) => {
      for (const child of field.children || []) {
        if (
          child.type === "file" &&
          canRead(child, mode, roles, values) &&
          canWrite(child, mode, roles)
        )
          allowed.add(field.key + "." + index + "." + child.key);
      }
    });
  }
  return Object.fromEntries(
    Object.entries(pending).filter(
      ([path]) => allowed.has(path) && !clearedGroups.has(path.split(".")[0]),
    ),
  );
}
