import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api/client";
import type { ContentHit } from "./content";

type Batch = {items: ContentHit[]; total: number; next_cursor: string | null};
type Feed = {key: string; data: Batch | null; loading: boolean; error: string; appendError: string; startedAt: number | null; durationMs: number | null};
const empty = (key: string): Feed => ({key, data: null, loading: true, error: "", appendError: "", startedAt: null, durationMs: null});

/** Opaque cursors belong to one query generation. Layout and selected cards do not. */
export function useDocumentFeed(body: unknown, csrfToken: string) {
  const key = JSON.stringify([body, csrfToken]);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<Feed>(() => empty(key));
  const active = useRef<{key: string; controller: AbortController; busy: boolean; data: Batch | null; consumed: Set<string>} | null>(null);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const request = useCallback(async (generation: NonNullable<typeof active.current>, cursor: string | null) => {
    if (generation.busy || generation.controller.signal.aborted) return;
    generation.busy = true;
    const started = performance.now();
    setState(current => ({...current, loading: true, startedAt: started, error: "", appendError: ""}));
    try {
      const [parameters, token] = JSON.parse(generation.key);
      const batch = await apiRequest<Batch>("/search", {method: "POST", body: JSON.stringify({...parameters, cursor}), headers: {"X-CSRF-Token": token}, signal: generation.controller.signal});
      if (active.current !== generation || generation.controller.signal.aborted) return;
      if (cursor) generation.consumed.add(cursor);
      const rows = new Map((generation.data?.items || []).map(hit => [hit.document.id, hit]));
      for (const hit of batch.items) rows.set(hit.document.id, hit);
      generation.data = {...batch, items: [...rows.values()], next_cursor: batch.next_cursor && !generation.consumed.has(batch.next_cursor) ? batch.next_cursor : null};
      setState(current => ({...current, data: generation.data}));
    } catch (failure) {
      if (active.current === generation && !generation.controller.signal.aborted) {
        const message = failure instanceof Error ? failure.message : "Не удалось загрузить документы";
        setState(current => ({...current, ...(cursor ? {appendError: message} : {error: message})}));
      }
    } finally {
      generation.busy = false;
      if (active.current === generation && !generation.controller.signal.aborted) setState(current => ({...current, loading: false, startedAt: null, durationMs: Math.max(0, performance.now() - started)}));
    }
  }, []);
  useEffect(() => {
    const generation = {key, controller: new AbortController(), busy: false, data: null, consumed: new Set<string>()};
    active.current = generation;
    setState(empty(key));
    void request(generation, null);
    return () => generation.controller.abort();
  }, [key, revision, request]);
  const loadMore = useCallback(() => {
    const generation = active.current;
    if (generation?.key === key && generation.data?.next_cursor) void request(generation, generation.data.next_cursor);
  }, [key, request]);
  return {...(state.key === key ? state : empty(key)), loadMore, reload};
}
