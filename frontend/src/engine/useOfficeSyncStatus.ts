import { useMemo, useSyncExternalStore } from "react";
import { ApiError, apiRequest } from "../api/client";
import type { OfficeSyncStatus } from "../components/OfficeSync";

type Snapshot = {status: OfficeSyncStatus | null; error: string};
const empty: Snapshot = {status: null, error: ""};
const stores = new Map<string, ReturnType<typeof makeStore>>();

function makeStore(path: string) {
  let snapshot = empty;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const listeners = new Set<() => void>();
  const publish = (next: Snapshot) => {snapshot = next; listeners.forEach(listener => listener());};
  async function refresh() {
    if (!listeners.size) return;
    clearTimeout(timer);
    controller?.abort();
    const active = new AbortController();
    controller = active;
    try {
      const status = await apiRequest<OfficeSyncStatus>(path, {signal: active.signal});
      if (!active.signal.aborted) publish({status, error: ""});
    } catch (failure) {
      const denied = failure instanceof ApiError && [401, 403, 404].includes(failure.status);
      if (!active.signal.aborted) publish({status: snapshot.status, error: denied
        ? "Доступ к оригиналу отозван или он недоступен. Локальные изменения сохраните через ACTI Sync."
        : "Нет связи с ACTI. Состояние сохранения неизвестно."});
    } finally {
      if (!active.signal.aborted && listeners.size) timer = setTimeout(() => void refresh(), 7000);
    }
  }
  return {
    getSnapshot: () => snapshot,
    refresh: () => void refresh(),
    subscribe(listener: () => void) {
      listeners.add(listener);
      if (listeners.size === 1) void refresh();
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          controller?.abort(); clearTimeout(timer);
          // Never keep an old user's capabilities or contact data across logout/navigation.
          snapshot = empty;
        }
      };
    },
  };
}

const disabled = {getSnapshot: () => empty, subscribe: () => () => {}, refresh: () => {}};
export function useOfficeSyncStatus(fileId: string, mode: "editor" | "viewer", enabled: boolean) {
  const store = useMemo(() => {
    if (!enabled) return disabled;
    const path = `/office-sync/files/${fileId}/status?mode=${mode}`;
    let current = stores.get(path);
    if (!current) {current = makeStore(path); stores.set(path, current);}
    return current;
  }, [fileId, mode, enabled]);
  return {...useSyncExternalStore(store.subscribe, store.getSnapshot, () => empty), refresh: store.refresh};
}
