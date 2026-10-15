import { useState } from "react";
import { ApiError, apiUpload, encodeFile, saveDownload, saveExport } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { DynamicForm } from "../engine/DynamicForm";
import {
  editableValues,
  initialValues,
  prunePendingFiles,
  type Card,
  type DocumentType,
  type Field,
  type FileVersion,
  type ReferenceSet,
  type Registry,
  type SchemaVersion,
  type Values,
} from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { CardNumberAssignment } from "./NumberBook";
import { DocumentContent } from "./DocumentContent";
import { ContentMatches } from "../components/ContentMatches";
import type { ContentHit, ContentTarget } from "../engine/content";
import { DocumentRelations, RelationStatusHistory } from "./DocumentRelations";
import type { RelationNavigation } from "../engine/relations";

function filesInRevision(card: Card, values: Values): FileVersion[] {
  const ids = new Set<string>();
  function collect(fields: Field[], current: Values) {
    for (const field of fields) {
      const value = current[field.key];
      if (field.type === "file" && Array.isArray(value)) {
        for (const id of value) if (typeof id === "string") ids.add(id);
      } else if (field.type === "group" && Array.isArray(value)) {
        for (const row of value)
          if (row && typeof row === "object" && !Array.isArray(row))
            collect(field.children || [], row as Values);
      }
    }
  }
  collect(card.schema.sections.flatMap((section) => section.fields), values);
  // Historical row positions can differ from the current attachment field path.
  // Match each historical field by its recorded IDs rather than that path.
  return card.files
    .filter((file) => ids.has(file.id))
    .map((file) => ({ ...file, field_key: undefined }));
}

async function encodeAttachments(pending: Record<string, File>) {
  const total = Object.values(pending).reduce(
    (size, file) => size + file.size,
    0,
  );
  if (total > 25 * 1024 * 1024)
    throw new Error(
      "Общий размер файлов превышает 25 МиБ. Добавьте остальные файлы отдельно.",
    );
  return Promise.all(
    Object.entries(pending).map(async ([field_key, file]) => ({
      field_key,
      name: file.name,
      mime: file.type || "application/octet-stream",
      content_base64: await encodeFile(file),
    })),
  );
}

