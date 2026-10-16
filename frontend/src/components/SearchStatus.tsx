import { useEffect, useState } from "react";

export function SearchStatus({loading, startedAt, durationMs, error}: {
  loading: boolean; startedAt: number | null; durationMs: number | null; error: string;
}) {
  const [clock, setClock] = useState({start: startedAt, elapsed: 0});
  useEffect(() => {
    if (!loading || startedAt === null) return;
    const update = () => setClock({start: startedAt, elapsed: Math.max(0, performance.now() - startedAt)});
    update();
    const timer = window.setInterval(update, 100);
    return () => window.clearInterval(timer);
  }, [loading, startedAt]);
  const seconds = (value: number) => (value / 1000).toFixed(2).replace(".", ",") + " с";
  return <div className={"search-status" + (loading ? " searching" : "")} title="Время от отправки запроса до получения ответа, включая сеть.">
    {loading ? <>
      <span className="search-spinner" aria-hidden="true" />
      <span role="status">Идёт поиск…</span>
      <span aria-live="off">{seconds(clock.start === startedAt ? clock.elapsed : 0)}</span>
    </> : durationMs !== null ? <span role="status">{error ? "Поиск не выполнен · " : "Время поиска: "}{seconds(durationMs)}</span> : null}
  </div>;
}
