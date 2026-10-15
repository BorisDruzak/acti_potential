import { useState } from "react";
import { Feedback } from "../components/Feedback";
import type { AdminPage, ServiceScopeOptions } from "../engine/serviceAccounts";
import { useAction, useData } from "../engine/useData";

type Metrics = {content: {documents: number; files: number; extractions: Record<string, number>}; usage: {action: string; count: number}[]; numbers: {visible_entries: number}; execution: {visible_instances: number; [key: string]: number}; queue: {pending: number; failed: number}; retrieval: {requests: number; duration_ms_total: number; time_to_open_count?: number; time_to_open_ms_total?: number}};
type QueueEvent = {id: string; event_type: string; state: string; attempts: number; created_at: string; error_code?: string | null};
const extractionLabels: Record<string, string> = {pending: "Ожидает извлечения", running: "Извлекается", completed: "Извлечено", failed: "Ошибка извлечения", no_text: "Без цифрового текста", unsupported: "Формат не поддерживается", encrypted: "Защищён паролем"};
const usageLabels: Record<string, string> = {search: "Поиск знания", template_open: "Открытие шаблона", case_open: "Открытие случая", basis_follow: "Проверка основания", template_download: "Скачивание шаблона", process_open: "Открытие процесса", process_started: "Начало исполнения процесса"};
export function OperationsAdmin() {
  const options = useData<ServiceScopeOptions>("/admin/service-accounts/options");
  const [registryId, setRegistryId] = useState("");
  const [state, setState] = useState("failed");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState<QueueEvent | null>(null);
  const [reason, setReason] = useState("");
  const metrics = useData<Metrics>(`/admin/operations/metrics${registryId ? "?registry_id=" + encodeURIComponent(registryId) : ""}`);
  const queue = useData<AdminPage<QueueEvent>>(`/admin/operations/queue?${new URLSearchParams({state, page: String(page), page_size: "20", ...(registryId ? {registry_id: registryId} : {})})}`);
  const action = useAction();
  const data = metrics.data;
  const hasData = !!data && (data.content.documents + data.content.files + data.numbers.visible_entries + data.execution.visible_instances + data.queue.pending + data.queue.failed + data.retrieval.requests + data.usage.reduce((sum, item) => sum + item.count, 0)) > 0;
  function refresh() {metrics.reload(); queue.reload(); options.reload();}
  return <section className="operations-admin"><div className="toolbar"><div><h1>Эксплуатация</h1><p>Сводные показатели и обработка событий в пределах текущего доступа к содержимому.</p></div><button type="button" onClick={refresh}>Обновить эксплуатационные данные</button></div>
    <label>Реестр эксплуатационных данных<select value={registryId} onChange={event => {setRegistryId(event.target.value); setPage(1); setRetry(null);}}><option value="">Все доступные реестры</option>{options.data?.registries.map(registry => <option key={registry.id} value={registry.id}>{registry.name}</option>)}</select></label>
    <Feedback error={options.error || metrics.error || queue.error || action.error} loading={metrics.loading || queue.loading} message={action.message}/>
    {data && <><h2>Состояние разрешённых данных</h2>{!hasData && <p className="empty-state">Нет доступных эксплуатационных данных.</p>}
      <div className="module-grid operational-metrics"><article className="module-card"><h3>Документы и файлы</h3><p>Карточек: {data.content.documents}</p><p>Оригиналов: {data.content.files}</p></article><article className="module-card"><h3>Номера и исполнение</h3><p>Записей номеров: {data.numbers.visible_entries}</p><p>Дел: {data.execution.visible_instances}</p></article><article className="module-card"><h3>Получение данных API</h3><p>Запросов: {data.retrieval.requests}</p><p>Среднее время: {data.retrieval.requests ? Math.round(data.retrieval.duration_ms_total / data.retrieval.requests) + " мс" : "Нет измерений"}</p><p>От поиска до открытия: {data.retrieval.time_to_open_count ? Math.round((data.retrieval.time_to_open_ms_total || 0) / data.retrieval.time_to_open_count) + " мс" : "Нет измерений"}</p></article><article className="module-card"><h3>Очередь событий</h3><p>Ожидают обработки: {data.queue.pending}</p><p>Ошибок обработки: {data.queue.failed}</p></article></div>
      <div className="operations-summary"><section className="panel"><h3>Извлечение содержания</h3><ul>{Object.entries(data.content.extractions).map(([key, count]) => <li key={key}>{extractionLabels[key] || "Прочие состояния"}: {count}</li>)}</ul></section><section className="panel"><h3>Использование знаний</h3>{data.usage.length ? <ul>{data.usage.map(item => <li key={item.action}>{usageLabels[item.action] || "Обращение к знанию"}: {item.count}</li>)}</ul> : <p>Обращений пока нет.</p>}<p className="muted">Без персональных профилей, текстов запросов и IP-адресов.</p></section></div>
    </>}
    <section className="panel"><h2>Обработка событий</h2><p>Ошибочные события сохраняются для диагностики. Повторная обработка не меняет исходные документы.</p><label>Состояние событий<select value={state} onChange={event => {setState(event.target.value); setPage(1); setRetry(null);}}><option value="failed">Ошибки обработки</option><option value="pending">Ожидают обработки</option></select></label>
      <div className="table-wrap"><table><thead><tr><th>Событие</th><th>Создано</th><th>Попытки</th><th>Диагностика</th><th>Действие</th></tr></thead><tbody>{queue.data?.items.map(event => <tr key={event.id}><td>{event.event_type}</td><td>{new Date(event.created_at).toLocaleString("ru-RU")}</td><td>{event.attempts}</td><td>{event.error_code || "—"}</td><td>{event.state === "failed" && <button type="button" disabled={action.busy} onClick={() => {setRetry(event); setReason("");}}>Повторить обработку</button>}</td></tr>)}</tbody></table></div>
      {queue.data?.items.length === 0 && <p>Событий по выбранным условиям нет.</p>}{queue.data && <div className="pagination"><span>Событий: {queue.data.total} · страница {page}</span><button type="button" disabled={page <= 1} onClick={() => {setPage(value => value - 1); setRetry(null);}}>Предыдущие события очереди</button><button type="button" disabled={page * queue.data.page_size >= queue.data.total} onClick={() => {setPage(value => value + 1); setRetry(null);}}>Следующие события очереди</button></div>}
      {retry && <form className="compact-form queue-retry" onSubmit={event => {event.preventDefault(); void action.run(async () => {await action.request(`/admin/operations/queue/${retry.id}/retry`, "POST", {reason}); setRetry(null); refresh();}, "Событие отправлено на повторную обработку");}}><h3>Повторить: {retry.event_type}</h3><label>Причина повтора события<textarea required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)}/></label><div className="button-row"><button disabled={action.busy || !reason.trim()}>Подтвердить повтор события</button><button type="button" onClick={() => setRetry(null)}>Отмена повтора события</button></div></form>}
    </section>
  </section>;
}
