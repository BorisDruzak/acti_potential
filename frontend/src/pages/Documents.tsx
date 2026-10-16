import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRequest, apiUpload, encodeFile, saveDownload, saveExport } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { SearchStatus } from "../components/SearchStatus";
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
import { searchBody, type SearchScope } from "../engine/search";
import { useAction, useData } from "../engine/useData";
import { CardNumberAssignment } from "./NumberBook";
import { DocumentContent } from "./DocumentContent";
import { DocumentLayoutSwitch, DocumentWorkspace, useDocumentLayout } from "../components/DocumentWorkspace";
import { DocumentResults } from "../components/DocumentResults";
import { useDocumentSchemas } from "../engine/documentPresentation";
import type { ContentHit, ContentTarget } from "../engine/content";
import { DocumentRelations, RelationStatusHistory } from "./DocumentRelations";
import type { RelationNavigation } from "../engine/relations";
import { knowledgeTabs, RelatedKnowledge, type KnowledgeTab } from "./Knowledge";

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
  onDirtyChange,
}: {
  card: Card;
  readOnly: boolean;
  references: ReferenceSet[];
  onSaved: () => void;
  onClose: () => void;
  contentTarget?: ContentTarget;
  onNavigate: (value: RelationNavigation) => void;
  onDirtyChange?: (dirty: boolean) => void;
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
  useEffect(() => {onDirtyChange?.(hasUnsavedChanges);}, [hasUnsavedChanges, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
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
        {knowledgeTabs.map(name => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{name}</button>)}
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
      {knowledgeTabs.includes(tab as KnowledgeTab) && <RelatedKnowledge key={tab} documentId={card.id} readOnly={readOnly} activeTab={tab as KnowledgeTab}/>}
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
  const {layout, choose: chooseLayout, resize: resizeLayout, restore: restoreLayout, back: backToResults} = useDocumentLayout(auth.user?.id || "anonymous", readOnly);
  const dirtyCard = useRef(false);
  const reportDirty = useCallback((value: boolean) => {dirtyCard.current = value;}, []);
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const references = useData<ReferenceSet[]>(
    "/references?mode=" + (readOnly ? "viewer" : "editor"),
  );
  const initial = new URLSearchParams(window.location.search);
  const [registryId, setRegistryId] = useState(() => initial.get("registry_id") || "");
  const [documentId, setDocumentId] = useState(() => new URLSearchParams(window.location.search).get("document") || "");
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [savedDocument, setSavedDocument] = useState<{id: string; revision: number} | null>(null);
  const [creating, setCreating] = useState(false);
  const [q, setQ] = useState(() => initial.get("q") || initial.get("text") || "");
  const [scope, setScope] = useState<SearchScope>(() => (initial.get("scope") as SearchScope) || "all");
  const [versionScope, setVersionScope] = useState(() => initial.get("version_scope") || "current");
  const [advanced, setAdvanced] = useState(false);
  const [cursor, setCursor] = useState(() => initial.get("cursor") || "");
  const [previousCursors, setPreviousCursors] = useState<string[]>([]);
  const [fuzzy, setFuzzy] = useState(() => initial.get("fuzzy") !== "false");
  const [contentTarget, setContentTarget] = useState<ContentTarget | undefined>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("tab") === "content" ? {fileId: params.get("file") || undefined, blockId: params.get("block") || undefined, ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : undefined} : undefined;
  });
  const [number, setNumber] = useState(() => initial.get("reg_number") || "");
  const [registrationDate, setRegistrationDate] = useState(() => initial.get("reg_date") || "");
  const [status, setStatus] = useState(() => initial.get("status") || "");
  const [from, setFrom] = useState(() => initial.has("reg_date") ? "" : initial.get("date_from") || "");
  const [to, setTo] = useState(() => initial.has("reg_date") ? "" : initial.get("date_to") || "");
  const [typeId, setTypeId] = useState(() => initial.get("document_type_id") || "");
  const initialFilter = (() => {try {return Object.entries(JSON.parse(initial.get("filters") || "{}"))[0] || ["", null];} catch {return ["", null];}})();
  const initialFilterKey = String(initialFilter[0]);
  const initialFilterSpec = initialFilter[1] as {op?: string; value?: unknown} | null;
  const [filterKey, setFilterKey] = useState(initialFilterKey);
  const [filterValue, setFilterValue] = useState<unknown>(() => initialFilterSpec && typeof initialFilterSpec === "object" ? (initialFilterSpec.op === "between" && Array.isArray(initialFilterSpec.value) ? initialFilterSpec.value[0] : initialFilterSpec.value) : initialFilterSpec);
  const [sort, setSort] = useState(() => initial.get("sort") || "auto");
  const [direction, setDirection] = useState(() => initial.get("direction") || "desc");
  const [filterOp, setFilterOp] = useState(() => initialFilterSpec && typeof initialFilterSpec === "object" ? initialFilterSpec.op || "eq" : "eq");
  const [filterEnd, setFilterEnd] = useState(() => initialFilterSpec?.op === "between" && Array.isArray(initialFilterSpec.value) ? String(initialFilterSpec.value[1]) : "");
  const [viewName, setViewName] = useState("");
  const [savedViews, setSavedViews] = useState<{name: string; registry: string; query: string}[]>(() => {
    try { const data = JSON.parse(localStorage.getItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`) || "[]"); return Array.isArray(data) ? data.filter(item => typeof item?.name === "string" && typeof item?.registry === "string" && typeof item?.query === "string") : []; } catch { return []; }
  });
  const exportAction = useAction();
  const [page, setPage] = useState(() => Math.max(1, Number(initial.get("page")) || 1));
  const [search, setSearch] = useState(() => {const params = new URLSearchParams(window.location.search); for (const key of ["document", "tab", "file", "block", "ordinal", "registry_id", "page", "cursor"]) params.delete(key); return "&" + params.toString();});
  useEffect(() => {
    const params = new URLSearchParams(search);
    if (registryId) params.set("registry_id", registryId);
    if (documentId) params.set("document", documentId);
    if (contentTarget) {params.set("tab", "content"); if (contentTarget.fileId) params.set("file", contentTarget.fileId); if (contentTarget.blockId) params.set("block", contentTarget.blockId); if (contentTarget.ordinal !== undefined) params.set("ordinal", String(contentTarget.ordinal));}
    if (cursor) params.set("cursor", cursor);
    if (page > 1) params.set("page", String(page));
    window.history.replaceState(null, "", window.location.pathname + (params.size ? "?" + params.toString() : ""));
  }, [search, registryId, documentId, contentTarget, cursor, page]);
  const searchTiming = useRef<{registryId: string; started: number} | null>(null);
  function recordRetrieval(operation: "search" | "time_to_open", duration_ms: number) {
    if (!registryId) return;
    void apiRequest("/usage/retrieval", {method: "POST", headers: {"X-CSRF-Token": auth.user?.csrf_token || ""}, body: JSON.stringify({registry_id: registryId, operation, duration_ms: Math.min(3600000, Math.max(0, Math.round(duration_ms)))})}).catch(() => {});
  }
  function startSearchTiming() {searchTiming.current = {registryId, started: performance.now()}; recordRetrieval("search", 0);}
  function openSearchResult(id: string, target?: ContentTarget) {
    if (id === documentId && !target) return true;
    if (dirtyCard.current && !window.confirm("В карточке есть несохранённые изменения. Перейти к другому документу или фрагменту без сохранения?")) return false;
    const timing = searchTiming.current;
    if (timing?.registryId === registryId) recordRetrieval("time_to_open", performance.now() - timing.started);
    searchTiming.current = null;
    window.history.pushState(null, "", window.location.href);
    setContentTarget(target); setDocumentId(id); setNavigationRevision(value => value + 1);
    return true;
  }
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
  const applied = searchBody(search, registryId, readOnly ? "viewer" : "editor", cursor);
  const list = useData<{items: ContentHit[]; total: number; page_size: number; next_cursor: string | null}>(
    !creating ? "/search" : null,
    {body: applied, csrfToken: auth.user?.csrf_token || "", retainOnReload: true},
  );
  const resultDocuments = (list.data?.items || []).map(result => "document" in result ? result.document : result as unknown as Card);
  const listSchemas = useDocumentSchemas(resultDocuments, auth.user?.id || "anonymous", readOnly, layout.mode !== "card");
  function sortResults(key: string) {
    const nextDirection = applied.sort === key && applied.direction === "asc" ? "desc" : "asc";
    setSort(key); setDirection(nextDirection);
    const next = new URLSearchParams(search); next.set("sort", key); next.set("direction", nextDirection);
    setSearch("&" + next.toString()); setCursor(""); setPage(1); setPreviousCursors([]);
  }
  function openFullResult(id: string) {if (openSearchResult(id)) chooseLayout("card");}
  const card = useData<Card>(
    documentId
      ? "/documents/" + documentId + "?mode=" + (readOnly ? "viewer" : "editor")
      : null,
  );
  useEffect(() => {
    if (!savedDocument) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function refresh() {
      try {
        const state = await apiRequest<{ready: boolean}>("/documents/" + savedDocument!.id + "/search-index", {signal: controller.signal});
        if (controller.signal.aborted) return;
        if (state.ready) {list.reload(); setSavedDocument(null);}
        else if (++attempts < 30) timer = setTimeout(refresh, 1000);
        else setSavedDocument(null);
      } catch { if (!controller.signal.aborted) setSavedDocument(null); }
    }
    void refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [savedDocument, list.reload]);
  function applySearch() {
    window.history.pushState(null, "", window.location.href);
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    params.set("scope", scope); params.set("fuzzy", String(fuzzy)); params.set("version_scope", versionScope);
    if (number) params.set("reg_number", number);
    if (registrationDate) params.set("reg_date", registrationDate);
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
    setPage(1); setCursor(""); setPreviousCursors([]);
    setSearch("&" + params.toString());
    list.reload();
    startSearchTiming();
  }
  function persistViews(next: typeof savedViews) {
    setSavedViews(next);
    try { localStorage.setItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`, JSON.stringify(next)); } catch { /* Browser storage can be disabled; current filters still work. */ }
  }
  function loadView(query: string) {
    const params = new URLSearchParams(query);
    setQ(params.get("q") || params.get("text") || ""); setNumber(params.get("reg_number") || ""); setRegistrationDate(params.get("reg_date") || ""); setStatus(params.get("status") || ""); setFrom(params.has("reg_date") ? "" : params.get("date_from") || ""); setTo(params.has("reg_date") ? "" : params.get("date_to") || ""); setTypeId(params.get("document_type_id") || ""); setSort(params.get("sort") || "auto"); setDirection(params.get("direction") || "desc");
    setScope((params.get("scope") as SearchScope) || "all"); setVersionScope(params.get("version_scope") || "current"); setFuzzy(params.get("fuzzy") !== "false");
    try { const [key, value] = Object.entries(JSON.parse(params.get("filters") || "{}"))[0] || ["", null]; setFilterKey(key); const typed = value as {op?: string; value?: unknown} | null; setFilterOp(typed && typeof typed === "object" ? typed.op || "eq" : "eq"); setFilterValue(typed && typeof typed === "object" ? (Array.isArray(typed.value) && typed.op === "between" ? typed.value[0] : typed.value) : value); setFilterEnd(typed?.op === "between" && Array.isArray(typed.value) ? String(typed.value[1]) : ""); } catch { setFilterKey(""); setFilterValue(null); }
    setPage(1); setCursor(""); setPreviousCursors([]); setSearch("&" + query);
    startSearchTiming();
  }
  useEffect(() => {
    const restore = () => {
      const params = new URLSearchParams(window.location.search);
      setRegistryId(params.get("registry_id") || ""); setDocumentId(params.get("document") || "");
      setContentTarget(params.get("tab") === "content" ? {fileId: params.get("file") || undefined, blockId: params.get("block") || undefined, ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : undefined} : undefined);
      setNavigationRevision(value => value + 1);
      const query = new URLSearchParams(params); for (const key of ["document", "tab", "file", "block", "ordinal", "registry_id", "page", "cursor"]) query.delete(key);
      loadView(query.toString()); setCursor(params.get("cursor") || ""); setPage(Math.max(1, Number(params.get("page")) || 1));
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  const close = () => {
    setDocumentId("");
    setContentTarget(undefined);
    setCreating(false);
  };
  const detail = documentId ? (
      <>
        <Feedback {...card} />
        {card.error && <button onClick={close}>К реестрам</button>}
        {card.data && (
          <DocumentCard
            key={card.data.id + ":" + card.data.revision + ":" + navigationRevision}
            card={card.data}
            readOnly={readOnly}
            references={references.data || []}
            onSaved={() => {card.reload(); setSavedDocument({id: card.data!.id, revision: card.data!.revision});}}
            onClose={backToResults}
            onDirtyChange={reportDirty}
            contentTarget={contentTarget}
            onNavigate={value => {openSearchResult(value.documentId, value.contentTarget);}}
          />
        )}
      </>
    ) : <div className="empty-state">Выберите документ, чтобы увидеть карточку, файлы и историю.</div>;
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
    <div className="documents-search documents-layout-page">
      <div className="toolbar documents-heading">
        <div>
          <span className="eyebrow">
            {readOnly ? "ПРОСМОТР ДОКУМЕНТОВ" : "РАБОТА С ДОКУМЕНТАМИ"}
          </span>
          <h1>{registry ? registry.name : "Документы"}</h1>
        </div>
        <div className="documents-heading-actions"><DocumentLayoutSwitch mode={layout.mode} onChange={chooseLayout}/>
        {!readOnly && registry?.permissions.includes("create") && (
          <button className="primary" onClick={() => setCreating(true)}>
            Создать документ
          </button>
        )}
        </div>
      </div>
      <Feedback
        error={registries.error || types.error || references.error}
        loading={registries.loading}
      />
      <div className="registry-tabs" aria-label="Быстрый фильтр по реестру"><button className={!registryId ? "active" : ""} onClick={() => {setRegistryId(""); setCursor(""); setPage(1);}}>Все реестры</button>
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
                setScope("all"); setFuzzy(true); setContentTarget(undefined); setCursor(""); setPreviousCursors([]);
                setNumber("");
                setRegistrationDate("");
                setStatus("");
                setFrom("");
                setTo("");
                setSort("auto");
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
      {(registry || registries.data?.some(item => item.permissions.includes("read"))) && (
        <>
          <details className="panel search-saved-views"><summary>Сохранённые фильтры и сортировки</summary>
            <div className="inline-form"><label>Название представления<input value={viewName} onChange={event => setViewName(event.target.value)} /></label><button disabled={!viewName.trim() || !search} onClick={() => {persistViews([...savedViews.filter(item => !(item.registry === registryId && item.name === viewName.trim())), {name: viewName.trim(), registry: registryId, query: search.slice(1)}]); setViewName("");}}>Сохранить текущий поиск</button></div>
            {savedViews.filter(item => item.registry === registryId).map(item => <div className="button-row" key={item.name}><button onClick={() => loadView(item.query)}>{item.name}</button><button aria-label={`Удалить представление: ${item.name}`} onClick={() => persistViews(savedViews.filter(current => current !== item))}>Удалить</button></div>)}
          </details>
          <form
            className="panel search-form"
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
                placeholder="Номер, название, реквизиты или фраза из файла"
                maxLength={255}
                onChange={(event) => setQ(event.target.value)}
              />
            </label>
            <label>Где искать<select value={scope} onChange={event => setScope(event.target.value as SearchScope)}><option value="all">Везде</option><option value="metadata">Реквизиты</option><option value="content">Содержание документов</option></select></label>
            <div className="button-row"><button className="primary">Найти</button><button type="button" onClick={() => {setQ(""); const next = new URLSearchParams(search); next.delete("q"); next.delete("text"); setSearch("&" + next.toString()); setCursor(""); setPage(1);}}>Очистить запрос</button><button type="button" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)}>Фильтры</button></div>
            <SearchStatus loading={list.loading} startedAt={list.startedAt} durationMs={list.durationMs} error={list.error}/>
            <div className="search-quick-filters">
            <label>
              Регистрационный номер
              <input
                value={number}
                onChange={(event) => setNumber(event.target.value)}
              />
            </label>
            <label>
              Дата регистрации
              <input type="date" value={registrationDate} onChange={event => {setRegistrationDate(event.target.value); if (event.target.value) {setFrom(""); setTo("");}}}/>
            </label>
            <label>
              Статус
              <input
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              />
            </label>
            <label>
              Дата регистрации с
              <input
                type="date"
                disabled={!!registrationDate}
                title={registrationDate ? "Очистите точную дату регистрации, чтобы выбрать период" : undefined}
                value={from}
                onChange={(event) => setFrom(event.target.value)}
              />
            </label>
            <label>
              Дата регистрации по
              <input
                type="date"
                disabled={!!registrationDate}
                title={registrationDate ? "Очистите точную дату регистрации, чтобы выбрать период" : undefined}
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
                  setFilterKey(""); setFilterOp("eq"); setFilterEnd(""); setSort("auto");
                }}
              >
                <option value="">Все типы</option>
                {types.data
                  ?.filter((type) =>
                    !registry || registry.document_type_ids.includes(type.id),
                  )
                  .map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.name}
                    </option>
                  ))}
              </select>
            </label>
            </div>
            <details open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}><summary>Расширенные фильтры</summary><div className="filters">
            <label className="checkbox-label"><input type="checkbox" checked={fuzzy} onChange={event => setFuzzy(event.target.checked)}/>Учитывать опечатки</label>
            <label>Версии файлов<select value={versionScope} onChange={event => setVersionScope(event.target.value)}><option value="current">Актуальные</option><option value="all">Все доступные</option></select></label>
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
            </div></details>
          </form>
          <div className="active-search-filters" aria-label="Активные фильтры">{Array.from(new URLSearchParams(search)).filter(([key, value]) => value && ["q", "text", "reg_number", "reg_date", "status", "date_from", "date_to", "document_type_id", "filters"].includes(key)).map(([key, value]) => <button key={key} type="button" aria-label={"Удалить фильтр " + key} onClick={() => {const next = new URLSearchParams(search); next.delete(key); loadView(next.toString());}}>{({q: "Запрос", text: "Запрос", reg_number: "Номер", reg_date: "Дата регистрации", status: "Статус", date_from: "С", date_to: "По", document_type_id: "Тип", filters: "Поля"} as Record<string, string>)[key]}: {key === "document_type_id" ? types.data?.find(item => item.id === value)?.name || "Тип документа" : key === "filters" ? "Задан фильтр поля" : value} ×</button>)}</div>
          <div className="search-sort">            <label>
              Сортировка
              <select
                value={sort}

                onChange={(event) => {setSort(event.target.value); const next = new URLSearchParams(search); next.set("sort", event.target.value); setSearch("&" + next.toString()); setCursor(""); setPage(1);}}
              >
                <option value="auto">Автоматически</option><option value="relevance">Релевантность</option><option value="updated_at">Последние изменения</option>
                <option value="reg_date">Дата регистрации</option>
                <option value="reg_number">Номер</option>
                <option value="title">Название</option>
                {filterFields.filter(field => !field.system && field.type !== "multi_reference").map(field => <option key={field.key} value={`custom:${field.key}`}>{field.label}</option>)}
              </select>
            </label>
            <label>Направление сортировки<select value={direction}  onChange={event => {setDirection(event.target.value); const next = new URLSearchParams(search); next.set("direction", event.target.value); setSearch("&" + next.toString()); setCursor(""); setPage(1);}}><option value="asc">По возрастанию</option><option value="desc">По убыванию</option></select></label>
