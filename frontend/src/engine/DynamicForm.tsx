import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { apiRequest, saveDownload } from "../api/client";
import { displayConfigText } from "./presentation";
import "./Lookup.css";
import { LookupCombobox, type LookupChoice } from "./LookupCombobox";
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
  referenceScope?: "registry" | "available";
  files?: FileVersion[];
  canDownload?: boolean;
  onFile?: (field: string, file: File) => void;
  prefix?: string;
  conditionValues?: Values;
  conditionFields?: Field[];
  displayMode?: "editor" | "viewer";
  navigationPlacement?: "side";
  sectionContent?: Record<string, ReactNode>;
  fieldContent?: Record<string, ReactNode | ((id: string) => ReactNode)>;
  onCreateReference?: (field: Field, label: string) => Promise<{ item: ReferenceItem; created: boolean }>;
};
type Choice = LookupChoice;
const participantLabel = (item: ReferenceItem) => [item.label, ...[item.metadata?.position, item.metadata?.department].filter((value): value is string => typeof value === "string" && !!value.trim())].join(" · ");
const participantBooks = new Set(["executors", "responsible_executors", "signers", "approvers"]);
const cleanName = (name: string) => name.trim().replace(/\s+/gu, " ");
const normalizedName = (name: string) => cleanName(name).toLocaleLowerCase("ru");
function Lookup({
  field,
  value,
  disabled,
  onChange,
  registryId,
  references = [],
  referenceScope = "registry",
  id,
  displayMode = "editor",
  onCreateReference,
}: {
  field: Field;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  registryId?: string;
  references?: ReferenceSet[];
  referenceScope?: "registry" | "available";
  id: string;
  displayMode?: "editor" | "viewer";
  onCreateReference?: Props["onCreateReference"];
}) {
  const [choices, setChoices] = useState<Choice[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [creationError, setCreationError] = useState("");
  const [creationMessage, setCreationMessage] = useState("");
  const creationGeneration = useRef(0);
  const creationPending = useRef(false);
  const currentSelection = useRef({ value, onChange });
  currentSelection.current = { value, onChange };
  const multiple = field.type === "multi_reference";
  const label = displayConfigText(field.label);
  const documentQuery = field.type === "document" ? query : "";
  const values = Array.isArray(value)
    ? value.map(String)
    : value
      ? [String(value)]
      : [];
  const minimum = Math.max(field.required ? 1 : 0, multiple ? Number(field.constraints?.min_items) || 0 : 0);
  const maximum = multiple ? Math.min(500, Number(field.constraints?.max_items ?? 500)) : 1;
  const selectionError = values.length < minimum
    ? `Выберите минимум ${minimum}`
    : values.length > maximum
      ? `Выберите не более ${maximum}`
      : "";
  const matchingReferences = references.filter(
    (reference) => reference.code === field.reference_code,
  );
  const explicitReference = references.find(reference => reference.id === field.reference_set_id);
  const explicitInScope = referenceScope === "available" && !registryId
    ? !!explicitReference
    : !explicitReference?.registry_id || explicitReference.registry_id === registryId;
  const referenceId =
    (field.reference_set_id && explicitInScope ? field.reference_set_id : undefined) || (!field.reference_set_id ?
    (
      matchingReferences.find(
        (reference) => reference.registry_id === registryId,
      ) || matchingReferences.find((reference) => !reference.registry_id)
    )?.id : undefined);
  const aggregateReferences = referenceScope === "available" && !registryId && !!field.reference_code && !field.reference_set_id;
  const referenceIds = aggregateReferences
    ? [...new Set(matchingReferences.map(reference => reference.id))]
    : referenceId ? [referenceId] : [];
  const referenceIdsKey = JSON.stringify(referenceIds.map(reference => [reference, aggregateReferences ? references.find(book => book.id === reference)?.name : ""]));
  const referenceBook = references.find(reference => reference.id === referenceId);
  const canCreate = !disabled && !!onCreateReference && !!registryId &&
    ["reference", "multi_reference"].includes(field.type) &&
    referenceBook?.registry_id === registryId && participantBooks.has(referenceBook.code);
  useEffect(() => {
    creationGeneration.current += 1;
    creationPending.current = false;
    setCreating(false);
    setCreationError("");
    setCreationMessage("");
    setQuery("");
    return () => { creationGeneration.current += 1; };
  }, [registryId, referenceIdsKey, disabled, onCreateReference]);
  useEffect(() => {
    const abort = new AbortController();
    const paths = field.type === "user" ? ["/users/options"]
      : field.type === "document" ? [`/documents?${new URLSearchParams({ q: documentQuery, page_size: "100", mode: displayMode })}`]
      : referenceIds.map(reference => `/references/${reference}/items?mode=${displayMode}`);
    if (!paths.length) {
      setChoices([]);
      setLoading(false);
      setError("");
      return;
    }
    setLoading(true);
    setError("");
    setChoices([]);
    Promise.all(paths.map(path => apiRequest<
      | ReferenceItem[]
      | { id: string; display_name: string }[]
      | { items: { id: string; title?: string; reg_number?: string }[] }
    >(path, { signal: abort.signal })))
      .then((responses) => {
        if (abort.signal.aborted) return;
        const allChoices = responses.flatMap((data, index) => (
          Array.isArray(data)
            ? data.map((item) => ({
                id: item.id,
                label: "label" in item ? participantLabel(item) : item.display_name,
                name: "label" in item ? item.label : item.display_name,
                active: !("active" in item && item.active === false) && !("selectable" in item && item.selectable === false),
              }))
            : data.items.map((item) => ({
                id: item.id,
                label:
                  [item.reg_number, item.title].filter(Boolean).join(" · ") ||
                  item.id,
              }))
        ).map(choice => ({
          ...choice,
          label: aggregateReferences && referenceIds.length > 1
            ? `${choice.label} · ${references.find(book => book.id === referenceIds[index])!.name}`
            : choice.label,
        })));
        // Preserve each persisted UUID even when separate books use the same label.
        setChoices([...new Map(allChoices.map(choice => [choice.id, choice])).values()]);
      })
      .catch((failure) => {
        if (!abort.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!abort.signal.aborted) setLoading(false);
      });
    return () => abort.abort();
  }, [referenceIdsKey, field.type, registryId, documentQuery, displayMode, disabled]);
  const known = new Set(choices.map((choice) => choice.id));
  const merged = [
    ...choices,
    ...values
      .filter((current) => !known.has(current))
      .map((current) => ({ id: current, label: current, active: false })),
  ];
  const normalizedQuery = normalizedName(query);
  const visibleChoices = merged.filter((choice) =>
    field.type === "document" ||
    normalizedName(choice.label).includes(normalizedQuery),
  );
  function selectChoice(choiceId: string) {
    if (disabled || loading || creationPending.current || multiple && values.length >= maximum) return;
    const choice = choices.find(item => item.id === choiceId);
    if (!choice || choice.active === false || values.includes(choiceId)) return;
    onChange(multiple ? [...values, choiceId] : choiceId);
    setQuery("");
    setCreationError("");
    setCreationMessage("");
  }
  useEffect(() => {
    if (normalizedQuery.length < 3 || disabled || loading || error || creating || multiple && values.length >= maximum) return;
    const candidates = choices.filter(choice => choice.active !== false && !values.includes(choice.id) && normalizedName(choice.label).includes(normalizedQuery));
    if (candidates.length === 1) selectChoice(candidates[0].id);
  }, [query, choices, value, disabled, loading, error, creating]);
  const matches = choices.some((choice) =>
    normalizedName(choice.label).includes(normalizedQuery),
  );
  const inactiveMatch = !!normalizedQuery && choices.some(choice =>
    choice.active === false && normalizedName(choice.name || choice.label) === normalizedQuery);
  const selectableMatch = choices.some(choice => choice.active !== false && normalizedName(choice.label).includes(normalizedQuery));
  const offerCreation = canCreate && !!normalizedQuery && !loading && !error && !selectableMatch && !inactiveMatch;
  async function createReference() {
    if (!offerCreation || !onCreateReference || creationPending.current || multiple && values.length >= maximum) return;
    const generation = creationGeneration.current;
    creationPending.current = true;
    setCreating(true);
    setCreationError("");
    setCreationMessage("");
    try {
      const result = await onCreateReference(field, cleanName(query));
      if (generation !== creationGeneration.current) return;
      if (!result.item.active || result.item.selectable === false)
        throw new Error("Участник неактивен. Обратитесь к администратору справочника.");
      const item = { id: result.item.id, label: participantLabel(result.item), name: result.item.label, active: true };
      setChoices(current => [...current.filter(choice => choice.id !== item.id), item]);
      const current = currentSelection.current;
      const selected = Array.isArray(current.value) ? current.value.map(String) : current.value ? [String(current.value)] : [];
      current.onChange(multiple ? [...new Set([...selected, item.id])] : item.id);
      setQuery("");
      setCreationMessage(result.created ? `Участник «${item.label}» создан и выбран.` : `Участник «${item.label}» уже существует и выбран.`);
    } catch (failure) {
      if (generation === creationGeneration.current)
        setCreationError(failure instanceof Error ? failure.message : "Не удалось создать участника.");
    } finally {
      if (generation === creationGeneration.current) {
        creationPending.current = false;
        setCreating(false);
      }
    }
  }
  const status = creating ? "Создание участника…" : creationMessage || (canCreate && inactiveMatch ? "Участник уже есть в справочнике, но неактивен. Обратитесь к администратору справочника." : loading ? "Загрузка…" : error ? "" : !choices.length
    ? "Нет доступных значений"
    : normalizedQuery && !matches && field.type !== "document" ? "Ничего не найдено" : "");
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
      <LookupCombobox id={id} label={label} choices={visibleChoices}
        selected={values.map(current => merged.find(choice => choice.id === current)!)}
        multiple={multiple} query={query} onQuery={next => { if (!creationPending.current) { setQuery(next); setCreationError(""); setCreationMessage(""); } }}
        onSelect={selectChoice} onRemove={current => { if (!creationPending.current) { setQuery(""); onChange(multiple ? values.filter(value => value !== current) : null); } }}
        disabled={creating} busy={loading || !!error || creating} atLimit={multiple && values.length >= maximum}
        required={minimum > 0} validation={selectionError}
        placeholder={field.type === "document" ? "Найти документ" : "Поиск по имени или названию"}>
      {offerCreation && <div className="lookup-create">
        <button type="button" className="lookup-create-button" disabled={creating || multiple && values.length >= maximum} onClick={() => void createReference()}>
          Создать «{cleanName(query)}»
        </button>
        <small>Запись сразу сохраняется в справочник реестра.</small>
      </div>}
      </LookupCombobox>
      <div className="lookup-status" role="status" aria-label={`Состояние: ${label}`}>{status}</div>
      {(error || creationError) && (
        <p className="error" role="alert">
          {error || creationError}
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
  if (!props.prefix && props.fieldContent?.[field.key] !== undefined) {
    const custom = props.fieldContent[field.key];
    control = typeof custom === "function" ? custom(id) : custom;
  }
  else if (
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
        referenceScope={props.referenceScope}
        onCreateReference={props.prefix ? undefined : props.onCreateReference}
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
  const currentValues = useRef(values);
  currentValues.current = values;
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
              onChange={(value) => {
                const next = { ...currentValues.current, [field.key]: value };
                currentValues.current = next;
                onChange?.(next);
              }}
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
