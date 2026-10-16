import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "./DynamicForm";
import type { NumberSeries } from "./numbering";
import {
  fieldTypes,
  initialValues,
  moveItem,
  type Condition,
  type Field,
  type FieldType,
  type FormSchema,
  type ReferenceSet,
  type Registry,
  type SchemaVersion,
  type Values,
} from "./schema";
import { useAction, useData } from "./useData";

function validDefault(field: Field): boolean {
  const value = field.default;
  if (value === undefined || value === null) return true;
  switch (field.type) {
    case "integer":
      return typeof value === "number" && Number.isSafeInteger(value);
    case "decimal":
      return (
        (typeof value === "number" || typeof value === "string") &&
        String(value).trim() !== "" && Number.isFinite(Number(value))
      );
    case "boolean":
      return typeof value === "boolean";
    case "multi_reference":
      return Array.isArray(value) && value.every((item) => typeof item === "string");
    case "group":
      return Array.isArray(value) && value.every(
        (item) => !!item && typeof item === "object" && !Array.isArray(item),
      );
    case "file":
      return Array.isArray(value) && value.length === 0;
    default:
      return typeof value === "string";
  }
}

function defaultTextFor(field: Field): string {
  return field.default === undefined
    ? ""
    : typeof field.default === "string"
      ? field.default
      : JSON.stringify(field.default);
}

function changeFieldType(field: Field, type: FieldType): Field {
  const next = { ...field, type };
  if (!["reference", "multi_reference", "department"].includes(type)) {
    delete next.reference_set_id;
    delete next.reference_code;
  }
  if (type !== "group") delete next.children;
  const systemTypes: Record<string, FieldType> = {
    reg_number: "text", reg_date: "date", title: "text", status: "text",
  };
  if (next.system && systemTypes[next.system] !== type) delete next.system;
  if (next.system !== "reg_number") delete next.number_series_id;
  const allowed = type === "group"
    ? ["min_items", "max_items"]
    : ["integer", "decimal"].includes(type)
      ? ["min", "max"]
      : ["text", "textarea"].includes(type)
        ? ["min_length", "max_length", "choices", "pattern"]
        : [];
  next.constraints = Object.fromEntries(
    Object.entries(next.constraints || {}).filter(([key]) => allowed.includes(key)),
  );
  if (!validDefault(next)) delete next.default;
  return next;
}

