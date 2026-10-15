import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import type { ProcessInstance } from "../engine/execution";
import { utcDeadline } from "../engine/execution";
import type { Registry } from "../engine/schema";
import { useAction, useData } from "../engine/useData";
import { ExecutionDetail } from "./ExecutionDetail";

export function ExecutionStartForm({versionId, processName, registryId, onCreated, onClose}: {versionId: string; processName: string; registryId: string; onCreated: (instance: ProcessInstance) => void; onClose: () => void}) {
  const [name, setName] = useState(processName);
  const [assignee, setAssignee] = useState("");
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const users = useData<{id: string; display_name: string}[]>(`/execution/assignees?registry_id=${registryId}`);
  const auth = useAuth();
  const selfAssignable = users.data?.some(user => user.id === auth.user?.id);
  const action = useAction();
  return <form className="panel compact-form execution-start" onSubmit={event => {event.preventDefault(); void action.run(async () => {const instance = await action.request<ProcessInstance>("/execution/instances", "POST", {process_version_id: versionId, name, ...(assignee ? {assignee_id: assignee} : {}), deadline_at: utcDeadline(deadline), ...(reason.trim() ? {deadline_reason: reason} : {})}); onCreated(instance); onClose();}, "Дело начато");}}>
    <h3>Новое дело · {processName}</h3><label>Название дела<input required maxLength={300} value={name} onChange={event => setName(event.target.value)}/></label>
    <label>Ответственный за дело<select required={!selfAssignable} value={assignee} onChange={event => setAssignee(event.target.value)}><option value="">{selfAssignable ? "Назначить мне" : "Выберите сотрудника с доступом"}</option>{users.data?.map(user => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>
    <label>Срок дела<input type="datetime-local" value={deadline} onChange={event => setDeadline(event.target.value)}/><small>Время вашего браузера ({Intl.DateTimeFormat().resolvedOptions().timeZone}). Если срок не указан, используются правила процесса.</small></label>
    {deadline && <label>Причина срока<textarea value={reason} onChange={event => setReason(event.target.value)}/></label>}
    <p>Будет создано отдельное дело с закреплёнными версиями правил, форм, оснований и шаблонов.</p><Feedback error={users.error || action.error}/><div className="button-row"><button className="primary" disabled={action.busy || !users.data || (!selfAssignable && !assignee)}>Создать дело</button><button type="button" onClick={onClose}>Отмена начала дела</button></div>
  </form>;
}

export function ProcessExecutionStarter({versionId, processName, registryId}: {versionId: string; processName: string; registryId: string}) {
  const registries = useData<Registry[]>("/registries");
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<ProcessInstance | null>(null);
  const permitted = registries.data?.some(registry => registry.id === registryId && registry.permissions.includes("read") && registry.permissions.includes("create"));
  if (!permitted) return null;
  return <section className="process-execution-start"><button type="button" onClick={() => setOpen(true)}>Начать исполнение процесса</button>{open && <ExecutionStartForm key={versionId} versionId={versionId} processName={processName} registryId={registryId} onClose={() => setOpen(false)} onCreated={setCreated}/>}{created && <div className="panel"><button type="button" onClick={() => setCreated(null)}>Вернуться к описанию процесса</button><ExecutionDetail key={created.id} id={created.id} readOnly={false} initial={created}/></div>}</section>;
}
