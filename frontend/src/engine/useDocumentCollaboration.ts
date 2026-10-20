import { useEffect, useRef, useState } from "react";
import { ApiError, apiRequest } from "../api/client";
export type Collaboration = {revision: number; updated_at: string; updated_by: {id: string; display_name: string} | null; participants: {id: string; display_name: string; editing: boolean}[]};
export function useDocumentCollaboration(id: string, revision: number, mode: "editor" | "viewer", csrfToken: string, editing: boolean, dirty: boolean, onRefresh: () => void) {
  const [state, setState] = useState<Collaboration | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const client = useRef(crypto.randomUUID());
  const latest = useRef({dirty, editing, onRefresh}); latest.current = {dirty, editing, onRefresh};
  const refreshed = useRef(revision);
  const owner = useRef(id);
  useEffect(() => {
    const controller = new AbortController(); let busy = false; let denied = false; let heartbeatAt = -Infinity;
    refreshed.current = revision;
    if (owner.current !== id) {owner.current = id; setState(null);}
    setUnavailable(false);
    const poll = async (heartbeat = false) => {
      if (document.visibilityState === "hidden" || busy || denied || controller.signal.aborted) return;
      busy = true;
      try {
        const join = heartbeat || Date.now() - heartbeatAt >= 15000;
        const result = await apiRequest<Collaboration>(`/documents/${id}/collaboration?mode=${mode}`, {signal: controller.signal,
          ...(join ? {method: "POST", headers: {"X-CSRF-Token": csrfToken}, body: JSON.stringify({client_id: client.current, editing: latest.current.editing})} : {})});
        if (controller.signal.aborted || !Number.isInteger(result.revision)) return;
        if (join) heartbeatAt = Date.now();
        setUnavailable(false);
        setState({...result, participants: Array.isArray(result.participants) ? result.participants : []});
        if (result.revision !== revision && !latest.current.dirty && refreshed.current !== result.revision) {
          refreshed.current = result.revision; latest.current.onRefresh();
        }
      } catch (failure) {
        if (!controller.signal.aborted && failure instanceof ApiError && [403, 404].includes(failure.status)) {
          denied = true;
          setUnavailable(true); if (!latest.current.dirty) latest.current.onRefresh();
        }
      } finally {busy = false;}
    };
    void poll(true);
    const timer = setInterval(() => void poll(), 5000);
    const visible = () => {if (document.visibilityState !== "hidden") void poll(true);};
    document.addEventListener("visibilitychange", visible);
    return () => {controller.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", visible);};
  }, [id, revision, mode, csrfToken, editing]);
  return {state, unavailable, remote: state && state.revision !== revision ? state : null};
}
