import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { executionState, localDeadline, utcDeadline, type ExecutionEvent, type ExecutionPage, type ProcessInstance, type StepExecution } from "../engine/execution";
import type { ReferenceSet } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { ExecutionBasis } from "./ExecutionBasis";
import { ExecutionResultEditor } from "./ExecutionResult";
import { ConfirmedExecutionResult } from "./ConfirmedExecutionResult";

function dateText(value: string | null | undefined) {return value ? new Date(value).toLocaleString("ru-RU") : "Не указан";}
function ExecutionHistory({instance, readOnly}: {instance: ProcessInstance; readOnly: boolean}) {
  const [page, setPage] = useState(1);
  const data = useData<ExecutionPage<ExecutionEvent>>(`/execution/instances/${instance.id}/history?mode=${readOnly ? "viewer" : "editor"}&page=${page}&page_size=20`);
  const labels: Record<string, string> = {started: "Дело начато", opened: "Шаг открыт", assigned: "Назначен ответственный", "checklist.saved": "Проверки сохранены", "result.saved": "Результат подтверждён", advanced: "Шаг завершён", suspended: "Дело приостановлено", resumed: "Дело возобновлено", cancelled: "Дело отменено", returned: "Возврат на исправление", "deadline.changed": "Срок изменён"};
  return <section className="execution-history"><h3>История дела</h3><Feedback error={data.error} loading={data.loading}/><ol>{data.data?.items.map(event => <li key={event.id}><strong>{labels[event.action] || "Изменение дела"}</strong> · {dateText(event.created_at)}{event.actor_name && ` · ${event.actor_name}`}{event.step_key && <p>{instance.process_snapshot.steps.find(step => step.key === event.step_key)?.title || "Шаг процесса"}</p>}{event.reason && <p className="content-text">{event.reason}</p>}</li>)}</ol>{data.data && <div className="pagination"><span>Событий: {data.data.total} · страница {page}</span><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Предыдущие события дела</button><button type="button" disabled={page * data.data.page_size >= data.data.total} onClick={() => setPage(value => value + 1)}>Следующие события дела</button></div>}</section>;
}