function DocumentCard({
  card,
  readOnly,
  references,
  onSaved,
  onClose,
  contentTarget,
  onNavigate,
}: {
  card: Card;
  readOnly: boolean;
  references: ReferenceSet[];
  onSaved: () => void;
  onClose: () => void;
  contentTarget?: ContentTarget;
  onNavigate: (value: RelationNavigation) => void;
}) {
  const auth = useAuth();
  const action = useAction();
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Values>(card.values);
  const [conflict, setConflict] = useState(false);
  const [tab, setTab] = useState(contentTarget ? "content" : "card");
  const [pendingFiles, setPendingFiles] = useState<Record<string, File>>({});
  const [revision, setRevision] = useState(card.revision);
  const history = useData<
    {
      id: string;
      revision: number;
      values: Values;
      created_at: string;
      created_by: string;
    }[]
  >(
    tab === "history"
      ? "/documents/" +
          card.id +
          "/history?mode=" +
          (readOnly ? "viewer" : "editor")
      : null,
  );
  const canEdit = !readOnly && card.permissions.includes("update");
  const canUpload = !readOnly && card.permissions.includes("upload");
  const hasUnsavedChanges =
    editing &&
    (JSON.stringify(values) !== JSON.stringify(card.values) ||
      Object.keys(pendingFiles).length > 0);
  function changeValues(next: Values) {
    setPendingFiles((current) =>
      prunePendingFiles(
        current,
        card.schema,
        next,
        auth.user?.roles || [],
        "edit",
        values,
      ),
    );
    setValues(next);
  }
  async function save() {
    try {
      const files = await encodeAttachments(
        prunePendingFiles(
          pendingFiles,
          card.schema,
          values,
          auth.user?.roles || [],
          "edit",
        ),
      );
      const current = await action.request<Card>(
        "/documents/" + card.id,
        "PUT",
        {
          revision,
          values: editableValues(
            card.schema,
            values,
            "edit",
            auth.user?.roles || [],
          ),
          ...(files.length ? { files } : {}),
        },
        60000,
      );
      setRevision(current.revision);
      setPendingFiles({});
      setEditing(false);
      onSaved();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409)
        setConflict(true);
      throw failure;
    }
  }
  async function upload(field: string, file: File) {
    if (hasUnsavedChanges)
      throw new Error("Сохраните или отмените изменения карточки перед загрузкой файла.");
    try {
      await apiUpload(
        "/documents/" +
          card.id +
          "/files?" +
          new URLSearchParams({
            filename: file.name,
            revision: String(revision),
            ...(field ? { field_key: field } : {}),
          }),
        file,
        auth.user?.csrf_token || "",
      );
      onSaved();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409)
        setConflict(true);
      throw failure;
    }
  }
  return (
    <section className="document-card">
      <div className="toolbar">
        <div>
          <span className="eyebrow">КАРТОЧКА ДОКУМЕНТА</span>
          <h1>{card.title || "Документ"}</h1>
          <p>
            {card.reg_number || "Без номера"} · {card.reg_date || "Без даты"} ·
            редакция {card.revision}
          </p>
          <p className="muted">UUID: {card.id}</p>
        </div>
        <button onClick={onClose}>К списку</button>
      </div>
      <div className="tabs">
        <button
          className={tab === "card" ? "active" : ""}
          onClick={() => setTab("card")}
        >
          Карточка
        </button>
        <button
          className={tab === "files" ? "active" : ""}
          onClick={() => setTab("files")}
        >
          Файлы и версии
        </button>
        <button
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          История изменений
        </button>
        <button className={tab === "content" ? "active" : ""} onClick={() => setTab("content")}>Содержание</button>
        <button className={tab === "relations" ? "active" : ""} onClick={() => setTab("relations")}>Связанные акты</button>
        <button className={tab === "basis" ? "active" : ""} onClick={() => setTab("basis")}>Основания и изменения</button>
        <button className={tab === "status-history" ? "active" : ""} onClick={() => setTab("status-history")}>История статуса</button>
      </div>
      <Feedback {...action} />
      {!readOnly && card.schema.sections.some(section => section.fields.some(field => field.number_series_id && !card.values[field.key])) && <CardNumberAssignment card={card} onSaved={onSaved}/>}
      {conflict && (
        <div className="error" role="alert">
          <p>
            Документ изменён другим пользователем. Ваши изменения ещё не
            сохранены.
          </p>
          <button
            onClick={() => {
              setConflict(false);
              onSaved();
            }}
          >
            Загрузить актуальную редакцию
          </button>
        </div>
      )}
      {tab === "card" && (
        <form
          className="panel"
          onSubmit={(event) => {
            event.preventDefault();
            void action.run(save);
          }}
        >
          <DynamicForm
            schema={card.schema}
            values={values}
            onChange={changeValues}
            mode={editing ? "edit" : "view"}
            displayMode={readOnly ? "viewer" : "editor"}
            roles={auth.user?.roles || []}
            references={references}
            registryId={card.registry_id}
            files={card.files}
            canDownload={card.permissions.includes("download")}
            onFile={
              canUpload
                ? (field, file) =>
                    setPendingFiles((current) => ({
                      ...current,
                      [field]: file,
                    }))
                : undefined
            }
          />
          {canEdit && (
            <div className="button-row">
              {editing ? (
                <>
                  <button
                    key="save-card"
                    type="submit"
                    className="primary"
                    disabled={action.busy || conflict}
                  >
                    Сохранить карточку
                  </button>
                  <button
                    key="cancel-card-edit"
                    type="button"
                    onClick={() => {
                      setValues(card.values);
                      setPendingFiles({});
                      setEditing(false);
                    }}
                  >
                    Отмена изменений
                  </button>
                </>
              ) : (
                <button
                  key="edit-card"
                  type="button"
                  className="primary"
                  onClick={() => setEditing(true)}
                >
                  Редактировать карточку
                </button>
              )}
            </div>
          )}
          {Object.entries(pendingFiles).map(([field, file]) => (
            <div className="file-line" key={field}>
              <span>{file.name}</span>
              <small>
                {editing ? "Будет загружен при сохранении карточки" : ""}
              </small>
              <button
                type="button"
                disabled={action.busy}
                onClick={() =>
                  setPendingFiles((current) => {
                    const next = { ...current };
                    delete next[field];
                    return next;
                  })
                }
              >
                Убрать выбранный файл
              </button>
            </div>
          ))}
        </form>
      )}
      {tab === "files" && (
        <section className="panel">
          <h2>Оригиналы и вложения</h2>
          {canUpload && (
            <label className="upload-area">
              Добавить файл или следующую версию
              <input
                type="file"
                disabled={action.busy || conflict || hasUnsavedChanges}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file)
                    void action.run(() => upload("", file), "Файл загружен");
                  event.target.value = "";
                }}
              />
              <small>
                Каждая загрузка сохраняет отдельную неизменяемую версию.
              </small>
              {hasUnsavedChanges && (
                <small role="status">
                  Сохраните или отмените изменения карточки перед загрузкой файла.
                </small>
              )}
            </label>
          )}
          <div className="file-list">
            {card.files?.map((file) => (
              <div className="file-line" key={file.id}>
                <span>
                  <strong>{file.name}</strong>
                  <small>
                    Версия {file.version} · {(file.size / 1024).toFixed(1)} КБ ·{" "}
                    {new Date(file.created_at).toLocaleString("ru-RU")}
                  </small>
                </span>
                {card.permissions.includes("download") && (
                  <button
                    disabled={action.busy}
                    onClick={() =>
                      void action.run(
                        () =>
                          saveDownload(
                            file.id,
                            file.name,
                            readOnly ? "viewer" : "editor",
                          ),
                        "Файл скачан",
                      )
                    }
                  >
                    Скачать
                  </button>
                )}
              </div>
            ))}
          </div>
          {!card.files?.length && (
            <p className="empty-state">Файлы пока не загружены.</p>
          )}
        </section>
      )}
      {tab === "history" && (
        <section className="panel">
          <h2>История реквизитов</h2>
          <Feedback {...history} />
          {history.data?.map((item) => (
            <details key={item.id}>
              <summary>
                Редакция {item.revision} ·{" "}
                {new Date(item.created_at).toLocaleString("ru-RU")}
              </summary>
              <DynamicForm
                schema={card.schema}
                values={item.values}
                mode="view"
                displayMode={readOnly ? "viewer" : "editor"}
                roles={auth.user?.roles || []}
                references={references}
                registryId={card.registry_id}
                files={filesInRevision(card, item.values)}
                canDownload={card.permissions.includes("download")}
              />
            </details>
          ))}
        </section>
      )}
      {tab === "content" && <DocumentContent card={card} readOnly={readOnly} target={contentTarget}/>}
      {(tab === "relations" || tab === "basis") && <DocumentRelations key={tab} card={card} readOnly={readOnly} basis={tab === "basis"} onNavigate={onNavigate}/>}
      {tab === "status-history" && <RelationStatusHistory card={card} readOnly={readOnly} onNavigate={onNavigate}/>}
    </section>
  );
}

