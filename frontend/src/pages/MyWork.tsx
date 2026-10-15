import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { executionState, executionStates, type ExecutionPage, type ProcessInstance } from "../engine/execution";
import type { ProcessRecord, ProcessVersion } from "../engine/processes";
import type { Registry } from "../engine/schema";
import { useData } from "../engine/useData";
import { ExecutionDetail } from "./ExecutionDetail";
import { ExecutionStartForm } from "./ExecutionStart";

function StartFromCatalog({onCreated, onClose}: {onCreated: (instance: ProcessInstance) => void; onClose: () => void}) {
  const list = useData<ProcessRecord[]>("/processes");
  const registries = useData<Registry[]>("/registries");
  const [processId, setProcessId] = useState("");
  const [versionId, setVersionId] = useState("");
  const versions = useData<ProcessVersion[]>(processId ? `/processes/${processId}/versions?mode=editor` : null);
  const current = useData<ProcessRecord & {version: ProcessVersion | null}>(processId ? `/processes/${processId}?mode=editor` : null);
  const process = list.data?.find(item => item.id === processId);
  const version = versions.data?.find(item => item.id === versionId) || current.data?.version;
  const writable = new Set(registries.data?.filter(registry => registry.permissions.includes("read") && registry.permissions.includes("create")).map(registry => registry.id));
  return <section className="panel"><h2>Начать исполнение процесса</h2><label>Процесс для исполнения<select value={processId} onChange={event => {setProcessId(event.target.value); setVersionId("");}}><option value="">Выберите доступный процесс</option>{list.data?.filter(process => writable.has(process.registry_id) && process.status !== "archived").map(process => <option key={process.id} value={process.id}>{process.name}</option>)}</select></label><label>Версия процесса для исполнения<select value={version?.id || ""} onChange={event => setVersionId(event.target.value)}><option value="">Действующая опубликованная версия</option>{versions.data?.filter(version => version.state === "published").map(version => <option key={version.id} value={version.id}>Версия {version.version} · {version.effective_from}</option>)}</select></label><Feedback error={list.error || versions.error || current.error || registries.error}/>{version?.state === "published" && process ? <ExecutionStartForm key={version.id} versionId={version.id} processName={process.name} registryId={process.registry_id} onCreated={onCreated} onClose={onClose}/> : <button type="button" onClick={onClose}>Отмена начала дела</button>}</section>;
}

export function MyWork({readOnly}: {readOnly: boolean}) {
  const registries = useData<Registry[]>("/registries");
  const [mine, setMine] = useState(!readOnly);
  const [registryId, setRegistryId] = useState("");
  const [state, setState] = useState("");
  const [overdue, setOverdue] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState("");
  const [initial, setInitial] = useState<ProcessInstance | undefined>();
  const [creating, setCreating] = useState(false);
  const params = new URLSearchParams({mode: readOnly ? "viewer" : "editor", mine: String(mine), page: String(page), page_size: "20", ...(registryId ? {registry_id: registryId} : {}), ...(state ? {state} : {}), ...(overdue ? {overdue: "true"} : {})});
  const data = useData<ExecutionPage<ProcessInstance>>(`/execution/instances?${params}`);
  const mayStart = !readOnly && registries.data?.some(registry => registry.permissions.includes("read") && registry.permissions.includes("create"));
  return <section className="my-work"><div className="toolbar"><div><h1>{readOnly ? "Ход работы" : "Моя работа"}</h1><p>{readOnly ? "Разрешённые дела, результаты и история исполнения." : "Назначенные дела, текущие шаги и сроки."}</p></div>{mayStart && <button className="primary" onClick={() => {setCreating(true); setSelected("");}}>Начать исполнение процесса</button>}</div>
    <div className="panel filters"><label>Реестр дел<select value={registryId} onChange={event => {setRegistryId(event.target.value); setPage(1);}}><option value="">Все доступные реестры</option>{registries.data?.filter(registry => registry.permissions.includes("read")).map(registry => <option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label><label>Состояние дела<select value={state} onChange={event => {setState(event.target.value); setPage(1);}}><option value="">Все состояния</option>{executionStates.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>{!readOnly && <label className="checkbox-label"><input type="checkbox" checked={!mine} onChange={event => {setMine(!event.target.checked); setPage(1);}}/>Все доступные дела</label>}<label className="checkbox-label"><input type="checkbox" checked={overdue} onChange={event => {setOverdue(event.target.checked); setPage(1);}}/>Только просроченные дела</label></div>
    <Feedback error={data.error || registries.error} loading={data.loading}/>
    {creating && <StartFromCatalog onClose={() => setCreating(false)} onCreated={instance => {setSelected(instance.id); setInitial(instance); data.reload();}}/>}
    {selected && <div className="panel"><button type="button" onClick={() => {setSelected(""); setInitial(undefined); data.reload();}}>К списку дел</button><ExecutionDetail key={selected} id={selected} readOnly={readOnly} initial={initial} onChanged={data.reload}/></div>}
    {!selected && !creating && <><div className="module-grid execution-cards">{data.data?.items.map(instance => <button type="button" className="module-card" key={instance.id} onClick={() => {setSelected(instance.id); setInitial(undefined);}}><h3>{instance.name}</h3><p>{executionState(instance.state)}{instance.overdue ? " · Просрочено" : ""}</p><p>{instance.assignee_name || "Ответственный не указан"}</p>{instance.deadline_at && <small>Срок: {new Date(instance.deadline_at).toLocaleString("ru-RU")}</small>}</button>)}</div>{data.data?.total === 0 && <p className="empty-state">По выбранным условиям доступных дел нет.</p>}{data.data && <div className="pagination"><span>Дел: {data.data.total} · страница {page}</span><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Предыдущая страница дел</button><button type="button" disabled={page * data.data.page_size >= data.data.total} onClick={() => setPage(value => value + 1)}>Следующая страница дел</button></div>}</>}
  </section>;
}