function ExecutionControl({instance, busy, onAction}: {instance: ProcessInstance; busy: boolean; onAction: (action: string, body: Record<string, unknown>) => Promise<void>}) {
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState(instance.assignee_id || "");
  const [deadline, setDeadline] = useState(localDeadline(instance.deadline_at));
  const users = useData<{id: string; display_name: string}[]>(selected === "assign" ? `/execution/assignees?registry_id=${instance.registry_id}` : null);
  const [error, setError] = useState("");
  const ended = ["completed", "cancelled"].includes(instance.state);
  function choose(action: string) {setSelected(action); setReason(""); setAssignee(instance.assignee_id || ""); setDeadline(localDeadline(instance.deadline_at)); setError("");}
  const titles: Record<string, string> = {assign: "Назначить ответственного", suspend: "Приостановить дело", resume: "Возобновить дело", cancel: "Отменить дело", return: "Вернуть на исправление", deadline: "Изменить срок дела"};
  return <section className="execution-controls">{!ended && <div className="button-row"><button type="button" disabled={busy} onClick={() => choose("assign")}>Назначить ответственного</button>{instance.state !== "suspended" ? <button type="button" disabled={busy} onClick={() => choose("suspend")}>Приостановить дело</button> : <button type="button" disabled={busy} onClick={() => choose("resume")}>Возобновить дело</button>}<button type="button" disabled={busy} onClick={() => choose("deadline")}>Изменить срок дела</button>{instance.state === "active" && instance.steps.some(step => step.state === "completed") && <button type="button" disabled={busy} onClick={() => choose("return")}>Вернуть на исправление</button>}<button type="button" disabled={busy} onClick={() => choose("cancel")}>Отменить дело</button></div>}
    {selected && <form className="panel compact-form" onSubmit={event => {event.preventDefault(); setError(""); void (async () => {try {await onAction(selected, selected === "assign" ? {assignee_id: assignee} : selected === "deadline" ? {deadline_at: utcDeadline(deadline), reason} : {reason}); setSelected("");} catch (failure) {setError(failure instanceof Error ? failure.message : "Действие не выполнено");}})();}}><h3>{titles[selected]}</h3>
      {selected === "assign" ? <label>Новый ответственный<select required value={assignee} onChange={event => setAssignee(event.target.value)}><option value="">Выберите сотрудника с доступом</option>{users.data?.map(user => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label> : <>{selected === "deadline" && <label>Новый срок дела<input type="datetime-local" value={deadline} onChange={event => setDeadline(event.target.value)}/><small>Время вашего браузера ({Intl.DateTimeFormat().resolvedOptions().timeZone}). Пустое значение снимает срок.</small></label>}<label>Причина действия<textarea required value={reason} onChange={event => setReason(event.target.value)}/></label></>}
      {selected === "return" && <p>Откроется новая попытка непосредственно предыдущего завершённого шага. Прежние результаты и история сохраняются.</p>}
      <Feedback error={error || users.error}/><div className="button-row"><button disabled={busy || (selected === "assign" ? !assignee : !reason.trim())}>Подтвердить действие</button><button type="button" onClick={() => setSelected("")}>Отмена действия</button></div>
    </form>}
  </section>;
}

function ExecutionChecklist({execution, busy, editable, onSave}: {execution: StepExecution; busy: boolean; editable: boolean; onSave: (body: Record<string, unknown>) => Promise<void>}) {
  const [checks, setChecks] = useState(() => Object.fromEntries(execution.checklist.map(item => [item.id, item.checked])));
  return <section className="execution-checklist"><h4>Проверки шага</h4>{execution.checklist.map(item => <label className="checkbox-label" key={item.id}><input type="checkbox" checked={checks[item.id] ?? item.checked} disabled={!editable || busy} onChange={event => setChecks(current => ({...current, [item.id]: event.target.checked}))}/>{item.label}{item.required ? " · обязательно" : ""}</label>)}{execution.checklist.length === 0 && <p>Отдельные проверки не заданы.</p>}{editable && execution.checklist.length > 0 && <button type="button" disabled={busy} onClick={() => void onSave({items: execution.checklist.map(item => ({id: item.id, checked: checks[item.id] ?? item.checked}))}).catch(() => {})}>Сохранить проверки</button>}</section>;
}

export function ExecutionDetail({id, readOnly, initial, onChanged}: {id: string; readOnly: boolean; initial?: ProcessInstance; onChanged?: () => void}) {
  const data = useData<ProcessInstance>(`/execution/instances/${id}?mode=${readOnly ? "viewer" : "editor"}`);
  const [fresh, setFresh] = useState<ProcessInstance | null>(initial || null);
  const [selectedAttempt, setSelectedAttempt] = useState("");
  const instance = fresh || data.data;
  const action = useAction();
  const references = useData<ReferenceSet[]>(`/references?mode=${readOnly ? "viewer" : "editor"}`);
  async function mutate(operation: string, body: Record<string, unknown> = {}) {
    if (!instance) return;
    action.setError("");
    try {const next = await action.request<ProcessInstance>(`/execution/instances/${id}/${operation}`, "POST", {...body, revision: instance.revision}); setFresh(next); setSelectedAttempt(""); onChanged?.();}
    catch (failure) {action.setError(failure instanceof Error ? failure.message : "Действие не выполнено"); throw failure;}
  }
  async function run(operation: string, body: Record<string, unknown> = {}) {let failure: unknown; await action.run(async () => {try {await mutate(operation, body);} catch (error) {failure = error; throw error;}}, "Дело обновлено"); if (failure) throw failure;}
  if (!instance) return <Feedback error={data.error} loading={data.loading}/>;
  const current = instance.steps.filter(execution => execution.step_key === instance.current_step_key && ["pending", "active"].includes(execution.state)).at(-1);
  const execution = instance.steps.find(item => item.id === selectedAttempt) || current || instance.steps.at(-1);
  const step = instance.process_snapshot.steps.find(item => item.key === execution?.step_key);
  const editable = !readOnly && instance.permissions.work && instance.state === "active" && execution?.id === current?.id && execution?.state === "active";
  const controls = !readOnly && instance.permissions.control;
  return <section className="execution-detail"><div className="toolbar"><div><span className="eyebrow">ДЕЛО ПО ПРОЦЕССУ</span><h2>{instance.name}</h2><p><span className="badge">{executionState(instance.state)}</span>{instance.overdue && <strong className="error-text"> · Просрочено</strong>}</p><p>{instance.process_snapshot.metadata_snapshot?.name || "Закреплённый процесс"}{instance.process_snapshot.version ? ` · версия ${instance.process_snapshot.version}` : ""}</p></div><button type="button" onClick={() => {setFresh(null); data.reload();}}>Обновить дело</button></div>
    <dl className="process-facts"><div><dt>Ответственный</dt><dd>{instance.assignee_name || "Не назначен"}</dd></div><div><dt>Срок дела</dt><dd>{dateText(instance.deadline_at)}</dd></div><div><dt>Начал дело</dt><dd>{instance.created_by_name || "Сотрудник с доступом"}</dd></div></dl><p className="muted">Версия процесса, формы, основания и шаблоны закреплены при старте. Доступ к содержимому проверяется при каждом обращении.</p>
    <Feedback error={data.error || action.error} message={action.message}/>
    {controls && <ExecutionControl instance={instance} busy={action.busy} onAction={run}/>}
    <section className="execution-attempts"><h3>Прохождение шагов</h3><ol>{instance.steps.map(item => <li key={item.id}><button type="button" className={execution?.id === item.id ? "active" : "text-button"} onClick={() => setSelectedAttempt(item.id)}>{instance.process_snapshot.steps.find(candidate => candidate.key === item.step_key)?.title || "Шаг"} · попытка {item.attempt} · {executionState(item.state)}</button></li>)}</ol></section>
    {!execution && <p>Сведения о шагах недоступны с текущими правами.</p>}
    {execution && step && <section className="panel execution-current-step"><h3>{step.title} · попытка {execution.attempt}</h3><p className="content-text">{step.instruction}</p><p>Исполнитель: {execution.assignee_name || step.executor_role || "Не указан"} · {executionState(execution.state)}{execution.overdue ? " · Просрочено" : ""}</p><p>Открыт: {dateText(execution.started_at)} · завершён: {dateText(execution.completed_at)} · срок шага: {dateText(execution.deadline_at)}</p>
      {step.control_note && <p><strong>Контроль:</strong> {step.control_note}</p>}
      {!readOnly && instance.permissions.work && current?.id === execution.id && execution.state === "pending" && ["pending", "active"].includes(instance.state) && <button type="button" className="primary" disabled={action.busy} onClick={() => void run("open").catch(() => {})}>Открыть текущий шаг</button>}
      <ExecutionChecklist key={execution.id} execution={execution} busy={action.busy} editable={editable} onSave={body => run("checklist", body)}/>
      <ExecutionBasis key={execution.id + ":basis"} instanceId={id} executionId={execution.id} readOnly={readOnly} kind="basis"/><ExecutionBasis key={execution.id + ":templates"} instanceId={id} executionId={execution.id} readOnly={readOnly} kind="templates"/>
      {execution.result && <ConfirmedExecutionResult key={execution.result.id} instance={instance} executionId={execution.id} result={execution.result} step={step} readOnly={readOnly} references={references.data || []}/>}
      {editable && <><ExecutionResultEditor key={execution.id} instance={instance} execution={execution} step={step} busy={action.busy} onSave={body => run("result", body as Record<string, unknown>)}/><button type="button" className="primary" disabled={action.busy} onClick={() => void run("advance").catch(() => {})}>{step.is_terminal ? "Завершить дело" : "Завершить шаг"}</button><p className="muted">Сервер проверит подтверждённый результат, обязательные проверки и доступ к условиям маршрута.</p></>}
    </section>}
    <ExecutionHistory key={id + ":" + instance.revision} instance={instance} readOnly={readOnly}/>
  </section>;
}
