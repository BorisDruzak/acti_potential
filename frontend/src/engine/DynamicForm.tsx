import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { apiRequest, saveDownload } from "../api/client";
import { displayConfigText } from "./presentation";
import "./Lookup.css";
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
  navigationPlacement?: "side";
  sectionContent?: Record<string, ReactNode>;
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
  const searchRef = useRef<HTMLInputElement>(null);
  const multiple = field.type === "multi_reference";
  const label = displayConfigText(field.label);
  const documentQuery = field.type === "document" ? query : "";
  const values = Array.isArray(value)
    ? value.map(String)
    : value
      ? [String(value)]
      : [];
  const minimum = Math.max(field.required ? 1 : 0, Number(field.constraints?.min_items) || 0);
  const maximum = Math.min(500, Number(field.constraints?.max_items ?? 500));
  const selectionError = values.length < minimum
    ? `Выберите минимум ${minimum}`
    : values.length > maximum
      ? `Выберите не более ${maximum}`
      : "";
  useEffect(() => {
    searchRef.current?.setCustomValidity(multiple && !disabled ? selectionError : "");
  }, [multiple, disabled, selectionError]);
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
      path = `/documents?${new URLSearchParams({ q: documentQuery, page_size: "100", mode: displayMode })}`;
    else if (referenceId)
      path = `/references/${referenceId}/items?mode=${displayMode}`;
    else {
      setChoices([]);
      setLoading(false);
      setError("");
      return;
    }
    setLoading(true);
    setError("");
    setChoices([]);
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
  }, [referenceId, field.type, registryId, documentQuery, displayMode]);
  const known = new Set(choices.map((choice) => choice.id));
  const merged = [
    ...choices,
    ...values
      .filter((current) => !known.has(current))
      .map((current) => ({ id: current, label: current, active: false })),
  ];
  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const visibleChoices = merged.filter((choice) =>
    values.includes(choice.id) || field.type === "document" ||
    choice.label.toLocaleLowerCase("ru").includes(normalizedQuery),
  );
  const matches = choices.some((choice) =>
    choice.label.toLocaleLowerCase("ru").includes(normalizedQuery),
  );
  const status = loading ? "Загрузка…" : error ? "" : !choices.length
    ? "Нет доступных значений"
    : normalizedQuery && !matches && field.type !== "document" ? "Ничего не найдено" : "";
  if (disabled)
    return (
      <div className="field-value" id={id}>
        {values
          .map(
            (current) =>
              choices.find((choice) => choice.id === current)?.label || current,
          )
          .join(", ") || "Не указано"}
      </div>
    );
  return (
    <div className="lookup-control">
      <input
        ref={searchRef}
        id={multiple ? id : `${id}-search`}
        aria-label={`Поиск: ${label}`}
        aria-describedby={multiple ? `${id}-selection` : undefined}
        aria-required={multiple && minimum > 0 || undefined}
        type="search"
        placeholder={field.type === "document" ? "Найти документ" : "Поиск по имени или названию"}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {multiple ? (
        <>
          <div className="lookup-options" role="group" aria-label={label} aria-busy={loading}>
            {visibleChoices.map((choice) => {
              const selected = values.includes(choice.id);
              const unavailable = !selected && (choice.active === false || values.length >= maximum);
              return (
                <label className={`lookup-option${selected ? " lookup-option-selected" : ""}`} key={choice.id}>
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={loading || unavailable}
                    onChange={() => {
                      if (loading || unavailable) return;
                      onChange(selected ? values.filter((current) => current !== choice.id) : [...values, choice.id]);
                    }}
                  />
                  <span>{choice.label}{choice.active === false ? " (неактивно)" : ""}</span>
                </label>
              );
            })}
          </div>
          <small id={`${id}-selection`}>
            Выбрано: {values.length}. Нажмите на имя, чтобы выбрать или убрать значение.
            {minimum > 0 ? ` Минимум: ${minimum}.` : ""}
            {maximum < 500 ? ` Максимум: ${maximum}.` : ""}
          </small>
        </>
      ) : <select
        id={id}
        disabled={loading}
        value={values[0] || ""}
        required={field.required}
        onChange={(event) =>
          onChange(event.target.value || null)
        }
      >
        <option value="">{loading ? "Загрузка…" : "Не указано"}</option>
        {visibleChoices.map((choice) => (
          <option
            key={choice.id}
            value={choice.id}
            disabled={choice.active === false && !values.includes(choice.id)}
          >
            {choice.label}
            {choice.active === false ? " (неактивно)" : ""}
          </option>
        ))}
      </select>}
      <div className="lookup-status" role="status" aria-label={`Состояние: ${label}`}>{status}</div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
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
          {displayConfigText(field.label)}
          {required ? " *" : ""}
        </legend>
        {field.description && <p>{displayConfigText(field.description)}</p>}
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
            Добавить: {displayConfigText(field.label)}
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
          ? field.number_series_id ? "Номер присваивается из резерва после создания карточки" : "Не указано"
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
        {displayConfigText(field.label)}
        {required && writable ? " *" : ""}
      </label>
      {control}
      {field.description && <small>{displayConfigText(field.description)}</small>}
    </div>
  );
}

export function DynamicForm({ schema, values, onChange, ...props }: Props) {
  const navigationId = useId();
  const conditionFields = props.conditionFields || schema.sections.flatMap((section) => section.fields);
  const hasNavigation = !props.prefix && schema.sections.length > 2;
  const sideNavigation = hasNavigation && props.navigationPlacement === "side";
  const sections = schema.sections.map((section) => {
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
    const extra = props.sectionContent?.[section.key];
    return visible.length || extra ? (
      <section className="form-section" key={section.key} id={`${navigationId}-${section.key}`}>
        {section.title && <h3>{displayConfigText(section.title)}</h3>}
        {extra}
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
  });
  return (
    <div className={`dynamic-form${sideNavigation ? " dynamic-form-side-navigation" : ""}`}>
      {hasNavigation && (
        <nav className="form-navigation" aria-label="Разделы формы">
          {schema.sections.filter(section => props.sectionContent?.[section.key] || section.fields.some(field => canRead(field, props.mode, props.roles, props.conditionValues || values, props.displayMode, conditionFields))).map(section => (
            <a key={section.key} href={`#${navigationId}-${section.key}`}>{displayConfigText(section.title)}</a>
          ))}
        </nav>
      )}
      {sideNavigation ? <div className="form-sections">{sections}</div> : sections}
    </div>
  );
}