function CreateDocument({
  registry,
  types,
  references,
  onCreated,
  onClose,
}: {
  registry: Registry;
  types: DocumentType[];
  references: ReferenceSet[];
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const auth = useAuth();
  const [typeId, setTypeId] = useState("");
  const versions = useData<SchemaVersion[]>(
    typeId ? "/document-types/" + typeId + "/versions?mode=editor" : null,
  );
  const version = versions.data?.find((item) => item.state === "published");
  return (
    <section>
      <div className="toolbar">
        <div>
          <h1>Новый документ</h1>
          <p>Реестр: {registry.name}</p>
        </div>
        <button onClick={onClose}>К списку</button>
      </div>
      <label className="picker">
        Тип документа
        <select
          value={typeId}
          onChange={(event) => setTypeId(event.target.value)}
        >
          <option value="">Выберите тип</option>
          {types
            .filter((type) => registry.document_type_ids.includes(type.id))
            .map((type) => (
              <option key={type.id} value={type.id}>
                {type.name}
              </option>
            ))}
        </select>
      </label>
      <Feedback {...versions} />
      {typeId && !versions.loading && !versions.error && !version && (
        <p className="empty-state">
          Для этого типа ещё нет опубликованной формы. Обратитесь к
          администратору.
        </p>
      )}
      {version && (
        <NewDocumentForm
          key={version.id}
          registry={registry}
          version={version}
          references={references}
          roles={auth.user?.roles || []}
          onCreated={onCreated}
        />
      )}
    </section>
  );
}
function NewDocumentForm({
  registry,
  version,
  references,
  roles,
  onCreated,
}: {
  registry: Registry;
  version: SchemaVersion;
  references: ReferenceSet[];
  roles: string[];
  onCreated: (id: string) => void;
}) {
  const action = useAction();
  const [values, setValues] = useState<Values>(() =>
    initialValues(version.schema),
  );
  const [pendingFiles, setPendingFiles] = useState<Record<string, File>>({});
  function changeValues(next: Values) {
    setPendingFiles((current) =>
      prunePendingFiles(current, version.schema, next, roles, "create", values),
    );
    setValues(next);
  }
  async function create() {
    const allowedFiles = prunePendingFiles(
      pendingFiles,
      version.schema,
      values,
      roles,
      "create",
    );
    const files = await encodeAttachments(allowedFiles);
    const card = await action.request<Card>(
      "/documents",
      "POST",
      {
        registry_id: registry.id,
        document_type_id: version.document_type_id,
        schema_version_id: version.id,
        values: editableValues(version.schema, values, "create", roles),
        ...(files.length ? { files } : {}),
      },
      60000,
    );
    onCreated(card.id);
  }
  return (
    <form
      className="panel"
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(create, "Документ создан");
      }}
    >
      <DynamicForm
        schema={version.schema}
        values={values}
        onChange={changeValues}
        mode="create"
        roles={roles}
        references={references}
        registryId={registry.id}
        onFile={
          registry.permissions.includes("upload")
            ? (field, file) =>
                setPendingFiles((current) => ({ ...current, [field]: file }))
            : undefined
        }
      />
      {Object.entries(pendingFiles).map(([field, file]) => (
        <div key={field} className="file-line">
          <span>{file.name} — будет сохранён вместе с карточкой</span>
          <button
            type="button"
            aria-label={"Убрать выбранный файл: " + file.name}
            onClick={() =>
              setPendingFiles((current) => {
                const next = { ...current };
                delete next[field];
                return next;
              })
            }
          >
            Убрать выбранный файл
          </button>
        </div>
      ))}
      <Feedback {...action} />
      <button className="primary" disabled={action.busy}>
        {action.busy ? "Сохранение…" : "Создать документ"}
      </button>
    </form>
  );
}

