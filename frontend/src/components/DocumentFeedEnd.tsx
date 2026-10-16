import { useEffect, useRef } from "react";

export function DocumentFeedEnd({hasMore, loading, error, onLoad, enabled}: {hasMore: boolean; loading: boolean; error: string; onLoad: () => void; enabled: boolean}) {
  const marker = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!enabled || !hasMore || loading || error || !marker.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {if (entries.some(entry => entry.isIntersecting)) onLoad();}, {root: marker.current.closest(".document-pane-scroll"), rootMargin: "180px"});
    observer.observe(marker.current);
    return () => observer.disconnect();
  }, [enabled, hasMore, loading, error, onLoad]);
  return <div ref={marker} className="document-feed-end" aria-live="polite">
    {loading ? "Загрузка документов…" : error ? <><p role="alert">{error}</p><button type="button" onClick={onLoad}>Повторить загрузку</button></> : !hasMore ? "Все результаты загружены" : null}
  </div>;
}
