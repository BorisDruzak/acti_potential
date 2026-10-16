import { useState } from "react";
import { Feedback } from "../components/Feedback";
import { useData } from "../engine/useData";

type Activity = {id: string; created_at: string; action: string; object_type: string};
type ActivityPage = {items: Activity[]; total: number; page: number; page_size: number};
const actions: Record<string, string> = {
  "auth.login": "Вход в ACTI", "auth.logout": "Выход из ACTI",
  "identity.password_changed": "Изменён пароль",
  "document.created": "Создан документ", "document.updated": "Изменён документ",
  "document.metadata.updated": "Изменены реквизиты документа", "document.version.created": "Добавлена версия документа",
  "document.exported": "Выгружены документы",
  "document.file.uploaded": "Добавлен файл документа", "document.file.downloaded": "Скачан файл документа",
  "document.access.changed": "Изменён доступ к документу",
  "number.reserved": "Зарезервирован номер", "number.assigned": "Документу присвоен номер",
  "number.cancelled": "Отменён номер", "number.exported": "Выгружен реестр номеров",
  "number.void": "Аннулирован номер", "number.expired": "Истёк срок резерва номера",
  "number.series.created": "Создана серия номеров", "number.series.updated": "Изменена серия номеров",
  "identity.user_created": "Создана учётная запись", "identity.user_updated": "Изменена учётная запись",
  "identity.registry_access_changed": "Изменён доступ к реестру",
};
const objects: Record<string, string> = {
  document: "Документы", document_file: "Файлы документов", session: "Учётная запись", user: "Учётные записи",
  registry: "Реестры", number_entry: "Реестр номеров", number_series: "Серии номеров",
  process: "Процессы", process_instance: "Исполнение процессов", knowledge_case: "Практические случаи",
};
function description(item: Activity) {
  if (actions[item.action]) return actions[item.action];
  const verb = item.action.split(".").at(-1);
  const verbs: Record<string, string> = {created: "Создание", updated: "Изменение", published: "Публикация", archived: "Архивирование", deleted: "Удаление", started: "Начало работы", completed: "Завершение работы", reserved: "Резервирование номера", assigned: "Присвоение номера", released: "Освобождение номера", voided: "Аннулирование номера"};
  return `${verbs[verb ?? ""] ?? "Действие"} · ${objects[item.object_type] ?? "Работа в ACTI"}`;
}
const emptyFilters = {category: "", from: "", to: ""};
export function UserHistory() {
  const [draft, setDraft] = useState(emptyFilters);
  const [filters, setFilters] = useState(emptyFilters);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const params = new URLSearchParams({page: String(page), page_size: "20"});
  if (filters.category) params.set("category", filters.category);
  if (filters.from) params.set("since", new Date(`${filters.from}T00:00:00`).toISOString());
  if (filters.to) {
    const end = new Date(`${filters.to}T00:00:00`);
    end.setDate(end.getDate() + 1);
    params.set("until", end.toISOString());
  }
  const data = useData<ActivityPage>(`/users/me/activity?${params}`);
  return <section className="user-history">
    <div className="toolbar"><div><span className="eyebrow">ЛИЧНАЯ ИСТОРИЯ</span><h1>Моя работа</h1>
      <p>Ваши действия в ACTI: документы, файлы, номера и учётная запись.</p></div>
      <button type="button" onClick={data.reload} disabled={data.loading}>Обновить историю</button></div>
    <form className="panel history-filters" onSubmit={event => {
      event.preventDefault();
      if (draft.from && draft.to && draft.from > draft.to) {setError("Начало периода должно быть не позже окончания."); return;}
      setError(""); setFilters(draft); setPage(1);
    }}>
      <label>Раздел истории<select value={draft.category} onChange={event => setDraft({...draft, category: event.target.value})}>
        <option value="">Все действия</option><option value="documents">Документы и файлы</option>
        <option value="numbers">Реестр номеров</option><option value="account">Учётная запись</option><option value="other">Другие действия</option>
      </select></label>
      <label>Период с<input type="date" value={draft.from} onChange={event => setDraft({...draft, from: event.target.value})}/></label>
      <label>Период по<input type="date" value={draft.to} onChange={event => setDraft({...draft, to: event.target.value})}/></label>
      <div className="history-filter-actions"><button type="submit" className="primary">Показать</button>
        <button type="button" onClick={() => {setDraft(emptyFilters); setFilters(emptyFilters); setPage(1); setError("");}}>Сбросить</button></div>
    </form>
    <Feedback error={error || data.error} loading={data.loading}/>
    {!data.loading && !data.error && data.data && <div className="panel history-panel">
      <div className="history-summary"><h2>История действий</h2><span>Найдено: {data.data.total}</span></div>
      {data.data.items.length ? <ol className="activity-list" aria-label="История моих действий">{data.data.items.map(item => <li key={item.id}>
        <span className="activity-dot" aria-hidden="true"/><div><strong>{description(item)}</strong><span>{objects[item.object_type] ?? "ACTI"}</span></div>
        <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("ru-RU", {day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit"})}</time>
      </li>)}</ol> : <div className="history-empty"><h3>За выбранный период действий нет.</h3><p>Здесь появятся ваши действия, когда вы начнёте работать в ACTI.</p></div>}
      {data.data.total > 0 && <div className="pagination"><span>Страница {data.data.page} из {Math.max(1, Math.ceil(data.data.total / data.data.page_size))}</span>
        <button type="button" aria-label="Предыдущая страница истории" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Назад</button>
        <button type="button" aria-label="Следующая страница истории" disabled={page * data.data.page_size >= data.data.total} onClick={() => setPage(value => value + 1)}>Далее</button></div>}
    </div>}
    <p className="history-note">Показаны только ваши действия, сохранённые системой. Время указано в часовом поясе вашего устройства.</p>
  </section>;
}