export function Documents({ readOnly = false }: { readOnly?: boolean }) {
  const auth = useAuth();
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const references = useData<ReferenceSet[]>(
    "/references?mode=" + (readOnly ? "viewer" : "editor"),
  );
  const [registryId, setRegistryId] = useState("");
  const [documentId, setDocumentId] = useState(() => new URLSearchParams(window.location.search).get("document") || "");
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState("");
  const [contentQ, setContentQ] = useState("");
  const [fuzzy, setFuzzy] = useState(true);
  const [contentTarget, setContentTarget] = useState<ContentTarget | undefined>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("tab") === "content" ? {fileId: params.get("file") || undefined, blockId: params.get("block") || undefined, ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : undefined} : undefined;
  });
  const [number, setNumber] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [typeId, setTypeId] = useState("");
  const [filterKey, setFilterKey] = useState("");
  const [filterValue, setFilterValue] = useState<unknown>(null);
  const [sort, setSort] = useState("updated_at");
  const [direction, setDirection] = useState("desc");
  const [filterOp, setFilterOp] = useState("eq");
  const [filterEnd, setFilterEnd] = useState("");
  const [viewName, setViewName] = useState("");
  const [savedViews, setSavedViews] = useState<{name: string; registry: string; query: string}[]>(() => {
    try { const data = JSON.parse(localStorage.getItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`) || "[]"); return Array.isArray(data) ? data.filter(item => typeof item?.name === "string" && typeof item?.registry === "string" && typeof item?.query === "string") : []; } catch { return []; }
  });
  const exportAction = useAction();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const registry = registries.data?.find((item) => item.id === registryId);
  const versions = useData<SchemaVersion[]>(
    typeId
      ? "/document-types/" +
          typeId +
          "/versions?mode=" +
          (readOnly ? "viewer" : "editor")
      : null,
  );
  const publishedSchema = versions.data?.find(item => item.state === "published")?.schema;
  const configuredColumns = new URLSearchParams(search).get("document_type_id") === typeId ? publishedSchema?.list_view?.columns || [] : [];
  const exportColumns = configuredColumns.filter(column => publishedSchema?.sections.some(section => section.fields.some(field => field.key === column.key && field.exportable)));
  const filterFields =
    publishedSchema?.sections.flatMap((section) => section.fields)
      .filter(
        (field) => field.filterable && !field.visible_when && !["file", "group"].includes(field.type),
      ) || [];
  const filterField = filterFields.find((field) => field.key === filterKey);
  const list = useData<{
    items: (Card | ContentHit)[];
    total: number;
    page: number;
    page_size: number;
  }>(
    registryId && !documentId && !creating
      ? (new URLSearchParams(search).has("text") ? "/content/search?registry_id=" : "/documents?registry_id=") +
          registryId +
          "&page=" +
          page +
          "&page_size=20&mode=" +
          (readOnly ? "viewer" : "editor") +
          search
      : null,
  );
  const card = useData<Card>(
    documentId
      ? "/documents/" + documentId + "?mode=" + (readOnly ? "viewer" : "editor")
      : null,
  );
  function applySearch() {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (contentQ.trim()) {params.set("text", contentQ.trim()); params.set("fuzzy", String(fuzzy));}
    if (number) params.set("reg_number", number);
    if (status) params.set("status", status);
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    if (typeId) params.set("document_type_id", typeId);
    params.set("sort", sort);
    params.set("direction", direction);
    if (
      filterKey &&
      filterValue !== null &&
      filterValue !== undefined &&
      filterValue !== ""
    ) {
      params.set("filters", JSON.stringify({ [filterKey]: filterOp === "eq" ? filterValue : {op: filterOp, value: filterOp === "between" ? [filterValue, filterEnd] : filterValue} }));
    }
    if (filterKey && ["present", "absent"].includes(filterOp)) params.set("filters", JSON.stringify({[filterKey]: {op: filterOp}}));
    setPage(1);
    setSearch("&" + params.toString());
  }
  function persistViews(next: typeof savedViews) {
    setSavedViews(next);
    try { localStorage.setItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`, JSON.stringify(next)); } catch { /* Browser storage can be disabled; current filters still work. */ }
  }
  function loadView(query: string) {
    const params = new URLSearchParams(query);
    setQ(params.get("q") || ""); setNumber(params.get("reg_number") || ""); setStatus(params.get("status") || ""); setFrom(params.get("date_from") || ""); setTo(params.get("date_to") || ""); setTypeId(params.get("document_type_id") || ""); setSort(params.get("sort") || "updated_at"); setDirection(params.get("direction") || "desc");
    setContentQ(params.get("text") || ""); setFuzzy(params.get("fuzzy") !== "false");
    try { const [key, value] = Object.entries(JSON.parse(params.get("filters") || "{}"))[0] || ["", null]; setFilterKey(key); const typed = value as {op?: string; value?: unknown} | null; setFilterOp(typed && typeof typed === "object" ? typed.op || "eq" : "eq"); setFilterValue(typed && typeof typed === "object" ? (Array.isArray(typed.value) && typed.op === "between" ? typed.value[0] : typed.value) : value); setFilterEnd(typed?.op === "between" && Array.isArray(typed.value) ? String(typed.value[1]) : ""); } catch { setFilterKey(""); setFilterValue(null); }
    setPage(1); setSearch("&" + query);
  }
  const close = () => {
    setDocumentId("");
    setContentTarget(undefined);
    setCreating(false);
    list.reload();
  };
  if (documentId)
    return (
      <>
        <Feedback {...card} />
        {card.error && <button onClick={close}>К реестрам</button>}
        {card.data && (
          <DocumentCard
            key={card.data.id + ":" + card.data.revision + ":" + navigationRevision}
            card={card.data}
            readOnly={readOnly}
            references={references.data || []}
            onSaved={card.reload}
            onClose={close}
            contentTarget={contentTarget}
            onNavigate={value => {setContentTarget(value.contentTarget); setDocumentId(value.documentId); setNavigationRevision(current => current + 1);}}
          />
        )}
      </>
    );
  if (creating && registry)
    return (
      <CreateDocument
        registry={registry}
        types={types.data || []}
        references={references.data || []}
        onCreated={(id) => {
          setCreating(false);
          setDocumentId(id);
        }}
        onClose={close}
      />
    );
  return (
    <>
      <div className="toolbar">
        <div>
          <span className="eyebrow">
            {readOnly ? "ПРОСМОТР ДОКУМЕНТОВ" : "РАБОТА С ДОКУМЕНТАМИ"}
          </span>
          <h1>{registry ? registry.name : "Мои реестры"}</h1>
        </div>
        {!readOnly && registry?.permissions.includes("create") && (
          <button className="primary" onClick={() => setCreating(true)}>
            Создать документ
          </button>
        )}
      </div>
      <Feedback
        error={registries.error || types.error || references.error}
        loading={registries.loading}
      />
      <div className="registry-tabs">
        {registries.data
          ?.filter((item) => item.permissions.includes("read"))
          .map((item) => (
            <button
              key={item.id}
              className={registryId === item.id ? "active" : ""}
              onClick={() => {
                setRegistryId(item.id);
                setPage(1);
                setSearch("");
                setQ("");
                setContentQ(""); setFuzzy(true); setContentTarget(undefined);
                setNumber("");
                setStatus("");
                setFrom("");
                setTo("");
                setSort("updated_at");
                setDirection("desc"); setFilterOp("eq"); setFilterEnd("");
                setTypeId("");
                setFilterKey("");
                setFilterValue(null);
              }}
            >
              {item.name}
            </button>
          ))}
      </div>
      {!registryId && (
        <div className="empty-state">
          {registries.data?.some((item) => item.permissions.includes("read"))
            ? "Выберите реестр для просмотра документов."
            : "Вам пока не предоставлен доступ к реестрам. Обратитесь к администратору."}
        </div>
      )}
      {registry && (
        <>
          <details className="panel"><summary>Сохранённые фильтры и сортировки</summary>
            <div className="inline-form"><label>Название представления<input value={viewName} onChange={event => setViewName(event.target.value)} /></label><button disabled={!viewName.trim() || !search} onClick={() => {persistViews([...savedViews.filter(item => !(item.registry === registryId && item.name === viewName.trim())), {name: viewName.trim(), registry: registryId, query: search.slice(1)}]); setViewName("");}}>Сохранить текущий поиск</button></div>
            {savedViews.filter(item => item.registry === registryId).map(item => <div className="button-row" key={item.name}><button onClick={() => loadView(item.query)}>{item.name}</button><button aria-label={`Удалить представление: ${item.name}`} onClick={() => persistViews(savedViews.filter(current => current !== item))}>Удалить</button></div>)}
          </details>
          <form
            className="panel filters"
            onSubmit={(event) => {
              event.preventDefault();
              applySearch();
            }}
          >
            <label>
              Поиск
              <input
                type="search"
                value={q}
                placeholder="Название или реквизиты"
                onChange={(event) => setQ(event.target.value)}
              />
            </label>
            <label>Поиск по содержанию<input type="search" maxLength={255} value={contentQ} placeholder="Фраза из документа" onChange={event => setContentQ(event.target.value)} /></label>
            <label className="checkbox-label"><input type="checkbox" checked={fuzzy} onChange={event => setFuzzy(event.target.checked)}/>Учитывать опечатки</label>
            <label>
              Регистрационный номер
              <input
                value={number}
                onChange={(event) => setNumber(event.target.value)}
              />
            </label>
            <label>
              Статус
              <input
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              />
            </label>
            <label>
              Дата с
              <input
                type="date"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              />
            </label>
            <label>
              Дата по
              <input
                type="date"
                value={to}
                onChange={(event) => setTo(event.target.value)}
              />
            </label>
            <label>
              Тип
              <select
                value={typeId}
                onChange={(event) => {
                  setTypeId(event.target.value);
                  setFilterKey(""); setFilterOp("eq"); setFilterEnd(""); setSort("updated_at");
                }}
              >
                <option value="">Все типы</option>
                {types.data
                  ?.filter((type) =>
                    registry.document_type_ids.includes(type.id),
                  )
                  .map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Сортировка
              <select
                value={sort}
                disabled={!!contentQ.trim()}
                onChange={(event) => setSort(event.target.value)}
              >
                <option value="updated_at">Последние изменения</option>
                <option value="reg_date">Дата регистрации</option>
                <option value="reg_number">Номер</option>
                <option value="title">Название</option>
                {filterFields.filter(field => !field.system && field.type !== "multi_reference").map(field => <option key={field.key} value={`custom:${field.key}`}>{field.label}</option>)}
              </select>
            </label>
            <label>Направление сортировки<select value={direction} disabled={!!contentQ.trim()} onChange={event => setDirection(event.target.value)}><option value="asc">По возрастанию</option><option value="desc">По убыванию</option></select></label>
            {!!contentQ.trim() && <p className="muted">Результаты поиска по содержанию упорядочены по релевантности.</p>}
            {filterFields.length > 0 && (
              <>
                <label>
                  Дополнительный фильтр
                  <select
                    value={filterKey}
                    onChange={(event) => {
                      setFilterKey(event.target.value);
                      setFilterValue(null); setFilterOp("eq"); setFilterEnd("");
                    }}
                  >
                    <option value="">Без дополнительного фильтра</option>
                    {filterFields.map((field) => (
                      <option key={field.key} value={field.key}>
                        {field.label}
                      </option>
                    ))}
                  </select>
                </label>
                {filterField && <label>Сравнение<select value={filterOp} onChange={event => setFilterOp(event.target.value)}><option value="eq">Равно</option><option value="ne">Не равно</option>{["text", "textarea"].includes(filterField.type) && <option value="contains">Содержит</option>}{["integer", "decimal", "date", "datetime"].includes(filterField.type) && <><option value="gte">Не меньше</option><option value="lte">Не больше</option><option value="between">В диапазоне</option></>}<option value="present">Заполнено</option><option value="absent">Не заполнено</option></select></label>}
                {filterField && !["present", "absent"].includes(filterOp) && (
                  <div className="filter-value">
                    <DynamicForm
                      schema={{
                        sections: [
                          {
                            key: "filter",
                            title: "",
                            fields: [
                              {
                                ...filterField,
                                label: "Значение фильтра",
                                type:
                                  filterField.type === "multi_reference"
                                    ? "reference"
                                    : filterField.type,
                                required: false,
                                visible_when: null,
                                required_when: null,
                                editable: true,
                                write_roles: [],
                                modes: [],
                              },
                            ],
                          },
                        ],
                      }}
                      values={{ [filterKey]: filterValue }}
                      onChange={(values) => setFilterValue(values[filterKey])}
                      mode="edit"
                      roles={auth.user?.roles || []}
                      registryId={registryId}
                      references={references.data || []}
                      displayMode={readOnly ? "viewer" : "editor"}
                    />
                    {filterOp === "between" && <label>Верхняя граница<input type={filterField.type === "date" ? "date" : filterField.type === "datetime" ? "datetime-local" : "number"} step="any" value={filterEnd} onChange={event => setFilterEnd(event.target.value)} /></label>}
                  </div>
                )}
              </>
            )}
            <button className="primary">Найти</button>
          </form>
          <Feedback {...list} />
          {new URLSearchParams(search).has("text") && <p className="muted">Недавно загруженные оригиналы появляются в поиске после обработки. Состояние и ошибки доступны во вкладке «Содержание» карточки.</p>}
          <Feedback {...exportAction} />
          <div className="button-row">{(["csv", "xlsx"] as const).map(format => <button key={format} disabled={list.loading || exportAction.busy || (configuredColumns.length > 0 && !exportColumns.length)} onClick={() => void exportAction.run(() => saveExport(`${new URLSearchParams(search).has("text") ? "/content/search/export" : "/documents/export"}?registry_id=${encodeURIComponent(registryId)}&format=${format}&mode=${readOnly ? "viewer" : "editor"}${search}${exportColumns.length ? "&columns=" + encodeURIComponent(exportColumns.map(column => column.key).join(",")) : ""}`, `documents.${format}`), "Экспорт подготовлен")}>Экспорт {format.toUpperCase()}</button>)}</div>
          {configuredColumns.length > 0 && !exportColumns.length && <p className="muted">В выбранном представлении нет разрешённых для экспорта колонок.</p>}
          <div className="toolbar">
            <h2>Документы</h2>
            <span className="muted">Найдено: {list.data?.total ?? "…"}</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {configuredColumns.length ? configuredColumns.map(column => <th key={column.key}>{column.label}</th>) : <><th>Номер</th><th>Дата</th><th>Название</th></>}
                  <th>Редакция</th>
                  {new URLSearchParams(search).has("text") && <th>Найденные фрагменты</th>}
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {list.data?.items.map((result) => {const item = "document" in result ? result.document : result; return (
                  <tr key={item.id}>
                    {configuredColumns.length ? configuredColumns.map(column => <td key={column.key}>{displayListValue(item.display_values?.[column.key] ?? item.values?.[column.key])}</td>) : <><td>{item.reg_number || "—"}</td><td>{item.reg_date || "—"}</td><td>{item.title || "Документ"}</td></>}
                    <td>{item.revision}</td>
                    {"document" in result && <td><ContentMatches matches={result.matches} query={new URLSearchParams(search).get("text") || ""} onOpen={target => {setContentTarget(target); setDocumentId(item.id);}}/></td>}
                    <td>
                      <button onClick={() => setDocumentId(item.id)}>
                        Открыть карточку
                      </button>
                    </td>
                  </tr>
                );})}
              </tbody>
            </table>
          </div>
          {list.data?.items.length === 0 && (
            <div className="empty-state">
              Документов по выбранным условиям пока нет.
            </div>
          )}
          <div className="pagination">
            <button
              disabled={page === 1 || list.loading}
              onClick={() => setPage(page - 1)}
            >
              Назад
            </button>
            <span>Страница {page}</span>
            <button
              disabled={
                !list.data || page * 20 >= list.data.total || list.loading
              }
              onClick={() => setPage(page + 1)}
            >
              Вперёд
            </button>
          </div>
        </>
      )}
    </>
  );
}

function displayListValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