</div>
          <Feedback error={list.error} />
          {!!applied.query && <p className="muted">Недавно загруженные оригиналы появляются в поиске после обработки. Состояние и ошибки доступны во вкладке «Содержание» карточки.</p>}
          <Feedback {...exportAction} />
          <div className="button-row">{(["csv", "xlsx"] as const).map(format => <button key={format} disabled={list.loading || exportAction.busy || (configuredColumns.length > 0 && !exportColumns.length)} onClick={() => void exportAction.run(() => saveExport(`/search/export?parameters=${encodeURIComponent(JSON.stringify({...applied, cursor: null}))}&format=${format}${exportColumns.length ? "&columns=" + encodeURIComponent(exportColumns.map(column => column.key).join(",")) : ""}`, `documents.${format}`), "Экспорт подготовлен")}>Экспорт {format.toUpperCase()}</button>)}</div>
          {configuredColumns.length > 0 && !exportColumns.length && <p className="muted">В выбранном представлении нет разрешённых для экспорта колонок.</p>}
          <DocumentWorkspace busy={list.loading} layout={layout} onChange={chooseLayout} onResize={resizeLayout} onRestore={restoreLayout}
            selectedLabel={card.data?.title || resultDocuments.find(item => item.id === documentId)?.title || "Карточка"}
            detail={detail}
            count={<div className="document-results-heading"><h2>Документы</h2><span className="muted">Найдено: {list.data?.total ?? "…"}</span></div>}
            list={<>
              <DocumentResults results={list.data?.items || []} schemas={listSchemas.byVersion} sortSchemas={listSchemas.forType(applied.document_type_ids.length === 1 ? applied.document_type_ids[0] || "" : "")} types={types.data || []}
                roles={auth.user?.roles || []} readOnly={readOnly} selected={documentId} query={applied.query}
                sort={applied.sort} direction={applied.direction} typeId={applied.document_type_ids.length === 1 ? applied.document_type_ids[0] || "" : ""}
                onSort={sortResults} onSelect={openSearchResult} onOpen={openFullResult}/>
          {list.data?.items.length === 0 && (
            <div className="empty-state">
              Документов по выбранным условиям пока нет.
            </div>
          )}
          <div className="pagination">
            <button
              disabled={page === 1 || list.loading}
              onClick={() => {setCursor(previousCursors.at(-1) || ""); setPage(previousCursors.length ? page - 1 : 1); setPreviousCursors(values => values.slice(0, -1));}}
            >
              Назад
            </button>
            <span>Страница {page}</span>
            <button
              disabled={
                !list.data?.next_cursor || list.loading
              }
              onClick={() => {setPreviousCursors(values => [...values, cursor]); setCursor(list.data?.next_cursor || ""); setPage(page + 1);}}
            >
              Вперёд
            </button>
          </div>
            </>}/>
        </>
      )}
    </div>
  );
}