function ConditionEditor({
  title,
  value,
  fields,
  onChange,
}: {
  title: string;
  value?: Condition | null;
  fields: Field[];
  onChange: (value: Condition | null) => void;
}) {
  const [literal, setLiteral] = useState(
    value?.value === undefined ? "" : JSON.stringify(value.value),
  );
  return (
    <fieldset>
      <legend>{title}</legend>
      <label>
        Поле условия
        <select
          value={value?.field || ""}
          onChange={(event) => {
            const dependency = fields.find(field => field.key === event.target.value);
            const comparison = dependency?.type === "boolean" ? true : dependency?.type === "integer" ? 0 : dependency?.type === "decimal" ? "0" : dependency?.type === "multi_reference" ? [] : "";
            setLiteral(event.target.value ? JSON.stringify(comparison) : "");
            onChange(
              event.target.value
                ? {
                    field: event.target.value,
                    op: value?.op || "eq",
                    value: comparison,
                  }
                : null,
            );
          }}
        >
          <option value="">Без условия</option>
          {fields.map((field) => (
            <option key={field.key} value={field.key}>
              {field.label} ({field.key})
            </option>
          ))}
        </select>
      </label>
      {value && (
        <>
          <label>
            Оператор
            <select
              value={value.op}
              onChange={(event) => {
                const op = event.target.value as Condition["op"];
                onChange(["present", "absent"].includes(op)
                  ? {field: value.field, op}
                  : {...value, op, value: value.value ?? true});
                if (value.value === undefined) setLiteral("true");
              }}
            >
              <option value="eq">Равно</option>
              <option value="ne">Не равно</option>
              <option value="in">В списке</option>
              <option value="not_in">Вне списка</option>
              <option value="present">Заполнено</option>
              <option value="absent">Не заполнено</option>
            </select>
          </label>
          {!["present", "absent"].includes(value.op) && (
            <label>
              Значение
              <input
                value={literal}
                onChange={(event) => {
                  setLiteral(event.target.value);
                  let parsed: unknown = event.target.value;
                  const type = fields.find(field => field.key === value.field)?.type;
                  const membership = ["in", "not_in"].includes(value.op);
                  if (membership || ["integer", "boolean", "multi_reference"].includes(type || "") || event.target.value.startsWith('"')) {
                    try { parsed = JSON.parse(event.target.value); } catch { /* Server validates typed operands. */ }
                  }
                  onChange({ ...value, value: parsed });
                }}
              />
              <small>{fields.find(field => field.key === value.field)?.type === "decimal"
                ? 'Десятичные значения задавайте строками: "1.20" или ["1.20","2.50"], чтобы сохранить точность.'
                : "Текст, число, true / false или список [1,2]."}</small>
            </label>
          )}
        </>
      )}
    </fieldset>
  );
}
function PropertyEditor({
  field,
  fields,
  references,
  series,
  onChange,
  child,
}: {
  field: Field;
  fields: Field[];
  references: ReferenceSet[];
  series: NumberSeries[];
  onChange: (field: Field) => void;
  child?: boolean;
}) {
  const change = (patch: Partial<Field>) => onChange({ ...field, ...patch });
  const constraints = field.constraints || {};
  const [defaultText, setDefaultText] = useState(() => defaultTextFor(field));
  return (
    <div className="properties">
      <h3>Свойства поля</h3>
      <label>
        Название поля
        <input
          value={field.label}
          required
          onChange={(event) => change({ label: event.target.value })}
        />
      </label>
      <label>
        Системный ключ
        <input
          aria-label="Системный ключ"
          value={field.key}
          pattern="[a-z][a-z0-9_]*"
          onChange={(event) => change({ key: event.target.value })}
        />
        <small>После публикации смысл ключа сохраняется.</small>
      </label>
      <label>
        Тип поля
        <select
          value={field.type}
          onChange={(event) => {
            const next = changeFieldType(field, event.target.value as FieldType);
            setDefaultText(defaultTextFor(next));
            onChange(next);
          }}
        >
          {fieldTypes
            .filter(([type]) => !child || type !== "group")
            .map(([type, label]) => (
              <option key={type} value={type}>
                {label}
              </option>
            ))}
        </select>
      </label>
      <label>
        Описание
        <textarea
          rows={2}
          value={field.description || ""}
          onChange={(event) => change({ description: event.target.value })}
        />
      </label>
      <label>
        Значение по умолчанию
        <input
          value={defaultText}
          aria-invalid={!validDefault(field)}
          onChange={(event) => {
            setDefaultText(event.target.value);
            let parsed: unknown = event.target.value;
            if (!event.target.value) parsed = undefined;
            else if (
              [
                "integer",
                "boolean",
                "multi_reference",
                "group",
                "file",
              ].includes(field.type)
            ) {
              try {
                parsed = JSON.parse(event.target.value);
              } catch {
                parsed = event.target.value;
              }
            }
            change({ default: parsed });
          }}
        />
      </label>
      {!validDefault(field) && (
        <p className="error" role="alert">
          Значение по умолчанию не соответствует типу поля.
        </p>
      )}
      {["reference", "multi_reference", "department"].includes(field.type) && (
        <label>
          Справочник
          <select
            value={field.reference_set_id || ""}
            onChange={(event) =>
              change({
                reference_set_id: event.target.value || null,
                reference_code: undefined,
              })
            }
          >
            <option value="">Выберите справочник</option>
            {references.map((reference) => (
              <option key={reference.id} value={reference.id}>
                {reference.name}
                {reference.registry_id ? " · реестр" : " · общий"}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Системный реквизит
        <select
          value={field.system || ""}
          onChange={(event) => change({ system: event.target.value || null, ...(event.target.value !== "reg_number" ? {number_series_id: null} : {}) })}
        >
          <option value="">Дополнительное поле</option>
          {["reg_number", "reg_date", "title", "status"].map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      </label>
      {field.system === "reg_number" && <label>
        Серия регистрационных номеров
        <select value={field.number_series_id || ""} onChange={event => change({number_series_id: event.target.value || null})}>
          <option value="">Обычный ввод номера</option>
          {series.map(item => <option key={item.id} value={item.id}>{item.name} · {item.code}{item.state === "blocked" ? " · заблокирована" : ""}</option>)}
        </select>
        <small>Сначала назначьте тип реестру. Для подключённой серии номер присваивается из резерва после создания карточки.</small>
      </label>}
      <div className="check-list">
        {[
          ["required", "Обязательное"],
          ["editable", "Редактируемое"],
          ["filterable", "Доступно в фильтрах"],
          ["exportable", "Доступно в экспорте"],
            ["searchable", "Индексировать значение для поиска"],
        ].map(([key, label]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={
                key === "editable" ? field.editable !== false : key === "searchable" ? (field.searchable ?? ["text", "textarea", "date", "datetime", "group"].includes(field.type)) : !!field[key as keyof Field]
              }
              onChange={(event) => change({ [key]: event.target.checked })}
            />
            {label}
          </label>
        ))}
      </div>
      <fieldset>
        <legend>Ограничения</legend>
        {["text", "textarea"].includes(field.type) && <label>
          Регулярное выражение
          <input aria-label="Регулярное выражение" value={String(constraints.pattern || "")} onChange={event => {
            const next = { ...constraints };
            if (event.target.value) next.pattern = event.target.value;
            else delete next.pattern;
            change({ constraints: next });
          }} />
          <small>До 256 символов; без групп и альтернатив. Не более одного переменного повторения, фиксированные повторы до 1000. Шаблон проверяет сервер.</small>
        </label>}
        {(field.type === "group"
          ? ["min_items", "max_items"]
          : ["integer", "decimal"].includes(field.type)
            ? ["min", "max"]
            : ["text", "textarea"].includes(field.type)
              ? ["min_length", "max_length"]
              : []
        ).map((key) => (
          <label key={key}>
            {
              (
                {
                  min: "Минимум",
                  max: "Максимум",
                  min_length: "Минимальная длина",
                  max_length: "Максимальная длина",
                  min_items: "Минимум записей",
                  max_items: "Максимум записей",
                } as Record<string, string>
              )[key]
            }
            <input
              type="number"
              step={field.type === "decimal" ? "any" : "1"}
              value={String(constraints[key] ?? "")}
              onChange={(event) => {
                const next = { ...constraints };
                if (event.target.value === "") delete next[key];
                else next[key] = field.type === "decimal" ? event.target.value : Number(event.target.value);
                change({ constraints: next });
              }}
            />
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Режимы отображения</legend>
        <div className="check-list">
          {[
            ["editor", "Редактор"],
            ["viewer", "Читатель"],
          ].map(([mode, label]) => (
            <label key={mode}>
              <input
                type="checkbox"
                disabled={field.modes?.length === 1 && field.modes.includes(mode)}
                checked={!field.modes?.length || field.modes.includes(mode)}
                onChange={(event) => {
                  const modes = field.modes?.length
                    ? field.modes
                    : ["editor", "viewer"];
                  change({
                    modes: event.target.checked
                      ? [...modes, mode]
                      : modes.filter((item) => item !== mode),
                  });
                }}
              />
              {label}
            </label>
          ))}
        </div>
        <small>Выберите хотя бы один режим отображения.</small>
      </fieldset>
      {(["read_roles", "write_roles"] as const).map((key) => (
        <fieldset key={key}>
          <legend>
            {key === "read_roles" ? "Роли для просмотра" : "Роли для изменения"}
          </legend>
          <div className="check-list">
            {["admin", "editor", "viewer", "user"].map((role) => (
              <label key={role}>
                <input
                  type="checkbox"
                  checked={field[key]?.includes(role) || false}
                  onChange={(event) =>
                    change({
                      [key]: event.target.checked
                        ? [...(field[key] || []), role]
                        : field[key]?.filter((item) => item !== role),
                    })
                  }
                />
                {role}
              </label>
            ))}
          </div>
          <small>Без выбора — без дополнительного ограничения.</small>
        </fieldset>
      ))}
      <ConditionEditor
        title="Условие видимости"
        value={field.visible_when}
        fields={fields.filter((current) => current.key !== field.key)}
        onChange={(value) => change({ visible_when: value })}
      />
      <ConditionEditor
        title="Условие обязательности"
        value={field.required_when}
        fields={fields.filter((current) => current.key !== field.key)}
        onChange={(value) => change({ required_when: value })}
      />
    </div>
  );
}

export function SchemaBuilder({
  version,
  references,
  onSaved,
}: {
  version: SchemaVersion;
  references: ReferenceSet[];
  onSaved: () => void;
}) {
  const [schema, setSchema] = useState<FormSchema>(() =>
    structuredClone(version.schema),
  );
  const [selection, setSelection] = useState<{
    section: number;
    field: number;
    child?: number;
  } | null>(null);
  const [addType, setAddType] = useState<FieldType>("text");
  const [preview, setPreview] = useState<"editor" | "viewer" | null>(null);
  const [previewValues, setPreviewValues] = useState<Values>(() =>
    initialValues(version.schema),
  );
  const action = useAction();
  const numberSeries = useData<NumberSeries[]>("/number-series");
  const registries = useData<Registry[]>("/registries");
  const compatibleSeries = (Array.isArray(numberSeries.data) ? numberSeries.data : []).filter(item =>
    (item.document_type_ids.length ? item.document_type_ids.includes(version.document_type_id) : Array.isArray(registries.data) && registries.data.some(registry => registry.id === item.registry_id && registry.document_type_ids.includes(version.document_type_id))),
  );
  const published = version.state === "published";
  const allFields = schema.sections.flatMap((section) => section.fields);
  const selectedParent = selection
    ? schema.sections[selection.section]?.fields[selection.field]
    : null;
  const selected =
    selection?.child !== undefined
      ? selectedParent?.children?.[selection.child]
      : selectedParent;
  function updateFields(
    sectionIndex: number,
    transform: (fields: Field[]) => Field[],
  ) {
    setSchema((current) => ({
      ...current,
      sections: current.sections.map((section, index) =>
        index === sectionIndex
          ? { ...section, fields: transform(section.fields) }
          : section,
      ),
    }));
  }
  function updateField(next: Field) {
    if (selection)
      updateFields(selection.section, (fields) =>
        fields.map((field, index) =>
          index !== selection.field
            ? field
            : selection.child === undefined
              ? next
              : {
                  ...field,
                  children: field.children?.map((child, index) =>
                    index === selection.child ? next : child,
                  ),
                },
        ),
      );
  }
  function newField(type: FieldType): Field {
    return {
      key: "field_" + crypto.randomUUID().replaceAll("-", "").slice(0, 10),
      label: fieldTypes.find(([key]) => key === type)![1],
      type,
      editable: true,
    };
  }
  async function save(publish = false) {
    for (const field of allFields.flatMap((field) => [field, ...(field.children || [])])) {
      if (!validDefault(field)) {
        throw new Error(`${field.label}: значение по умолчанию не соответствует типу поля.`);
      }
    }
    const payload = structuredClone(schema);
    if (payload.list_view) {
      payload.list_view.columns = payload.list_view.columns.filter(column => allFields.some(field => field.key === column.key));
      if (!payload.list_view.columns.length) delete payload.list_view;
    }
    await action.request("/form-schemas/" + version.id, "PUT", { schema: payload });
    if (publish)
      await action.request(
        "/form-schemas/" + version.id + "/publish",
        "POST",
        {},
      );
    onSaved();
  }
  return (
    <section className="schema-builder">
      <div className="toolbar">
        <div>
          <h2>Конструктор формы</h2>
          <p>
            Версия {version.version} ·{" "}
            {published ? "Опубликована, только чтение" : "Черновик"}
          </p>
        </div>
        <div className="button-row">
          <button
            onClick={() => {
              setPreviewValues(initialValues(schema));
              setPreview("editor");
            }}
          >
            Как редактор
          </button>
          <button onClick={() => setPreview("viewer")}>Как читатель</button>
          {!published && (
            <>
              <button
                disabled={action.busy}
                onClick={() => void action.run(() => save())}
              >
                Сохранить черновик
              </button>
              <button
                className="primary"
                disabled={action.busy}
                onClick={() =>
                  void action.run(() => save(true), "Форма опубликована")
                }
              >
                Опубликовать
              </button>
            </>
          )}
        </div>
      </div>
      <Feedback {...action} />
      {(
        <label>
          {published ? "Свойства опубликованного поля" : "Перейти к полю"}
          <select
            value={selection
              ? `${selection.section}:${selection.field}${selection.child === undefined ? "" : ":" + selection.child}`
              : ""}
            onChange={(event) => {
              if (!event.target.value) return setSelection(null);
              const [section, field, child] = event.target.value.split(":").map(Number);
              setSelection({ section, field, ...(child === undefined ? {} : { child }) });
            }}
          >
            <option value="">Выберите поле</option>
            {schema.sections.flatMap((section, sectionIndex) =>
              section.fields.flatMap((field, fieldIndex) => [
                <option key={`${sectionIndex}:${fieldIndex}`} value={`${sectionIndex}:${fieldIndex}`}>
                  {section.title} · {field.label}
                </option>,
                ...(field.children || []).map((child, childIndex) => (
                  <option key={`${sectionIndex}:${fieldIndex}:${childIndex}`} value={`${sectionIndex}:${fieldIndex}:${childIndex}`}>
                    {field.label} · {child.label}
                  </option>
                )),
              ]),
            )}
          </select>
        </label>
      )}
      {preview && (
        <div className="panel preview">
          <div className="toolbar">
            <h3>
              Предпросмотр · {preview === "editor" ? "редактор" : "читатель"}
            </h3>
            <button onClick={() => setPreview(null)}>
              Закрыть предпросмотр
            </button>
          </div>
          <DynamicForm
            schema={schema}
            values={previewValues}
            onChange={setPreviewValues}
            mode={preview === "editor" ? "edit" : "view"}
            roles={[preview]}
            references={references}
          />
        </div>
      )}
      <details className="panel">
        <summary>Представление списка документов</summary>
        <fieldset disabled={published || action.busy}>
          <p>Выберите колонки, задайте заголовки и порядок. Права на значения проверяются для каждой карточки.</p>
          <div className="check-list">{allFields.map(field => <label key={field.key}>
            <input type="checkbox" checked={!!schema.list_view?.columns.some(column => column.key === field.key)} onChange={event => setSchema(current => ({ ...current, list_view: { columns: event.target.checked ? [...(current.list_view?.columns || []), {key: field.key, label: field.label}] : (current.list_view?.columns || []).filter(column => column.key !== field.key) } }))} />{field.label}
          </label>)}</div>
          {schema.list_view?.columns.map((column, index) => <div className="button-row" key={column.key}>
            <label>Заголовок: {column.key}<input value={column.label} onChange={event => setSchema(current => ({...current, list_view: { columns: (current.list_view?.columns || []).map(item => item.key === column.key ? {...item, label: event.target.value} : item) }}))}/></label>
            <button aria-label={`Колонка выше: ${column.label}`} disabled={index === 0} onClick={() => setSchema(current => ({...current, list_view: { columns: moveItem(current.list_view?.columns || [], index, -1) }}))}>↑</button>
            <button aria-label={`Колонка ниже: ${column.label}`} disabled={index === schema.list_view!.columns.length - 1} onClick={() => setSchema(current => ({...current, list_view: { columns: moveItem(current.list_view?.columns || [], index, 1) }}))}>↓</button>
          </div>)}
        </fieldset>
      </details>
      <div className="builder-grid">
        <div className="builder-canvas">
          <nav className="form-navigation" aria-label="Разделы конструктора">{schema.sections.map(section => <a key={section.key} href={`#builder-${section.key}`}>{section.title}</a>)}</nav>
          <fieldset disabled={published || action.busy}>
            <div className="toolbar">
              <label>
                Добавляемый тип
                <select
                  value={addType}
                  onChange={(event) =>
                    setAddType(event.target.value as FieldType)
                  }
                >
                  {fieldTypes.map(([type, label]) => (
                    <option key={type} value={type}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() =>
                  setSchema((current) => ({
                    ...current,
                    sections: [
                      ...current.sections,
                      {
                        key: "section_" + crypto.randomUUID().slice(0, 8),
                        title: "Новый раздел",
                        fields: [],
                      },
                    ],
                  }))
                }
              >
                + Раздел
              </button>
            </div>
            {!schema.sections.length && (
              <div className="empty-state">
                Добавьте раздел, затем поля формы.
              </div>
            )}
            {schema.sections.map((section, sectionIndex) => (
              <section className="builder-section" key={section.key} id={`builder-${section.key}`}>
                <div className="toolbar">
                  <label>
                    Название раздела
                    <input
                      value={section.title}
                      onChange={(event) =>
                        setSchema((current) => ({
                          ...current,
                          sections: current.sections.map((item, index) =>
                            index === sectionIndex
                              ? { ...item, title: event.target.value }
                              : item,
                          ),
                        }))
                      }
                    />
                  </label>
                  <div className="button-row">
                    <button
                      aria-label={"Раздел выше: " + section.title}
                      disabled={sectionIndex === 0}
                      onClick={() => {
                        setSchema((current) => ({
                          ...current,
                          sections: moveItem(
                            current.sections,
                            sectionIndex,
                            -1,
                          ),
                        }));
                        setSelection(null);
                      }}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={"Раздел ниже: " + section.title}
                      disabled={sectionIndex === schema.sections.length - 1}
                      onClick={() => {
                        setSchema((current) => ({
                          ...current,
                          sections: moveItem(current.sections, sectionIndex, 1),
                        }));
                        setSelection(null);
                      }}
                    >
                      ↓
                    </button>
                    <button
                      className="danger-link"
                      onClick={() => {
                        setSchema((current) => ({
                          ...current,
                          sections: current.sections.filter(
                            (_, index) => index !== sectionIndex,
                          ),
                        }));
                        setSelection(null);
                      }}
                    >
                      Удалить раздел
                    </button>
                  </div>
                </div>
                {section.fields.map((field, fieldIndex) => (
                  <div className="builder-field" key={fieldIndex}>
                    <div
                      className={
                        "builder-field-row " +
                        (selection?.section === sectionIndex &&
                        selection.field === fieldIndex &&
                        selection.child === undefined
                          ? "selected"
                          : "")
                      }
                    >
                      <button
                        className="field-select"
                        onClick={() =>
                          setSelection({
                            section: sectionIndex,
                            field: fieldIndex,
                          })
                        }
                      >
                        <strong>{field.label || "Без названия"}</strong>
                        <small>
                          {
                            fieldTypes.find(
                              ([type]) => type === field.type,
                            )?.[1]
                          }{" "}
                          · {field.key}
                        </small>
                      </button>
                      <button
                        aria-label={"Поле выше: " + field.label}
                        disabled={fieldIndex === 0}
                        onClick={() => {
                          updateFields(sectionIndex, (fields) =>
                            moveItem(fields, fieldIndex, -1),
                          );
                          setSelection(null);
                        }}
                      >
                        ↑
                      </button>
                      <button
                        aria-label={"Поле ниже: " + field.label}
                        disabled={fieldIndex === section.fields.length - 1}
                        onClick={() => {
                          updateFields(sectionIndex, (fields) =>
                            moveItem(fields, fieldIndex, 1),
                          );
                          setSelection(null);
                        }}
                      >
                        ↓
                      </button>
                      <button
                        aria-label={"Удалить поле: " + field.label}
                        onClick={() => {
                          updateFields(sectionIndex, (fields) =>
                            fields.filter((_, index) => index !== fieldIndex),
                          );
                          setSelection(null);
                        }}
                      >
                        ×
                      </button>
                    </div>
                    {field.type === "group" && (
                      <div className="group-builder">
                        {field.children?.map((child, index) => (
                          <div className="button-row" key={index}>
                            <button
                              onClick={() =>
                                setSelection({
                                  section: sectionIndex,
                                  field: fieldIndex,
                                  child: index,
                                })
                              }
                            >
                              {child.label} · {child.key}
                            </button>
                            {([-1, 1] as const).map(direction => <button key={direction} aria-label={`${direction === -1 ? "Дочернее поле выше" : "Дочернее поле ниже"}: ${child.label}`} disabled={direction === -1 ? index === 0 : index === (field.children?.length || 0) - 1} onClick={() => {
                              updateFields(sectionIndex, fields => fields.map((item, position) => position === fieldIndex ? {...item, children: moveItem(item.children || [], index, direction)} : item));
                              setSelection(null);
                            }}>{direction === -1 ? "↑" : "↓"}</button>)}
                            <button
                              aria-label={
                                "Удалить дочернее поле: " + child.label
                              }
                              onClick={() => {
                                updateFields(sectionIndex, (fields) =>
                                  fields.map((item, position) =>
                                    position === fieldIndex
                                      ? {
                                          ...item,
                                          children: item.children?.filter(
                                            (_, position) => position !== index,
                                          ),
                                        }
                                      : item,
                                  ),
                                );
                                setSelection(null);
                              }}
                            >
                              ×
                            </button>
                          </div>
                        ))}
                        <button
                          disabled={addType === "group"}
                          onClick={() =>
                            updateFields(sectionIndex, (fields) =>
                              fields.map((item, index) =>
                                index === fieldIndex
                                  ? {
                                      ...item,
                                      children: [
                                        ...(item.children || []),
                                        newField(addType),
                                      ],
                                    }
                                  : item,
                              ),
                            )
                          }
                        >
                          + Поле в группу
                        </button>
                      </div>
                    )}
                  </div>
                ))}
                <button
                  className="add-field"
                  onClick={() => {
                    const next = newField(addType);
                    updateFields(sectionIndex, (fields) => [...fields, next]);
                    setSelection({
                      section: sectionIndex,
                      field: section.fields.length,
                    });
                  }}
                >
                  + Добавить поле
                </button>
              </section>
            ))}
          </fieldset>
        </div>
        <aside className="property-panel">
          <fieldset disabled={published || action.busy}>
            {selected ? (
              <PropertyEditor
                key={
                  String(selection?.section) +
                  "-" +
                  String(selection?.field) +
                  "-" +
                  String(selection?.child)
                }
                field={selected}
                fields={allFields}
                references={references}
                series={compatibleSeries}
                onChange={updateField}
                child={selection?.child !== undefined}
              />
            ) : (
              <div className="empty-state">
                Выберите поле, чтобы настроить его свойства.
              </div>
            )}
          </fieldset>
        </aside>
      </div>
    </section>
  );
}
