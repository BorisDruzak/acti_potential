export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId = "",
  ) {
    super(message);
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 10000,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (typeof init.body === "string" && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  let response: Response;
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener("abort", abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  const timer = setTimeout(
    abort,
    init.body instanceof Blob ? 120000 : timeoutMs,
  );
  try {
    response = await fetch("/api/v1" + path, {
      ...init,
      headers,
      credentials: "same-origin",
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "Не удалось связаться с сервером. Проверьте соединение и повторите попытку.",
    );
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener("abort", abort);
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("acti:unauthorized"));
    throw new ApiError(
      response.status,
      body?.error?.code ?? "request_failed",
      body?.error?.message ?? "Запрос не выполнен",
      body?.request_id ?? response.headers.get("X-Request-ID") ?? "",
    );
  }
  return body as T;
}

export async function apiUpload<T>(
  path: string,
  body: Blob,
  csrfToken: string,
): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    body,
    headers: {
      "Content-Type": body.type || "application/octet-stream",
      "X-CSRF-Token": csrfToken,
    },
  });
}

export async function encodeFile(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
}

export async function apiDownload(path: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, { credentials: "same-origin" });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "Не удалось скачать файл. Проверьте соединение.",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (response.status === 401)
      window.dispatchEvent(new Event("acti:unauthorized"));
    throw new ApiError(
      response.status,
      body?.error?.code ?? "request_failed",
      body?.error?.message ?? "Файл недоступен",
    );
  }
  return response.blob();
}

export async function saveDownload(
  id: string,
  name: string,
  mode?: "editor" | "viewer",
) {
  const blob = await apiDownload(
    `/files/${id}/download${mode ? "?mode=" + mode : ""}`,
  );
  saveBlob(blob, name);
}

export async function saveExport(path: string, name: string) {
  saveBlob(await apiDownload(path), name);
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
