import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
export function useData<T>(path: string | null, request?: {body: unknown; csrfToken: string; retainOnReload?: boolean}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [durationMs, setDurationMs] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  const body = request ? JSON.stringify(request.body) : undefined;
  const csrfToken = request?.csrfToken;
  const retainOnReload = request?.retainOnReload;
  const previousKey = useRef("");
  useEffect(() => {
    const key = JSON.stringify([path, body, csrfToken]);
    if (!retainOnReload || previousKey.current !== key || !path) setData(null);
    previousKey.current = key;
    setError("");
    setStartedAt(null);
    setDurationMs(null);
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const started = performance.now();
    setStartedAt(started);
    setLoading(true);
    apiRequest<T>(path, { signal: controller.signal, ...(body !== undefined ? {
      method: "POST", body, headers: {"X-CSRF-Token": csrfToken || ""},
    } : {}) })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDurationMs(Math.max(0, performance.now() - started));
          setStartedAt(null);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [path, revision, body, csrfToken, retainOnReload]);
  return { data, error, loading, reload, startedAt, durationMs };
}
export function useAction() {
  const auth = useAuth();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function request<T>(
    path: string,
    method: string,
    body?: unknown,
    timeoutMs?: number,
  ): Promise<T> {
    return apiRequest<T>(
      path,
      {
        method,
        headers: { "X-CSRF-Token": auth.user?.csrf_token || "" },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      },
      timeoutMs,
    );
  }
  async function run(
    action: () => Promise<void>,
    success = "Изменения сохранены",
  ) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Не удалось выполнить действие",
      );
    } finally {
      setBusy(false);
    }
  }
  return { request, run, error, message, busy, setError, setMessage };
}
