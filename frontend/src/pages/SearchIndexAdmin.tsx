import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { useAction, useData } from "../engine/useData";
import { extractionStates } from "../engine/content";

type IndexStatus = {
  indexed_documents: number; pending_documents: number; extractions: Record<string, number>;
  queue_age_seconds: number; last_rebuild: {at: string; state: string} | null;
  latency: {requests: number; mean_ms: number | null; p95_upper_bound_ms: number | null};
};

export function SearchIndexAdmin({registryId}: {registryId: string}) {
  const status = useData<IndexStatus>("/admin/search/status" + (registryId ? "?registry_id=" + registryId : ""));
  const errors = useData<{items: {id: string; state: string; error_code: string | null; file_id: string}[]}>("/admin/content/errors?page_size=20");
  const action = useAction();
  const [reason, setReason] = useState("");
  const data = status.data;
  return <section className="panel"><div className="toolbar"><h2>Поисковый индекс</h2><button onClick={() => {status.reload(); errors.reload();}}>Обновить состояние индекса</button></div>
    <Feedback error={status.error || errors.error || action.error} loading={status.loading} message={action.message}/>
    {data?.latency && <><p>Проиндексировано документов: {data.indexed_documents} · ожидают: {data.pending_documents}</p>
      <p>Возраст очереди файлов: {data.queue_age_seconds} с</p><ul>{Object.entries(data.extractions).map(([state, count]) => <li key={state}>{extractionStates[state] || state}: {count}</li>)}</ul>
      <p>Поисковых запросов: {data.latency.requests} · среднее: {data.latency.mean_ms ?? "—"} мс · p95, верхняя граница диапазона: {data.latency.p95_upper_bound_ms ?? "—"} мс</p>
      <p>Последняя перестройка: {data.last_rebuild ? `${new Date(data.last_rebuild.at).toLocaleString("ru-RU")} · ${data.last_rebuild.state === "completed" ? "Завершена" : "Выполняется"}` : "Не запускалась"}</p></>}
    <form className="inline-form" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request("/admin/search/rebuild", "POST", {registry_id: registryId, reason}); setReason(""); status.reload();}, "Индекс поставлен на фоновую перестройку");}}><label>Причина перестройки индекса<input value={reason} maxLength={2000} onChange={event => setReason(event.target.value)}/></label><button disabled={!registryId || !reason.trim() || action.busy}>Перестроить индекс реестра</button></form>
    {!registryId && <p className="muted">Для контролируемой перестройки выберите реестр эксплуатационных данных.</p>}
    {!!errors.data?.items?.length && <details><summary>Диагностика файлов</summary><ul>{errors.data.items.map(item => <li key={item.id}>{extractionStates[item.state]} · {item.error_code || "Без ошибки"}{item.state === "failed" && <button disabled={action.busy} onClick={() => void action.run(async () => {await action.request(`/files/${item.file_id}/extract`, "POST", {}); status.reload(); errors.reload();}, "Файл поставлен на повторную обработку")}>Повторить обработку файла</button>}</li>)}</ul></details>}
  </section>;
}
