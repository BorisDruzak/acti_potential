import { useEffect, useId, useState } from "react";
import { apiRequest, saveDownload } from "../api/client";
import {
  canRead,
  canWrite,
  conditionMatches,
  initialValues,
  type Field,
  type FileVersion,
  type FormSchema,
  type ReferenceItem,
  type ReferenceSet,
  type Values,
} from "./schema";

type Props = {
  schema: FormSchema;
  values: Values;
  onChange?: (values: Values) => void;
  mode: "create" | "edit" | "view";
  roles: string[];
  registryId?: string;
  references?: ReferenceSet[];
  files?: FileVersion[];
  canDownload?: boolean;
  onFile?: (field: string, file: File) => void;
  prefix?: string;
  conditionValues?: Values;
  conditionFields?: Field[];
  displayMode?: "editor" | "viewer";
};
type Choice = { id: string; label: string; active?: boolean };
function Lookup({
  field,
  value,
  disabled,
  onChange,
  registryId,
  references = [],
  id,
  displayMode = "editor",
}: {
  field: Field;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  registryId?: string;
  references?: ReferenceSet[];
  id: string;
  displayMode?: "editor" | "viewer";
}) {
  const [choices, setChoices] = useState<Choice[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const matchingReferences = references.filter(
    (reference) => reference.code === field.reference_code,
  );
  const referenceId =
    field.reference_set_id ||
    (
      matchingReferences.find(
        (reference) => reference.registry_id === registryId,
      ) || matchingReferences.find((reference) => !reference.registry_id)
    )?.id;
  useEffect(() => {
    const abort = new AbortController();
    let path: string;
    if (field.type === "user") path = "/users/options";
    else if (field.type === "document")
      path = `/documents?${new URLSearchParams({ q: query, page_size: "100", mode: displayMode })}`;
    else if (referenceId)
      path = `/references/${referenceId}/items?mode=${displayMode}`;
    else {
      setChoices([]);
      return;
    }
    setLoading(true);
    setError("");
    apiRequest<
      | ReferenceItem[]
      | { id: string; display_name: string }[]
      | { items: { id: string; title?: string; reg_number?: string }[] }
    >(path, { signal: abort.signal })
      .then((data) => {
        if (abort.signal.aborted) return;
        setChoices(
          Array.isArray(data)
            ? data.map((item) => ({
                id: item.id,
                label: "label" in item ? item.label : item.display_name,
                active: "selectable" in item ? item.selectable : "active" in item ? item.active : true,
              }))
            : data.items.map((item) => ({
                id: item.id,
                label:
                  [item.reg_number, item.title].filter(Boolean).join(" · ") ||
                  item.id,
              })),
        );
      })
      .catch((failure) => {
        if (!abort.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [referenceId, field.type, registryId, query, displayMode]);
  const values = Array.isArray(value)
    ? value.map(String)
    : value
      ? [String(value)]
      : [];
  const known = new Set(choices.map((choice) => choice.id));
  const merged = [
    ...choices,
    ...values
      .filter((current) => !known.has(current))
      .map((current) => ({ id: current, label: current, active: false })),
  ];
  if (disabled)
    return (
      <div className="field-value">
        {values
          .map(
            (current) =>
              choices.find((choice) => choice.id === current)?.label || current,
          )
          .join(", ") || "Не указано"}
      </div>
    );
  return (
    <>
      {field.type === "document" && (
        <input
          aria-label={`Поиск: ${field.label}`}
          type="search"
          placeholder="Найти документ"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <select
        id={id}
        disabled={loading}
        multiple={field.type === "multi_reference"}
        value={field.type === "multi_reference" ? values : values[0] || ""}
        required={field.required}
        onChange={(event) =>
          onChange(
            field.type === "multi_reference"
              ? Array.from(
                  event.target.selectedOptions,
                  (option) => option.value,
                )
              : event.target.value || null,
          )
        }
      >
        {field.type !== "multi_reference" && (
          <option value="">{loading ? "Загрузка…" : "Не указано"}</option>
        )}
        {merged.map((choice) => (
          <option
            key={choice.id}
            value={choice.id}
            disabled={choice.active === false && !values.includes(choice.id)}
          >
            {choice.label}
            {choice.active === false ? " (неактивно)" : ""}
          </option>
        ))}
      </select>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {field.type === "multi_reference" && (
        <small>Для нескольких значений используйте Ctrl / Cmd.</small>
      )}
    </>
  );
}

function FieldControl({
  field,
  value,
  values,
  onChange,
  ...props
}: Omit<Props, "schema" | "onChange"> & {
  field: Field;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const id = useId();
  const writable = canWrite(field, props.mode, props.roles);
  const required =
    field.required ||
    (!!field.required_when &&
      conditionMatches(field.required_when, props.conditionValues || values, props.conditionFields));
  if (
    !canRead(
      field,
      props.mode,
      props.roles,
      props.conditionValues || values,
      props.displayMode,
      props.conditionFields,
    )
  )
    return null;
  if (field.type === "group") {
    const rows = Array.isArray(value) ? (value as Values[]) : [];
    return (
      <fieldset className="repeat-group">
        <legend>
          {field.label}
          {required ? " *" : ""}
        </legend>
        {field.description && <p>{field.description}</p>}
        {rows.map((row, index) => (
          <div className="repeat-row" key={index}>
            <h4>Запись {index + 1}</h4>
            <DynamicForm
              {...props}
              schema={{
                sections: [
                  { key: field.key, title: "", fields: field.children || [] },
                ],
              }}
              values={row}
              conditionValues={props.conditionValues || values}
              onChange={(next) =>
                onChange(
                  rows.map((current, position) =>
                    position === index ? next : current,
                  ),
                )
              }
              mode={writable ? props.mode : "view"}
              displayMode={props.displayMode || (props.mode === "view" ? "viewer" : "editor")}
              prefix={`${props.prefix || ""}${field.key}.${index}.`}
            />
            {writable && (
              <button
                type="button"
                className="danger-link"
                onClick={() =>
                  onChange(rows.filter((_, position) => position !== index))
                }
              >
                Удалить запись {index + 1}
              </button>
            )}
          </div>
        ))}
        {!rows.length && <p className="muted">Записей пока нет</p>}
        {writable && (
          <button
            type="button"
            onClick={() =>
              onChange([
                ...rows,
                initialValues({
                  sections: [
                    { key: "row", title: "", fields: field.children || [] },
                  ],
                }),
              ])
            }
          >
            Добавить: {field.label}
          </button>
        )}
      </fieldset>
    );
  }
  const selectedFiles =
    props.files?.filter(
      (file) =>
        file.field_key === `${props.prefix || ""}${field.key}` ||
        (Array.isArray(value) && value.includes(file.id)),
    ) || [];
  let control;
  if (
    ["reference", "multi_reference", "user", "department", "document"].includes(
      field.type,
    )
  )
    control = (
      <Lookup
        field={{ ...field, required }}
        value={value}
        disabled={!writable}
        onChange={onChange}
        registryId={props.registryId}
        references={props.references}
        id={id}
        displayMode={
          props.displayMode || (props.mode === "view" ? "viewer" : "editor")
        }
      />
    );
  else if (field.type === "file")
    control = (
      <div>
        {selectedFiles.map((file) => (
          <div className="file-line" key={file.id}>
            {props.canDownload === false ? (
              <span>{file.name} · версия {file.version}</span>
            ) : (
            <button
              type="button"
              className="text-button"
              onClick={() =>
                void saveDownload(
                  file.id,
                  file.name,
                  props.displayMode ||
                    (props.mode === "view" ? "viewer" : "editor"),
                ).catch((error) => window.alert(error.message))
              }
            >
              {file.name} · версия {file.version}
            </button>
            )}
          </div>
        ))}
        {writable && props.onFile && (
          <input
            id={id}
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file)
                props.onFile?.(`${props.prefix || ""}${field.key}`, file);
              event.target.value = "";
            }}
          />
        )}
        {!selectedFiles.length && !writable && <span>Файлы не добавлены</span>}
        {writable && !props.onFile && (
          <p className="muted">
            Загрузка станет доступна в карточке документа.
          </p>
        )}
      </div>
    );
  else if (!writable)
    control = (
      <div className="field-value" id={id}>
        {value === null || value === undefined || value === ""
          ? "Не указано"
          : typeof value === "boolean"
            ? value
              ? "Да"
              : "Нет"
            : String(value)}
      </div>
    );
  else if (field.type === "boolean")
    control = (
      <select
        id={id}
        value={value === true ? "true" : value === false ? "false" : ""}
        required={required}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? null : event.target.value === "true",
          )
        }
      >
        <option value="">Не указано</option>
        <option value="true">Да</option>
        <option value="false">Нет</option>
      </select>
    );
  else if (field.type === "textarea")
    control = (
      <textarea
        id={id}
        value={String(value ?? "")}
        required={required}
        rows={4}
        minLength={Number(field.constraints?.min_length) || undefined}
        maxLength={Number(field.constraints?.max_length) || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  else
    control = (
      <input
        id={id}
        type={
          field.type === "integer" || field.type === "decimal"
            ? "number"
            : field.type === "datetime"
              ? "datetime-local"
              : field.type === "date"
                ? "date"
                : "text"
        }
        value={String(value ?? "")}
        required={required}
        step={
          field.type === "decimal"
            ? "any"
            : field.type === "integer"
              ? "1"
              : undefined
        }
        min={field.constraints?.min as number | undefined}
        max={field.constraints?.max as number | undefined}
        minLength={Number(field.constraints?.min_length) || undefined}
        maxLength={Number(field.constraints?.max_length) || undefined}
        pattern={field.constraints?.pattern as string | undefined}
        onChange={(event) =>
          onChange(
            event.target.value === ""
              ? null
              : field.type === "integer"
                ? Number(event.target.value)
                : event.target.value,
          )
        }
      />
    );
  return (
    <div
      className={`form-field ${field.type === "textarea" || field.type === "file" ? "wide" : ""}`}
    >
      <label htmlFor={id}>
        {field.label}
        {required && writable ? " *" : ""}
      </label>
      {control}
      {field.description && <small>{field.description}</small>}
    </div>
  );
}

export function DynamicForm({ schema, values, onChange, ...props }: Props) {
  const navigationId = useId();
  const conditionFields = props.conditionFields || schema.sections.flatMap((section) => section.fields);
  return (
    <div className="dynamic-form">
      {!props.prefix && schema.sections.length > 2 && (
        <nav className="form-navigation" aria-label="Разделы формы">
          {schema.sections.filter(section => section.fields.some(field => canRead(field, props.mode, props.roles, props.conditionValues || values, props.displayMode, conditionFields))).map(section => (
            <a key={section.key} href={`#${navigationId}-${section.key}`}>{section.title}</a>
          ))}
        </nav>
      )}
      {schema.sections.map((section) => {
        const visible = section.fields.filter((field) =>
          canRead(
            field,
            props.mode,
            props.roles,
            props.conditionValues || values,
            props.displayMode,
            conditionFields,
          ),
        );
        return visible.length ? (
          <section className="form-section" key={section.key} id={`${navigationId}-${section.key}`}>
            {section.title && <h3>{section.title}</h3>}
            <div className="form-grid">
              {visible.map((field) => (
                <FieldControl
                  {...props}
                  conditionFields={conditionFields}
                  key={field.key}
                  field={field}
                  values={values}
                  value={values[field.key]}
                  onChange={(value) =>
                    onChange?.({ ...values, [field.key]: value })
                  }
                />
              ))}
            </div>
          </section>
        ) : null;
      })}
    </div>
  );
}
