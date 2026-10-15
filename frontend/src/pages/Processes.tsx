import { useState } from "react";
import { Feedback } from "../components/Feedback";
import type { ProcessRecord, ProcessVersion } from "../engine/processes";
import type { DocumentType, Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { ProcessBuilder } from "./ProcessBuilder";
import { ProcessView } from "./ProcessView";

function ProcessMetadata({process, copy = false, onSaved, onClose}: {process?: ProcessRecord; copy?: boolean; onSaved: (id: string) => void; onClose: () => void}) {
  const registries = useData<Registry[]>("/registries");
  const users = useData<{id: string; display_name: string}[]>("/users/options");
  const [draft, setDraft] = useState({registry_id: process?.registry_id || "", code: copy ? "" : process?.code || "", name: process ? process.name + (copy ? " — копия" : "") : "", description: process?.description || "", owner_id: process?.owner_id || null, department: process?.department || "", category: process?.category || "", purpose: process?.purpose || "", status: process?.status || "active"});
  const action = useAction();
  function patch(next: Partial<typeof draft>) {setDraft(current => ({...current, ...next}));}
  return <form className="panel compact-form process-metadata" onSubmit={event => {event.preventDefault(); void action.run(async () => {
    const editing = !!process && !copy;
    const {registry_id: _registry, code: _code, ...editable} = draft;
    const saved = await action.request<ProcessRecord>(editing ? `/processes/${process.id}` : copy ? `/processes/${process!.id}/copy` : "/processes", editing ? "PATCH" : "POST", editing ? editable : draft);
    onSaved(saved.id); onClose();
  }, "Процесс сохранён");}}>
    <h2>{copy ? "Копия процесса" : process ? "Метаданные процесса" : "Новый процесс"}</h2>
    <label>Название процесса<input required value={draft.name} onChange={event => patch({name: event.target.value})}/></label>
    <label>Код процесса<input required disabled={!!process && !copy} pattern="[a-z][a-z0-9_-]*" value={draft.code} onChange={event => patch({code: event.target.value})}/></label>
    <label>Реестр процесса<select required disabled={!!process && !copy} value={draft.registry_id} onChange={event => patch({registry_id: event.target.value})}><option value="">Выберите реестр с правом изменения</option>{registries.data?.filter(registry => registry.permissions.includes("read") && registry.permissions.includes("update")).map(registry => <option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label>
    <p className="muted">Администратор настраивает процесс только при явно предоставленном доступе к реестру.</p>
    <label>Владелец процесса<select value={draft.owner_id || ""} onChange={event => patch({owner_id: event.target.value || null})}><option value="">Не указан</option>{users.data?.map(user => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>
    <label>Подразделение процесса<input value={draft.department} onChange={event => patch({department: event.target.value})}/></label>
    <label>Категория процесса<input value={draft.category} onChange={event => patch({category: event.target.value})}/></label>
    <label>Назначение процесса<textarea value={draft.purpose} onChange={event => patch({purpose: event.target.value})}/></label>
    <label>Описание процесса<textarea value={draft.description} onChange={event => patch({description: event.target.value})}/></label>
    <label>Состояние процесса<select value={draft.status} onChange={event => patch({status: event.target.value})}><option value="active">Действующий</option><option value="archived">Архивный</option></select></label>
    <Feedback error={registries.error || users.error || action.error} message={action.message}/>
    <div className="button-row"><button className="primary" disabled={action.busy}>Сохранить процесс</button><button type="button" onClick={onClose}>Отмена настройки процесса</button></div>
  </form>;
}

export function Processes({admin = false, readOnly = false}: {admin?: boolean; readOnly?: boolean}) {
  const registries = useData<Registry[]>("/registries");
  const types = useData<DocumentType[]>("/document-types");
  const [registryId, setRegistryId] = useState("");
  const list = useData<ProcessRecord[]>(`/processes${registryId ? "?registry_id=" + encodeURIComponent(registryId) : ""}`);
  const [selected, setSelected] = useState("");
  const [versionId, setVersionId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [editing, setEditing] = useState<"create" | "edit" | "copy" | null>(null);
  const mode = readOnly ? "viewer" : "editor";
  const versions = useData<ProcessVersion[]>(selected ? `/processes/${selected}/versions?mode=${mode}` : null);
  const latest = versions.data?.filter(version => version.process_id === selected).reduce<ProcessVersion | undefined>((current, version) => !current || version.version > current.version ? version : current, undefined);
  const requestedVersion = versionId || (admin ? latest?.id : undefined);
  const detail = useData<ProcessRecord & {version: ProcessVersion | null}>(selected ? `/processes/${selected}?${new URLSearchParams({mode, ...(requestedVersion ? {version_id: requestedVersion} : {}), ...(asOf && !requestedVersion ? {as_of: asOf} : {})})}` : null);
  const process = list.data?.find(item => item.id === selected) || detail.data;
  const version = detail.data?.version;
  const action = useAction();
  const registry = registries.data?.find(item => item.id === process?.registry_id);
  return <>
    <div className="toolbar"><div><h1>{admin ? "Конструктор процессов" : "Процессы"}</h1><p>Проверенные инструкции, маршруты и точные основания выполнения работы.</p></div>{admin && <button className="primary" onClick={() => setEditing("create")}>Создать процесс</button>}</div>
    <Feedback error={list.error || registries.error || types.error || versions.error || detail.error || action.error} loading={list.loading || detail.loading} message={action.message}/>
    {editing && <ProcessMetadata key={editing + (process?.id || "")} process={editing === "create" ? undefined : process || undefined} copy={editing === "copy"} onClose={() => setEditing(null)} onSaved={id => {setSelected(id); setVersionId(""); list.reload(); versions.reload(); detail.reload();}}/>}
    <div className="panel filters"><label>Реестр процессов<select value={registryId} onChange={event => {setRegistryId(event.target.value); setSelected(""); setVersionId("");}}><option value="">Все доступные реестры</option>{registries.data?.filter(item => item.permissions.includes("read")).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Процесс<select value={selected} onChange={event => {setSelected(event.target.value); setVersionId(""); setAsOf("");}}><option value="">Выберите процесс</option>{list.data?.map(item => <option key={item.id} value={item.id}>{item.name}{item.status === "archived" ? " · архивный" : ""}</option>)}</select></label></div>
    {process && <>
      <div className="toolbar"><label>Версия процесса<select value={requestedVersion || ""} onChange={event => setVersionId(event.target.value)}>{!admin && <option value="">Действующая версия</option>}{versions.data?.map(item => <option key={item.id} value={item.id}>Версия {item.version} · {item.state === "published" ? "Опубликована" : "Черновик"} · {item.effective_from}</option>)}</select></label>
        {!admin && <label>Процесс на дату<input type="date" value={asOf} onChange={event => {setAsOf(event.target.value); setVersionId("");}}/></label>}
        {admin && <div className="button-row"><button onClick={() => setEditing("edit")}>Настроить процесс</button><button onClick={() => setEditing("copy")}>Копировать процесс</button><button disabled={action.busy} onClick={() => void action.run(async () => {const next = await action.request<ProcessVersion>(`/processes/${process.id}/versions`, "POST", {}); setVersionId(next.id); versions.reload();}, "Новая версия процесса создана")}>Новая версия процесса</button></div>}
      </div>
      {version ? admin ? <ProcessBuilder key={version.id + ":" + version.state} process={process} version={version} types={(types.data || []).filter(type => registry?.document_type_ids.includes(type.id))} onSaved={() => {versions.reload(); detail.reload();}}/> : <ProcessView key={version.id} process={process} version={version} readOnly={readOnly}/> : !detail.loading && <p className="empty-state">На выбранную дату нет опубликованной версии процесса.</p>}
    </>}
    {!selected && !list.loading && <div className="module-grid">{list.data?.map(item => <button className="module-card" key={item.id} onClick={() => setSelected(item.id)}><h3>{item.name}</h3><p>{item.purpose || item.description}</p><small>{item.department} · {item.category}</small></button>)}</div>}
    {!list.loading && list.data?.length === 0 && <p className="empty-state">Доступных процессов пока нет.</p>}
  </>;
}
