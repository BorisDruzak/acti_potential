import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError, apiRequest, apiUpload, encodeFile, saveExport, saveSelectedExport } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { SearchStatus } from "../components/SearchStatus";
import { DynamicForm } from "../engine/DynamicForm";
import { sameFormValue } from "../engine/formChanges";
import {
  initialValues,
  prunePendingFiles,
  type Card,
  type CardState,
  type PublicationTarget,
  type DocumentType,
  type Field,
  type FileVersion,
  type ReferenceItem,
  type ReferenceSet,
  type Registry,
  type SchemaVersion,
  type Values,
} from "../engine/schema";
import { searchBody, type SearchScope } from "../engine/search";
import { useAction, useData } from "../engine/useData";
import { useRegistrationNumber } from "../engine/useRegistrationNumber";
import { DocumentContent } from "./DocumentContent";
import { DocumentLayoutSwitch, DocumentWorkspace, useDocumentLayout } from "../components/DocumentWorkspace";
import { DocumentResults } from "../components/DocumentResults";
import { DocumentDialog } from "../components/DocumentDialog";
import { DocumentContextMenu } from "../components/DocumentContextMenu";
import { DocumentFieldFilters, parseFieldFilters, serializeFieldFilters } from "../components/DocumentFieldFilters";
import { FileActions, FileRevisionHistory, isOfficeSyncSource } from "../components/OfficeSync";
import { OriginalActions } from "../components/OriginalActions";
import { useDocumentSelection } from "../engine/useDocumentSelection";
import { useDocumentCollaboration } from "../engine/useDocumentCollaboration";
import { useDocumentSchemas } from "../engine/documentPresentation";
import { useDocumentFeed } from "../engine/useDocumentFeed";
import { useDocumentFilterCatalog } from "../engine/documentFilterCatalog";
import { DocumentFeedEnd } from "../components/DocumentFeedEnd";
import { DocumentCardState, DocumentPublication, mainInformationSection, publicationSection } from "../components/DocumentPublication";
import type { ContentTarget } from "../engine/content";

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

function useParticipantCreation(registryId: string, schemaVersionId: string, csrfToken: string, documentId?: string) {
  const pending = useRef(0);
  const [count, setCount] = useState(0);
  const create = useCallback(async (field: Field, label: string) => {
    pending.current += 1;
    setCount(current => current + 1);
    try {
      return await apiRequest<{ item: ReferenceItem; created: boolean }>(`/registries/${registryId}/people`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ schema_version_id: schemaVersionId, field_key: field.key, label, ...(documentId ? { document_id: documentId } : {}) }),
      });
    } finally {
      pending.current -= 1;
      setCount(current => current - 1);
    }
  }, [registryId, schemaVersionId, csrfToken, documentId]);
  return { create, pending, busy: count > 0 };
}

