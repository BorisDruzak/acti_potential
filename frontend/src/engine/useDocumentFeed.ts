import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api/client";
import type { ContentHit } from "./content";

type Batch = {items: ContentHit[]; total: number; next_cursor: string | null};
type Feed = {key: string; data: Batch | null; loading: boolean; refreshing: boolean; searching: boolean; searchError: string; error: string; appendError: string; startedAt: number | null; durationMs: number | null};
type Generation = {key: string; controller: AbortController; busy: boolean; data: Batch | null; consumed: Set<string>; pages: number};
const empty = (key: string): Feed => ({key, data: null, loading: true, refreshing: false, searching: true, searchError: "", error: "", appendError: "", startedAt: null, durationMs: null});

/** Replace all previously loaded batches atomically on refresh; cursors never cross generations. */
export function useDocumentFeed(body: unknown, csrfToken: string) {
  const key = JSON.stringify([body, csrfToken]);
  const [state, setState] = useState<Feed>(() => empty(key));
  const active = useRef<Generation | null>(null);
  const request = useCallback(async (generation: Generation, cursor: string | null, refresh = false, measure = false) => {
    if (generation.busy || generation.controller.signal.aborted) return;
    generation.busy = true;
    const started = measure ? performance.now() : null;
    setState(current => ({...current, loading: !refresh, refreshing: refresh, ...(measure ? {searching: true, startedAt: started, searchError: ""} : {}), error: "", appendError: ""}));
    try {
      const [parameters, token] = JSON.parse(generation.key);
      const rows = new Map((refresh ? [] : generation.data?.items || []).map(hit => [hit.document.id, hit]));
      const consumed = refresh ? new Set<string>() : new Set(generation.consumed);
      let next = cursor; let batch: Batch; let fetched = 0;
      const target = refresh ? Math.max(1, generation.pages) : 1;
      do {
        batch = await apiRequest<Batch>("/search", {method: "POST", body: JSON.stringify({...parameters, cursor: next}), headers: {"X-CSRF-Token": token}, signal: generation.controller.signal});
        if (active.current !== generation || generation.controller.signal.aborted) return;
        if (next) consumed.add(next);
        for (const hit of batch.items) rows.set(hit.document.id, hit);
        next = batch.next_cursor && !consumed.has(batch.next_cursor) ? batch.next_cursor : null;
        fetched++;
      } while (refresh && fetched < target && next);
      generation.pages = refresh ? fetched : generation.pages + fetched;
      generation.consumed = consumed;
      const updated = {...batch!, items: [...rows.values()], next_cursor: next};
      if (JSON.stringify(updated) !== JSON.stringify(generation.data)) generation.data = updated;
      setState(current => ({...current, data: generation.data}));
    } catch (failure) {
      if (active.current === generation && !generation.controller.signal.aborted) {
        const message = failure instanceof Error ? failure.message : "Не удалось загрузить документы";
        setState(current => ({...current, ...(cursor ? {appendError: message} : {error: message}), ...(measure ? {searchError: message} : {})}));
      }
    } finally {
      generation.busy = false;
      if (active.current === generation && !generation.controller.signal.aborted) setState(current => ({...current, loading: false, refreshing: false, ...(started !== null ? {searching: false, startedAt: null, durationMs: Math.max(0, performance.now() - started)} : {})}));
    }
  }, []);
  const begin = useCallback(() => {
    active.current?.controller.abort();
    const generation: Generation = {key, controller: new AbortController(), busy: false, data: null, consumed: new Set(), pages: 0};
    active.current = generation;
    setState(empty(key));
    void request(generation, null, false, true);
    return generation;
  }, [key, request]);
  useEffect(() => {
    const generation = begin();
    return () => generation.controller.abort();
  }, [begin]);
  const loadMore = useCallback(() => {
    const generation = active.current;
    if (generation?.key === key && generation.data?.next_cursor) void request(generation, generation.data.next_cursor);
  }, [key, request]);
  const reload = useCallback(() => {
    const generation = active.current;
    if (generation?.key !== key || generation.busy) return;
    void request(generation, null, true);
  }, [key, request]);
  const retry = useCallback(() => {begin();}, [begin]);
  return {...(state.key === key ? state : empty(key)), loadMore, reload, retry};
}
