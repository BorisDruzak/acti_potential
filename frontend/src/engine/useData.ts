import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
export function useData<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    setData(null);
    setError("");
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    apiRequest<T>(path, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, reload };
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