function DocumentCard({
  card,
  readOnly,
  references,
  onSaved,
  onClose,
  contentTarget,
  registryName,
  onDirtyChange,
}: {
  card: Card;
  readOnly: boolean;
  references: ReferenceSet[];
  onSaved: () => void;
  onClose: () => void;
  contentTarget?: ContentTarget;
  registryName?: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const auth = useAuth();
  const action = useAction();
  const canEdit = !readOnly && card.permissions.includes("update");
  const formId = useId();
  const dock = useRef<HTMLDivElement>(null);
  const [values, setValues] = useState<Values>(card.values);
  const [savedValues, setSavedValues] = useState<Values>(card.values);
  const [cardState, setCardState] = useState<CardState>(card.card_state || "draft");
  const [targets, setTargets] = useState<PublicationTarget[]>(card.publication_targets || ["internal"]);
  const [savedTargets, setSavedTargets] = useState<PublicationTarget[]>(card.publication_targets || ["internal"]);
  const publicationKey = publicationSection(card.schema);
  const mainKey = mainInformationSection(card.schema);
  const lifecycle = <DocumentCardState state={cardState}/>;
  const publicationDirty = !sameFormValue([...targets].sort(), [...savedTargets].sort());
  const publication = <div className={canEdit && publicationDirty ? "field-dirty publication-dirty" : undefined} data-dirty={canEdit && publicationDirty || undefined}><DocumentPublication targets={targets} onTargets={canEdit && !action.busy ? setTargets : undefined}/>{canEdit && publicationDirty && <small className="field-unsaved">Не сохранено</small>}</div>;
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
      created_by_name?: string;
      card_state?: CardState | null;
      publication_targets?: PublicationTarget[] | null;
    }[]
  >(
    tab === "history"
      ? "/documents/" +
          card.id +
          "/history?mode=" +
          (readOnly ? "viewer" : "editor")
      : null,
  );
  const canUpload = !readOnly && card.permissions.includes("upload");
  const csrfToken = auth.user?.csrf_token || "";
  const participantCreation = useParticipantCreation(card.registry_id, card.schema_version_id, csrfToken, card.id);
  const registrationNumber = useRegistrationNumber({schema:card.schema,values,roles:auth.user?.roles||[],mode:"edit",
    registryId:card.registry_id,documentTypeId:card.document_type_id,schemaVersionId:card.schema_version_id,
    documentId:card.id,revision,csrfToken,locked:cardState==="published",disabled:action.busy,onValues:changeValues});
  const hasUnsavedChanges =
    canEdit &&
    (!sameFormValue(values, savedValues) || publicationDirty ||
      Object.keys(pendingFiles).length > 0 || registrationNumber.busy);
  const collaboration = useDocumentCollaboration(card.id, revision, readOnly ? "viewer" : "editor", csrfToken, hasUnsavedChanges, hasUnsavedChanges, onSaved);
  const hasCollaboration = !!collaboration.state;
  useLayoutEffect(() => {
    const node = dock.current;
    if (!node) return;
    const measure = () => node.parentElement?.style.setProperty("--document-card-dock-height", `${Math.ceil(node.getBoundingClientRect().height)}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    window.addEventListener("resize", measure);
    return () => {observer?.disconnect(); window.removeEventListener("resize", measure);};
  }, [hasCollaboration]);
  const remoteChange = hasUnsavedChanges ? collaboration.remote : null;
  const blocked = conflict || !!remoteChange || collaboration.unavailable;
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
  async function save(targetState: CardState = cardState) {
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
          card_state: targetState,
          publication_targets: targets,
          values: registrationNumber.serialize(),
          ...(registrationNumber.numberEntryId ? {number_entry_id:registrationNumber.numberEntryId} : {}),
          ...(files.length ? { files } : {}),
        },
        60000,
      );
      setRevision(current.revision);
      setCardState(current.card_state || "draft");
      setValues(current.values); setSavedValues(current.values);
      setTargets(current.publication_targets || ["internal"]); setSavedTargets(current.publication_targets || ["internal"]);
      setPendingFiles({});
      onSaved();
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "revision_conflict")
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
      <div className="document-card-top">
      <div className="toolbar document-card-heading">
        <div>
          <div className="document-card-summary"><span className="eyebrow">{registryName} · {card.reg_number ? `№ ${card.reg_number}` : "БЕЗ НОМЕРА"}</span>
          <span className={`document-state ${cardState}`}>{cardState === "published" ? "Опубликован" : "Черновик"}</span></div>
          <h1 title={card.title}>{card.title || "Документ"}</h1>
        </div>
        <button onClick={onClose}>К списку</button>
      </div>
      </div>
      <div className="document-card-dock" ref={dock}>
      <div className="document-actions-strip" role="group" aria-label="Действия с документом">
          {canEdit && (
            <div className="button-row document-save-actions">
              {canEdit ? (
                <>
                  <button
                    key="save-card"
                    type="submit" form={formId}
                    onClick={() => setTab("card")}
                    className="primary"
                    disabled={!hasUnsavedChanges || action.busy || blocked || participantCreation.busy || registrationNumber.busy}
                  >
                    Сохранить карточку
                  </button>
                  {cardState !== "published" && <button type="submit" form={formId} onClick={() => setTab("card")} className="primary" data-action="publish"
                    disabled={action.busy || blocked || participantCreation.busy || registrationNumber.busy}>Опубликовать</button>}
                  <button
                    key="cancel-card-edit"
                    type="button"
                    disabled={action.busy || participantCreation.busy || registrationNumber.busy}
                    onClick={() => {
                      setValues(savedValues);
                      registrationNumber.reset();
                      setTargets(savedTargets);
                      setPendingFiles({});
                    }}
                  >
                    Отмена изменений
                  </button>
                  <span className={`document-save-status${hasUnsavedChanges ? " unsaved" : ""}`} aria-live="polite">{hasUnsavedChanges ? "Есть несохранённые изменения" : "Все изменения сохранены"}</span>
                </>
              ) : null}
            </div>
          )}
      {(!!card.files?.length || canUpload) && <section className="document-original-panel" aria-label="Оригинал документа">
        <div className="document-original-heading">
          <svg className="document-original-icon" width="20" height="24" viewBox="0 0 20 24" fill="none" aria-hidden="true"><path d="M3 1h9l5 5v16H3V1Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M12 1v6h5M6 12h8M6 16h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
          <div><strong>Оригинал документа</strong>{!card.files?.length && <small>Файлы пока не добавлены</small>}</div>
        </div>
        <div className="document-header-actions">
          <OriginalActions files={card.files || []} canDownload={card.permissions.includes("download")} mode={readOnly ? "viewer" : "editor"} csrfToken={csrfToken}/>
        </div>
      </section>}
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
      </div>
      {collaboration.state && <div className="document-collaboration" aria-label="Совместная работа">
        <p>Последнее изменение: {collaboration.state.updated_by?.display_name || "Автор неизвестен"} · {new Date(collaboration.state.updated_at).toLocaleString("ru-RU")}</p>
        {collaboration.state.participants.length > 0 && <p>Сейчас в карточке: {collaboration.state.participants.map(person => `${person.display_name}${person.editing ? " (редактирует)" : " (просматривает)"}`).join(", ")}</p>}
      </div>}
      </div>
      <Feedback {...action} />
      {collaboration.unavailable && <div className="error" role="alert">Документ удалён или доступ к нему изменён. Обновите список документов.</div>}
      {(conflict || remoteChange) && (
        <div className="error" role="alert">
          <p>
            Документ изменён другим пользователем. Ваши изменения ещё не
            сохранены.
            {remoteChange && <> Последняя редакция {remoteChange.revision}: {remoteChange.updated_by?.display_name || "другой пользователь"}, {new Date(remoteChange.updated_at).toLocaleString("ru-RU")}. Локальные изменения сохранены в форме.</>}
          </p>
          <button
            onClick={() => {
              if (hasUnsavedChanges && !window.confirm("Загрузить актуальную редакцию и отменить ваши несохранённые изменения?")) return;
              setConflict(false);
              onSaved();
            }}
          >
            Загрузить актуальную редакцию
          </button>
        </div>
      )}
      {(
        <form
          id={formId}
          hidden={tab !== "card"}
          className="panel document-card-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!canEdit || participantCreation.pending.current > 0) return;
            const publish=(event.nativeEvent as SubmitEvent).submitter?.getAttribute("data-action")==="publish";
            if (!action.busy && !blocked && !registrationNumber.pending.current)
              void action.run(()=>save(publish?"published":cardState),publish?"Карточка опубликована":"Изменения сохранены");
          }}
        >
          <fieldset className="document-fields" disabled={action.busy}>
          <DynamicForm
            sectionContent={{...(mainKey ? {[mainKey]: lifecycle} : {}), ...(publicationKey ? {[publicationKey]: publication} : {})}}
            navigationPlacement="side"
            compactRegistration
            schema={card.schema}
            fieldContent={canEdit ? registrationNumber.fieldContent : undefined}
            values={values}
            savedValues={savedValues}
            dirtyFileFields={new Set(Object.keys(pendingFiles))}
            onChange={changeValues}
            mode={canEdit ? "edit" : "view"}
            displayMode={readOnly ? "viewer" : "editor"}
            roles={auth.user?.roles || []}
            references={references}
            registryId={card.registry_id}
            onCreateReference={canEdit && !action.busy ? participantCreation.create : undefined}
            files={card.files}
            canDownload={card.permissions.includes("download")}
            officeSync={{csrfToken}}
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
          {!mainKey && <section className="form-section"><h3>Основные сведения</h3>{lifecycle}</section>}
          {!publicationKey && <section className="form-section"><h3>Публикация</h3>{publication}</section>}
          {Object.entries(pendingFiles).map(([field, file]) => (
            <div className="file-line" key={field}>
              <span>{file.name}</span>
              <small>
                Будет загружен при сохранении карточки
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
          </fieldset>
        </form>
      )}
      {tab === "files" && (
        <section className="panel">
          <h2>Оригиналы и вложения</h2>
          {canUpload && (
            <label className="upload-area">
              Добавить файл
              <input
                type="file"
                disabled={action.busy || blocked || hasUnsavedChanges}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file)
                    void action.run(() => upload("", file), "Файл загружен");
                  event.target.value = "";
                }}
              />

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
                </span>
                {card.permissions.includes("download") && <FileActions file={file} mode={readOnly ? "viewer" : "editor"} csrfToken={csrfToken}/>}
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
          {card.permissions.includes("download") && card.files?.some(file => isOfficeSyncSource(file.name)) && <>
            <h2>История файлов</h2>
            {card.files.filter(file => isOfficeSyncSource(file.name)).map(file => <FileRevisionHistory key={file.id} file={file} mode={readOnly ? "viewer" : "editor"}/>)}
          </>}
          <h2>История реквизитов</h2>
          <Feedback {...history} />
          {history.data?.map((item) => (
            <details key={item.id}>
              <summary>
                Редакция {item.revision} ·{" "}
                {new Date(item.created_at).toLocaleString("ru-RU")} · {item.created_by_name || "Автор неизвестен"}
              </summary>
              {item.card_state && item.publication_targets ? <><DocumentCardState state={item.card_state}/><DocumentPublication targets={item.publication_targets}/></> : <p className="muted">Состояние карточки и базы публикации в этой редакции неизвестны.</p>}
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
    </section>
  );
}

function CreateDocument({
  registries,
  onRegistryChange,
  onBeforeCreate,
  onDirtyChange,
  registry,
  types,
  references,
  onCreated,
  onClose,
}: {
  registries: Registry[];
  onRegistryChange: (id: string) => void;
  onBeforeCreate: () => boolean;
  onDirtyChange: (dirty: boolean) => void;
  registry: Registry;
  types: DocumentType[];
  references: ReferenceSet[];
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const auth = useAuth();
  const [typeId, setTypeId] = useState("");
  const draftDirty = useRef(false);
  const reportDirty = useCallback((dirty: boolean) => {draftDirty.current = dirty; onDirtyChange(dirty);}, [onDirtyChange]);
  const versions = useData<SchemaVersion[]>(
    typeId ? "/document-types/" + typeId + "/versions?mode=editor" : null,
  );
  const version = versions.data?.find((item) => item.state === "published");
  return (
    <section className="document-card document-create">
      <div className="toolbar">
        <div>
          <h1>Новый документ</h1>
          <p>Реестр: {registry.name}</p>
        </div>
        <button onClick={onClose}>Отмена создания</button>
      </div>
      <label className="picker">Реестр документа<select value={registry.id} onChange={event => onRegistryChange(event.target.value)}>{registries.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="picker">
        Тип документа
        <select
          value={typeId}
          onChange={event => {if (!draftDirty.current || window.confirm("В новом документе есть несохранённые изменения. Сменить тип документа?")) setTypeId(event.target.value);}}
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
          onBeforeCreate={onBeforeCreate}
          onDirtyChange={reportDirty}
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
  onBeforeCreate,
  onDirtyChange,
}: {
  onBeforeCreate: () => boolean;
  onDirtyChange: (dirty: boolean) => void;
  registry: Registry;
  version: SchemaVersion;
  references: ReferenceSet[];
  roles: string[];
  onCreated: (id: string) => void;
}) {
  const auth = useAuth();
  const action = useAction();
  const csrfToken = auth.user?.csrf_token || "";
  const participantCreation = useParticipantCreation(registry.id, version.id, csrfToken);
  const [values, setValues] = useState<Values>(() =>
    initialValues(version.schema),
  );
  const [pendingFiles, setPendingFiles] = useState<Record<string, File>>({});
  const cardState: CardState = "draft";
  const [targets, setTargets] = useState<PublicationTarget[]>(["internal"]);
  const registrationNumber=useRegistrationNumber({schema:version.schema,values,roles,mode:"create",
    registryId:registry.id,documentTypeId:version.document_type_id,schemaVersionId:version.id,csrfToken,
    disabled:action.busy,onValues:changeValues});
  const publicationKey = publicationSection(version.schema);
  const mainKey = mainInformationSection(version.schema);
  const lifecycle = <DocumentCardState state={cardState}/>;
  const publication = <DocumentPublication targets={targets} onTargets={setTargets}/>;
  const dirty = JSON.stringify(values) !== JSON.stringify(initialValues(version.schema)) || cardState !== "draft" || JSON.stringify(targets) !== '["internal"]' || Object.keys(pendingFiles).length > 0 || registrationNumber.busy;
  useEffect(() => {onDirtyChange(dirty);}, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  function changeValues(next: Values) {
    setPendingFiles((current) =>
      prunePendingFiles(current, version.schema, next, roles, "create", values),
    );
    setValues(next);
  }
  async function create(targetState: CardState = "draft") {
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
        card_state: targetState,
        publication_targets: targets,
        registry_id: registry.id,
        document_type_id: version.document_type_id,
        schema_version_id: version.id,
        values: registrationNumber.serialize(),
        ...(registrationNumber.numberEntryId ? {number_entry_id:registrationNumber.numberEntryId} : {}),
        ...(files.length ? { files } : {}),
      },
      60000,
    );
    onCreated(card.id);
  }
  return (
    <form
      className="panel document-card-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (participantCreation.pending.current > 0 || registrationNumber.pending.current) return;
        const publish=(event.nativeEvent as SubmitEvent).submitter?.getAttribute("data-action")==="publish";
        if (!action.busy && onBeforeCreate()) void action.run(()=>create(publish?"published":"draft"),publish?"Карточка опубликована":"Документ создан");
      }}
    >
      <DynamicForm
        navigationPlacement="side"
        compactRegistration
        sectionContent={{...(mainKey ? {[mainKey]: lifecycle} : {}), ...(publicationKey ? {[publicationKey]: publication} : {})}}
        schema={version.schema}
        fieldContent={registrationNumber.fieldContent}
        values={values}
        onChange={changeValues}
        mode="create"
        roles={roles}
        references={references}
        registryId={registry.id}
        onCreateReference={registry.permissions.includes("create") && !action.busy ? participantCreation.create : undefined}
        onFile={
          registry.permissions.includes("upload")
            ? (field, file) =>
                setPendingFiles((current) => ({ ...current, [field]: file }))
            : undefined
        }
      />
      {!mainKey && <section className="form-section"><h3>Основные сведения</h3>{lifecycle}</section>}
      {!publicationKey && <section className="form-section"><h3>Публикация</h3>{publication}</section>}
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
      <div className="button-row document-save-actions"><button type="submit" className="primary" disabled={action.busy || participantCreation.busy || registrationNumber.busy}>
        {action.busy ? "Сохранение…" : "Создать документ"}
      </button>
      <button type="submit" className="primary" data-action="publish" disabled={action.busy || participantCreation.busy || registrationNumber.busy}>Опубликовать</button></div>
    </form>
  );
}

export function Documents({ readOnly: requestedReadOnly = false }: { readOnly?: boolean }) {
  const auth = useAuth();
  const readOnly = requestedReadOnly || !auth.user?.roles.some(role => ["admin", "editor"].includes(role));
  const {layout, choose: chooseLayout, resize: resizeLayout, restore: restoreLayout, back: backToResults} = useDocumentLayout(auth.user?.id || "anonymous", readOnly);
  const dirtyCard = useRef(false);
  const dirtyCreation = useRef(false);
  const reportCreationDirty = useCallback((value: boolean) => {dirtyCreation.current = value;}, []);
  const priorCreateLayout = useRef<typeof layout | null>(null);
  const reportDirty = useCallback((value: boolean) => {dirtyCard.current = value;}, []);
  useEffect(() => {
    const beforeNavigation = (event: Event) => {
      if ((dirtyCard.current || dirtyCreation.current) && !window.confirm("Есть несохранённые изменения документа. Перейти в другой раздел без сохранения?")) event.preventDefault();
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyCard.current || dirtyCreation.current) {event.preventDefault(); event.returnValue = "";}
    };
    window.addEventListener("acti:before-workspace-navigation", beforeNavigation);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {window.removeEventListener("acti:before-workspace-navigation", beforeNavigation); window.removeEventListener("beforeunload", beforeUnload);};
  }, []);
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
  const [createRegistryId, setCreateRegistryId] = useState("");
  const [q, setQ] = useState(() => initial.get("q") || initial.get("text") || "");
  const [scope, setScope] = useState<SearchScope>(() => (initial.get("scope") as SearchScope) || "all");
  const [versionScope, setVersionScope] = useState(() => initial.get("version_scope") || "current");
  const [searchOpen, setSearchOpen] = useState(false);
  const [draftRegistryId, setDraftRegistryId] = useState(registryId);
  const priorSearchDraft = useRef("");
  function openAdvancedSearch() {
    priorSearchDraft.current = searchDraft();
    setDraftRegistryId(registryId); setSearchOpen(true);
  }
  function closeAdvancedSearch() {
    restoreSearchDraft(priorSearchDraft.current); setSearchOpen(false);
  }
  const [fuzzy, setFuzzy] = useState(() => initial.get("fuzzy") !== "false");
  const [contentTarget, setContentTarget] = useState<ContentTarget | undefined>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("tab") === "content" ? {fileId: params.get("file") || undefined, blockId: params.get("block") || undefined, ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : undefined} : undefined;
  });
  const [number, setNumber] = useState(() => initial.get("reg_number") || "");
  const [registrationDate, setRegistrationDate] = useState(() => initial.get("reg_date") || "");
  const [status, setStatus] = useState(() => initial.get("card_state") || "");
  const [legalStatus, setLegalStatus] = useState(() => initial.get("status") || "");
  const [publicationTarget, setPublicationTarget] = useState(() => initial.get("publication_target") || "");
  const [from, setFrom] = useState(() => initial.has("reg_date") ? "" : initial.get("date_from") || "");
  const [to, setTo] = useState(() => initial.has("reg_date") ? "" : initial.get("date_to") || "");
  const [typeId, setTypeId] = useState(() => initial.get("document_type_id") || "");
  const [fieldFilters, setFieldFilters] = useState(() => parseFieldFilters(initial.get("filters")));
  const [sort, setSort] = useState(() => initial.get("sort") || "auto");
  const [direction, setDirection] = useState(() => initial.get("direction") || "desc");
  const [viewName, setViewName] = useState("");
  const [savedViews, setSavedViews] = useState<{name: string; registry: string; query: string}[]>(() => {
    try { const data = JSON.parse(localStorage.getItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`) || "[]"); return Array.isArray(data) ? data.filter(item => typeof item?.name === "string" && typeof item?.registry === "string" && typeof item?.query === "string") : []; } catch { return []; }
  });
  const exportAction = useAction();
  const [search, setSearch] = useState(() => {const params = new URLSearchParams(window.location.search); for (const key of ["document", "tab", "file", "block", "ordinal", "registry_id", "page", "cursor"]) params.delete(key); return "&" + params.toString();});
  const documentLocation = useRef(window.location.pathname + window.location.search);
  const acceptedLocation = useRef(window.location.pathname + window.location.search + window.location.hash);
  const cardNavigation = useRef("");
  cardNavigation.current = JSON.stringify([window.location.pathname, documentId, !!contentTarget, contentTarget?.fileId, contentTarget?.blockId, contentTarget?.ordinal]);
  useEffect(() => {
    const params = new URLSearchParams(search);
    if (registryId) params.set("registry_id", registryId);
    if (documentId) params.set("document", documentId);
    if (contentTarget) {params.set("tab", "content"); if (contentTarget.fileId) params.set("file", contentTarget.fileId); if (contentTarget.blockId) params.set("block", contentTarget.blockId); if (contentTarget.ordinal !== undefined) params.set("ordinal", String(contentTarget.ordinal));}
    window.history.replaceState(null, "", window.location.pathname + (params.size ? "?" + params.toString() : ""));
    documentLocation.current = window.location.pathname + window.location.search;
    acceptedLocation.current = window.location.pathname + window.location.search + window.location.hash;
  }, [search, registryId, documentId, contentTarget]);
  const searchTiming = useRef<{registryId: string; started: number} | null>(null);
  function recordRetrieval(operation: "search" | "time_to_open", duration_ms: number) {
    if (!registryId) return;
    void apiRequest("/usage/retrieval", {method: "POST", headers: {"X-CSRF-Token": auth.user?.csrf_token || ""}, body: JSON.stringify({registry_id: registryId, operation, duration_ms: Math.min(3600000, Math.max(0, Math.round(duration_ms)))})}).catch(() => {});
  }
  function startSearchTiming() {searchTiming.current = {registryId, started: performance.now()}; recordRetrieval("search", 0);}
  function openSearchResult(id: string, target?: ContentTarget) {
    if (!creating && id === documentId && !target) return true;
    if ((dirtyCard.current || (creating && dirtyCreation.current)) && !window.confirm("В карточке есть несохранённые изменения. Перейти к другому документу или фрагменту без сохранения?")) return false;
    const timing = searchTiming.current;
    if (timing?.registryId === registryId) recordRetrieval("time_to_open", performance.now() - timing.started);
    searchTiming.current = null;
    window.history.pushState(null, "", window.location.href);
    setCreating(false);
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
  const permittedTypeIds = [...new Set((registries.data || []).filter(item => item.permissions.includes("read") && (!(searchOpen ? draftRegistryId : registryId) || item.id === (searchOpen ? draftRegistryId : registryId))).flatMap(item => item.document_type_ids || []))];
  const filterCatalog = useDocumentFilterCatalog(typeId ? permittedTypeIds.filter(id => id === typeId) : permittedTypeIds, types.data || [], auth.user?.id || "anonymous", auth.user?.roles || [], readOnly);
  const filterFields = filterCatalog.fields;
  const applied = searchBody(search, registryId, readOnly ? "viewer" : "editor");
  const list = useDocumentFeed(applied, auth.user?.csrf_token || "");
  const resultDocuments = (list.data?.items || []).map(result => "document" in result ? result.document : result as unknown as Card);
  const listSchemas = useDocumentSchemas(resultDocuments, auth.user?.id || "anonymous", readOnly, layout.mode !== "card");
  const selection = useDocumentSelection(resultDocuments.map(item => item.id));
  const [contextPoint, setContextPoint] = useState<{x: number; y: number} | null>(null);
  const closeContext = useCallback(() => setContextPoint(null), []);
  const selectedRows = resultDocuments.filter(item => selection.ids.has(item.id));
  const selectionAction = useAction();
  const [deleteCards, setDeleteCards] = useState<Card[] | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [originalCard, setOriginalCard] = useState<Card | null>(null);
  const displayMode = readOnly ? "viewer" : "editor";
  useEffect(() => {
    const timer = setInterval(() => {if (document.visibilityState !== "hidden") list.reload();}, 5000);
    return () => clearInterval(timer);
  }, [list.reload]);
  async function exportSelected(format: "csv" | "xlsx") {
    if (!selectedRows.length) return;
    await saveSelectedExport(selectedRows.map(item => item.id), format, exportColumns.length ? exportColumns.map(column => column.key) : undefined, displayMode, auth.user?.csrf_token || "");
  }
  async function original() {
    const item = selectedRows[0]; if (!item) return;
    const document = await apiRequest<Card>(`/documents/${item.id}?mode=${displayMode}`);
    if (!document.permissions.includes("download")) throw new Error("Скачивание оригиналов этого документа недоступно.");
    setOriginalCard(document);
  }
  async function prepareDelete() {
    if (readOnly || !selectedRows.length) return;
    const cards = await Promise.all(selectedRows.map(item => apiRequest<Card>(`/documents/${item.id}?mode=editor`)));
    if (cards.some(item => !item.permissions.includes("update"))) throw new Error("Недостаточно прав для удаления выбранных документов.");
    setDeleteReason(""); setDeleteCards(cards);
  }
  async function deleteSelected() {
    if (!deleteCards || !deleteReason.trim()) return;
    // A failure leaves the remaining cards and their original revisions in the dialog.
    for (const item of deleteCards) {
      await selectionAction.request(`/documents/${item.id}`, "DELETE", {revision: item.revision, reason: deleteReason.trim()});
      setDeleteCards(current => current?.filter(card => card.id !== item.id) || null);
      if (item.id === documentId) {dirtyCard.current = false; setDocumentId(""); setContentTarget(undefined);}
    }
    setDeleteCards(null); selection.clear(); list.reload();
  }
  async function copySelected() {
    await navigator.clipboard.writeText(selectedRows.map(item => [item.reg_number, item.title].filter(Boolean).join(" — ")).join("\n"));
  }

  function sortResults(key: string) {
    const nextDirection = applied.sort === key && applied.direction === "asc" ? "desc" : "asc";
    setSort(key); setDirection(nextDirection);
    const next = new URLSearchParams(search); next.set("sort", key); next.set("direction", nextDirection);
    setSearch("&" + next.toString());
  }
  function changeRegistry(id: string) {
    const appliedQuery = new URLSearchParams(search).get("q") || new URLSearchParams(search).get("text");
    const next = new URLSearchParams();
    if (appliedQuery) next.set("q", appliedQuery);
    setRegistryId(id); setDraftRegistryId(id); setSearch(next.size ? "&" + next.toString() : ""); setScope("all"); setFuzzy(true); setContentTarget(undefined);
    setNumber(""); setRegistrationDate(""); setStatus(""); setFrom(""); setTo(""); setSort("auto"); setDirection("desc");
    setLegalStatus(""); setPublicationTarget("");
    setTypeId(""); setFieldFilters([]);
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
  function searchDraft() {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    params.set("scope", scope); params.set("fuzzy", String(fuzzy)); params.set("version_scope", versionScope);
    if (number) params.set("reg_number", number);
    if (registrationDate) params.set("reg_date", registrationDate);
    if (status) params.set("card_state", status);
    if (publicationTarget) params.set("publication_target", publicationTarget);
    if (from) params.set("date_from", from);
    if (to) params.set("date_to", to);
    if (typeId) params.set("document_type_id", typeId);
    params.set("sort", sort);
    params.set("direction", direction);
    const predicates = serializeFieldFilters(fieldFilters);
    if (Object.keys(predicates).length) params.set("filters", JSON.stringify(predicates));
    // The legacy legal-act status is distinct from the card publication state.
    if (legalStatus) params.set("status", legalStatus);

    return params.toString();
  }
  function applySearch(nextRegistry = registryId) {
    setRegistryId(nextRegistry);
    window.history.pushState(null, "", window.location.href);
    const nextSearch = "&" + searchDraft();
    if (nextSearch === search) list.retry();
    setSearch(nextSearch);
    startSearchTiming();
  }
  function persistViews(next: typeof savedViews) {
    setSavedViews(next);
    try { localStorage.setItem(`acti.views.v1.${auth.user?.id}.${readOnly ? "viewer" : "editor"}`, JSON.stringify(next)); } catch { /* Browser storage can be disabled; current filters still work. */ }
  }
  function restoreSearchDraft(query: string) {
    const params = new URLSearchParams(query);
    params.delete("page"); params.delete("cursor");
    setQ(params.get("q") || params.get("text") || ""); setNumber(params.get("reg_number") || ""); setRegistrationDate(params.get("reg_date") || ""); setStatus(params.get("card_state") || ""); setFrom(params.has("reg_date") ? "" : params.get("date_from") || ""); setTo(params.has("reg_date") ? "" : params.get("date_to") || ""); setTypeId(params.get("document_type_id") || ""); setSort(params.get("sort") || "auto"); setDirection(params.get("direction") || "desc");
    setScope((params.get("scope") as SearchScope) || "all"); setVersionScope(params.get("version_scope") || "current"); setFuzzy(params.get("fuzzy") !== "false");
    setFieldFilters(parseFieldFilters(params.get("filters")));
    setPublicationTarget(params.get("publication_target") || "");
    setLegalStatus(params.get("status") || "");
  }
  function loadView(query: string) {
    restoreSearchDraft(query);
    const params = new URLSearchParams(query);
    params.delete("page"); params.delete("cursor");
    if ("&" + params.toString() === search) list.retry();
    setSearch("&" + params.toString());
    startSearchTiming();
  }
  useEffect(() => {
    const restore = (event: PopStateEvent & {actiDocumentGuarded?: boolean}) => {
      const location = window.location.pathname + window.location.search;
      // Section anchors also emit popstate. Keep the mounted card and draft for hash-only navigation.
      if (location === documentLocation.current) {acceptedLocation.current = location + window.location.hash; return;}
      const params = new URLSearchParams(window.location.search);
      const target = params.get("tab") === "content" ? {fileId: params.get("file") || undefined, blockId: params.get("block") || undefined, ordinal: params.has("ordinal") ? Number(params.get("ordinal")) : undefined} : undefined;
      const replacingCard = cardNavigation.current !== JSON.stringify([window.location.pathname, params.get("document") || "", !!target, target?.fileId, target?.blockId, target?.ordinal]);
      if (replacingCard && (dirtyCard.current || dirtyCreation.current) && !window.confirm("Есть несохранённые изменения документа. Перейти по истории без сохранения?")) {
        window.history.pushState(null, "", acceptedLocation.current);
        event.stopImmediatePropagation();
        return;
      }
      event.actiDocumentGuarded = true;
      documentLocation.current = location; acceptedLocation.current = location + window.location.hash;
      setRegistryId(params.get("registry_id") || ""); setDocumentId(params.get("document") || "");
      setContentTarget(target);
      if (replacingCard) {setCreating(false); setNavigationRevision(value => value + 1);}
      const query = new URLSearchParams(params); for (const key of ["document", "tab", "file", "block", "ordinal", "registry_id", "page", "cursor"]) query.delete(key);
      loadView(query.toString());
    };
    window.addEventListener("popstate", restore, true);
    return () => window.removeEventListener("popstate", restore, true);
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
            registryName={registries.data?.find(item => item.id === card.data?.registry_id)?.name || "Реестр"}
            readOnly={readOnly}
            references={references.data || []}
            onSaved={() => {card.reload(); setSavedDocument({id: card.data!.id, revision: card.data!.revision});}}
            onClose={backToResults}
            onDirtyChange={reportDirty}
            contentTarget={contentTarget}
          />
        )}
      </>
    ) : <div className="empty-state">Выберите документ, чтобы увидеть карточку, файлы и историю.</div>;
  const createRegistries = (registries.data || []).filter(item => item.permissions.includes("create"));
  const createRegistry = createRegistries.find(item => item.id === createRegistryId) || createRegistries[0];
  function startCreation() {
    if (creating) {if (layout.mode === "table") chooseLayout("split"); return;}
    if (dirtyCard.current && !window.confirm("В карточке есть несохранённые изменения. Открыть создание нового документа?")) return;
    priorCreateLayout.current = layout;
    setCreateRegistryId(createRegistries.some(item => item.id === registryId) ? registryId : createRegistries[0].id);
    setCreating(true);
    if (layout.mode === "table") chooseLayout("split");
  }
  function cancelCreation() {
    if (dirtyCreation.current && !window.confirm("В новом документе есть несохранённые изменения. Отменить создание?")) return;
    setCreating(false);
    if (priorCreateLayout.current) restoreLayout(priorCreateLayout.current);
  }
  const workspaceDetail = <>
    <div hidden={creating}>{detail}</div>
    {creating && createRegistry && <CreateDocument key={createRegistry.id} registries={createRegistries}
      onRegistryChange={id => {if (!dirtyCreation.current || window.confirm("В новом документе есть несохранённые изменения. Сменить реестр?")) setCreateRegistryId(id);}}
      onBeforeCreate={() => !dirtyCard.current || window.confirm("В выбранной карточке есть несохранённые изменения. Создать новый документ и закрыть её без сохранения?")}
      onDirtyChange={reportCreationDirty} registry={createRegistry} types={types.data || []} references={references.data || []}
      onCreated={id => {setCreating(false); setSavedDocument({id, revision: 1}); setDocumentId(id); setContentTarget(undefined); if (layout.mode === "table") chooseLayout("split");}}
      onClose={cancelCreation}/>}
  </>;
  return (
    <div className="documents-search documents-layout-page">
      <div className="toolbar documents-heading">
        <div>
          <span className="eyebrow">
            {readOnly ? "ПРОСМОТР ДОКУМЕНТОВ" : "РАБОТА С ДОКУМЕНТАМИ"}
          </span>
          <h1>Документы</h1>
        </div>
      </div>
      <Feedback
        error={registries.error || types.error || references.error}
        loading={registries.loading}
      />
      {!registries.loading && !registries.error && registries.data && !registries.data.some(item => item.permissions.includes("read")) && (
        <div className="empty-state">Вам пока не предоставлен доступ к реестрам. Обратитесь к администратору.</div>
      )}
      {(registry || registries.data?.some(item => item.permissions.includes("read"))) && (
        <>
          <form className="document-quick-search" role="search" aria-label="Поиск документов" onSubmit={event => {event.preventDefault(); applySearch();}}>
            <label className="quick-query"><span className="sr-only">Поиск</span><input type="search" value={q} maxLength={255} placeholder="Номер, название, реквизиты или текст документа…" onChange={event => setQ(event.target.value)}/></label>
            <label className="quick-registry">Реестр<select value={registryId} onChange={event => changeRegistry(event.target.value)}><option value="">Все реестры</option>{registries.data?.filter(item => item.permissions.includes("read")).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label><span className="sr-only">Где искать</span><select value={scope} onChange={event => setScope(event.target.value as SearchScope)}><option value="all">Везде</option><option value="metadata">Реквизиты</option><option value="content">Содержание документов</option></select></label>
            <button className="primary" type="submit">Найти</button>
            <button type="button" aria-haspopup="dialog" onClick={openAdvancedSearch}>Расширенный поиск</button>
            <button type="button" className="text-button" onClick={() => {setQ(""); const next = new URLSearchParams(search); next.delete("q"); next.delete("text"); setSearch("&" + next.toString());}}>Очистить запрос</button>
          </form>
          <div className="search-feedback-line"><span className="search-scope-caption">{registryId ? `Реестр: ${registry?.name || "недоступен"}` : "По всем доступным реестрам"}</span><SearchStatus loading={list.searching} startedAt={list.startedAt} durationMs={list.durationMs} error={list.searchError}/></div>
          {searchOpen && <DocumentDialog label="Расширенный поиск" className="search-drawer" onClose={closeAdvancedSearch}>
            <div className="search-drawer-heading"><div><span className="eyebrow">ПОИСК ДОКУМЕНТОВ</span><h2>Расширенный поиск</h2></div><button type="button" aria-label="Закрыть расширенный поиск" onClick={closeAdvancedSearch}>Закрыть</button></div>
            <form id="advanced-document-search" className="search-drawer-form" onSubmit={event => {event.preventDefault(); applySearch(draftRegistryId); setSearchOpen(false);}}>
              <fieldset className="search-group"><legend>Основные</legend>
                <label>Текстовый запрос<input type="search" value={q} maxLength={255} placeholder="Название, реквизиты или текст файла" onChange={event => setQ(event.target.value)}/></label>
                <label>Область поиска<select value={scope} onChange={event => setScope(event.target.value as SearchScope)}><option value="all">Везде</option><option value="metadata">Реквизиты</option><option value="content">Содержание документов</option></select></label>
                <label className="checkbox-label"><input type="checkbox" checked={fuzzy} onChange={event => setFuzzy(event.target.checked)}/>Учитывать опечатки</label>
              </fieldset>
            <fieldset className="search-group"><legend>Регистрация</legend>
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
            </fieldset>
            <fieldset className="search-group"><legend>Документ</legend>
              <label>Реестр документов<select value={draftRegistryId} onChange={event => {setDraftRegistryId(event.target.value); setTypeId(""); setFieldFilters([]);}}><option value="">Все реестры</option>{registries.data?.filter(item => item.permissions.includes("read")).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              <label>
              Тип
              <select
                value={typeId}
                onChange={(event) => {
                  setTypeId(event.target.value);
                  setFieldFilters([]); setSort("auto");
                }}
              >
                <option value="">Все типы</option>
                {types.data
                  ?.filter((type) =>
                    !draftRegistryId || !!registries.data?.find(item => item.id === draftRegistryId)?.document_type_ids.includes(type.id),
                  )
                  .map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.name}
                    </option>
                  ))}
              </select>
            </label>
              <label>
              Состояние карточки
              <select value={status} onChange={event => setStatus(event.target.value)}><option value="">Все доступные</option>{!readOnly && <option value="draft">Черновик</option>}<option value="published">Опубликован</option></select>
            </label>
              <label>База публикации карточки<select value={publicationTarget} onChange={event => setPublicationTarget(event.target.value)}><option value="">Все доступные</option><option value="internal">Внутренняя база</option><option value="shared">Общая база</option></select></label>
            </fieldset>
            <fieldset className="search-group"><legend>Содержание</legend><p className="search-group-caption">Для поиска внутри файлов выберите область «Содержание документов».</p>
              <label>Версии файлов<select value={versionScope} onChange={event => setVersionScope(event.target.value)}><option value="current">Актуальные</option><option value="all">Все доступные</option></select></label>
            </fieldset>
              <fieldset className="search-group"><legend>Поля документа</legend>
            <Feedback loading={filterCatalog.loading} error={filterCatalog.error}/>
            {(filterFields.length > 0 || fieldFilters.length > 0) && <DocumentFieldFilters filters={fieldFilters} fields={filterFields} onChange={setFieldFilters}
              roles={auth.user?.roles || []} registryId={draftRegistryId} registryNames={Object.fromEntries((registries.data||[]).map(registry=>[registry.id,registry.name]))} references={references.data || []} readOnly={readOnly}/>}

              </fieldset>
            </form>
          <details className="panel search-saved-views"><summary>Сохранённые поиски</summary>
            <div className="inline-form"><label>Название представления<input value={viewName} onChange={event => setViewName(event.target.value)} /></label><button disabled={!viewName.trim()} onClick={() => {persistViews([...savedViews.filter(item => !(item.registry === draftRegistryId && item.name === viewName.trim())), {name: viewName.trim(), registry: draftRegistryId, query: searchDraft()}]); setViewName("");}}>Сохранить текущий поиск</button></div>
            {savedViews.filter(item => item.registry === draftRegistryId).map(item => <div className="button-row" key={item.name}><button onClick={() => restoreSearchDraft(item.query)}>{item.name}</button><button aria-label={`Удалить представление: ${item.name}`} onClick={() => persistViews(savedViews.filter(current => current !== item))}>Удалить</button></div>)}
          </details>
            <div className="search-drawer-footer"><button type="button" onClick={closeAdvancedSearch}>Отмена</button><button className="primary" type="submit" form="advanced-document-search">Найти</button></div>
          </DocumentDialog>}

          <div className="active-search-filters" aria-label="Активные фильтры">{Array.from(new URLSearchParams(search)).filter(([key, value]) => value && ["q", "text", "reg_number", "reg_date", "status", "card_state", "publication_target", "date_from", "date_to", "document_type_id"].includes(key)).map(([key, value]) => <button key={key} type="button" aria-label={"Удалить фильтр " + key} onClick={() => {const next = new URLSearchParams(search); next.delete(key); loadView(next.toString());}}>{({q: "Запрос", text: "Запрос", reg_number: "Номер", reg_date: "Дата регистрации", status: "Статус правового акта", card_state: "Состояние карточки", publication_target: "База публикации", date_from: "С", date_to: "По", document_type_id: "Тип", filters: "Поля"} as Record<string, string>)[key]}: {key === "document_type_id" ? types.data?.find(item => item.id === value)?.name || "Тип документа" : key === "filters" ? "Задан фильтр поля" : key === "publication_target" ? value === "shared" ? "Общая база" : "Внутренняя база" : key === "card_state" ? value === "draft" ? "Черновик" : "Опубликован" : value} ×</button>)}</div>
          <div className="active-search-filters" aria-label="Активные условия полей">{parseFieldFilters(new URLSearchParams(search).get("filters")).map((filter, index) => <button type="button" key={filter.key} onClick={() => {
            const params = new URLSearchParams(search); const remaining = parseFieldFilters(params.get("filters")).filter(item => item.key !== filter.key);
            if (remaining.length) params.set("filters", JSON.stringify(serializeFieldFilters(remaining))); else params.delete("filters"); loadView(params.toString());
          }} aria-label={`Удалить активное условие ${index + 1}`}>{filterFields.find(field => field.key === filter.key)?.label || filter.key} ×</button>)}</div>
          <Feedback error={list.error} />
          <Feedback {...selectionAction}/>
          {selection.ids.size > 0 && <div className="document-selection-toolbar" role="group" aria-label="Действия с выбранными документами">
            <strong>Выбрано: {selection.ids.size} / 1000</strong>
            <button type="button" onClick={selection.all}>Выделить все загруженные</button>
            <button type="button" onClick={selection.clear}>Снять выделение</button>
            {(["csv", "xlsx"] as const).map(format => <button key={format} type="button" disabled={selectionAction.busy || (configuredColumns.length > 0 && !exportColumns.length)} onClick={() => void selectionAction.run(() => exportSelected(format), "Экспорт выбранных подготовлен")}>Экспорт выбранных {format.toUpperCase()}</button>)}
            <button type="button" disabled={selectionAction.busy} onClick={() => void selectionAction.run(copySelected, "Номер и название скопированы")}>Копировать номер и название</button>
            <button type="button" disabled={selectionAction.busy} onClick={event => {const rect = event.currentTarget.getBoundingClientRect(); setContextPoint({x: rect.left, y: rect.bottom});}}>Действия</button>
          </div>}
          {contextPoint && selection.ids.size > 0 && <DocumentContextMenu point={contextPoint} count={selection.ids.size} single={selection.ids.size === 1} busy={selectionAction.busy}
            canExport={!configuredColumns.length || exportColumns.length > 0} canDelete={!readOnly && selectedRows.every(item => item.permissions?.includes("update"))} onClose={closeContext}
            onOpen={() => {if (selectedRows[0]) openFullResult(selectedRows[0].id);}}
            onOriginal={() => void selectionAction.run(original, "Выберите версию оригинала")}
            onCopy={() => void selectionAction.run(copySelected, "Номер и название скопированы")}
            onExport={format => void selectionAction.run(() => exportSelected(format), "Экспорт выбранных подготовлен")}
            onDelete={() => void selectionAction.run(prepareDelete, "Укажите причину удаления")}/>}
          {deleteCards && <DocumentDialog labelledBy="delete-documents-title" onClose={() => {if (!selectionAction.busy) setDeleteCards(null);}}>
            <h2 id="delete-documents-title">Удалить выбранные документы?</h2>
            <p>Документы будут скрыты из рабочих списков и поиска. История, номера и оригиналы сохранятся.</p>
            <ul>{deleteCards.map(item => <li key={item.id}>{item.reg_number} {item.title}</li>)}</ul>
            {dirtyCard.current && deleteCards.some(item => item.id === documentId) && <p className="error">В открытой карточке есть несохранённые изменения. При удалении они будут потеряны.</p>}
            <form onSubmit={event => {event.preventDefault(); if (deleteReason.trim()) void selectionAction.run(deleteSelected, "Документы удалены");}}>
              <label>Причина удаления<textarea autoFocus required maxLength={2000} value={deleteReason} onChange={event => setDeleteReason(event.target.value)}/></label>
              <Feedback error={selectionAction.error}/>
              <div className="button-row"><button type="submit" disabled={selectionAction.busy || !deleteReason.trim()}>Подтвердить удаление</button><button type="button" disabled={selectionAction.busy} onClick={() => setDeleteCards(null)}>Отмена</button></div>
            </form>
          </DocumentDialog>}
          {originalCard && <DocumentDialog label="Оригиналы и версии" onClose={() => setOriginalCard(null)}>
            <h2>Оригиналы и версии</h2><p>{originalCard.title}</p>
            {originalCard.files.map(file => <div key={file.id} className="file-line"><span>{file.name}</span><FileActions file={file} mode={displayMode} csrfToken={auth.user?.csrf_token || ""}/></div>)}
            {!originalCard.files.length && <p>Оригиналы пока не загружены.</p>}
            <Feedback error={selectionAction.error}/><button autoFocus onClick={() => setOriginalCard(null)}>Закрыть</button>
          </DocumentDialog>}
          <div className="document-results-toolbar" role="group" aria-label="Вид и сортировка документов">
          <div className="search-sort"><label>
              Сортировка
              <select
                value={sort}

                onChange={(event) => {setSort(event.target.value); const next = new URLSearchParams(search); next.set("sort", event.target.value); setSearch("&" + next.toString()); }}
              >
                <option value="auto">Автоматически</option><option value="relevance">Релевантность</option><option value="updated_at">Последние изменения</option>
                <option value="reg_date">Дата регистрации</option>
                <option value="reg_number">Номер</option>
                <option value="title">Название</option>
                {filterFields.filter(field => !field.system && field.type !== "multi_reference" && !field.key.startsWith("schema:")).map(field => <option key={field.key} value={`custom:${field.key}`}>{field.label}</option>)}
              </select>
            </label>
            <label>Направление сортировки<select value={direction}  onChange={event => {setDirection(event.target.value); const next = new URLSearchParams(search); next.set("direction", event.target.value); setSearch("&" + next.toString()); }}><option value="asc">По возрастанию</option><option value="desc">По убыванию</option></select></label>
</div>
            <div className="document-view-actions">
              <details className="search-transfer document-list-export" aria-label="Экспорт документов"><summary>Экспорт</summary>
          <Feedback {...exportAction} />
          <div className="button-row">{(["csv", "xlsx"] as const).map(format => <button key={format} disabled={list.loading || exportAction.busy || (configuredColumns.length > 0 && !exportColumns.length)} onClick={() => void exportAction.run(() => saveExport(`/search/export?parameters=${encodeURIComponent(JSON.stringify({...applied, cursor: null}))}&format=${format}${exportColumns.length ? "&columns=" + encodeURIComponent(exportColumns.map(column => column.key).join(",")) : ""}`, `documents.${format}`), "Экспорт подготовлен")}>Экспорт {format.toUpperCase()}</button>)}</div>
          {configuredColumns.length > 0 && !exportColumns.length && <p className="muted">В выбранном представлении нет разрешённых для экспорта колонок.</p>}
          </details>
              <DocumentLayoutSwitch mode={layout.mode} onChange={chooseLayout}/>
            {!readOnly && createRegistries.length > 0 && <button className="primary" onClick={startCreation}>Создать документ</button>}</div>
          </div>
          <DocumentWorkspace onListEnd={list.appendError ? undefined : list.loadMore} feedKey={JSON.stringify(applied)} busy={list.loading} layout={layout} onChange={chooseLayout} onResize={resizeLayout} onRestore={restoreLayout}
            selectedLabel={creating ? "Новый документ" : card.data?.title || resultDocuments.find(item => item.id === documentId)?.title || "Карточка"}
            detail={workspaceDetail}
            count={<div className="document-results-heading"><h2>Документы</h2><span className="muted">Найдено: {list.data?.total ?? "…"}</span></div>}
            list={<>
              <DocumentResults columnsSchema={applied.document_type_ids[0] === typeId ? publishedSchema : undefined} results={list.data?.items || []} schemas={listSchemas.byVersion} sortSchemas={listSchemas.forType(applied.document_type_ids.length === 1 ? applied.document_type_ids[0] || "" : "")} types={types.data || []}
                roles={auth.user?.roles || []} readOnly={readOnly} selected={documentId} selectedIds={selection.ids} query={applied.query}
                sort={applied.sort} direction={applied.direction} typeId={applied.document_type_ids.length === 1 ? applied.document_type_ids[0] || "" : ""}
                onSort={sortResults} onSelect={openSearchResult} onOpen={openFullResult} onSelection={selection.select} onSelectAll={selection.all}
                onContextMenu={(id, point) => {selection.context(id); setContextPoint(point);}}/>
          {list.data?.items.length === 0 && (
            <div className="empty-state">
              Документов по выбранным условиям пока нет.
            </div>
          )}
          {list.error && <button onClick={list.retry}>Повторить поиск</button>}
          {(list.data || list.loading) && <DocumentFeedEnd enabled={layout.mode !== "card"} hasMore={!!list.data?.next_cursor} loading={list.loading} error={list.appendError} onLoad={list.loadMore}/>}
            </>}/>
        </>
      )}
    </div>
  );
}
