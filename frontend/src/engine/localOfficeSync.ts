export type LocalBridge = {baseUrl: string; version: string; challenge: string};
const endpoint = /^http:\/\/127\.0\.0\.1:431(?:2[7-9]|3[0-9]|4[0-2])$/;
const nonce = /^[A-Za-z0-9_-]{43}$/;
const compatible = (version: string) => /^1\.(?:[3-9]|[1-9]\d+)\.(?:0|[1-9]\d*)$/.test(version);
const consumed = new Set<string>();

async function localRequest(url: string, signal: AbortSignal, init: RequestInit = {}, timeout = 1200) {
  signal.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, {once: true});
  const timer = setTimeout(abort, timeout);
  try {
    const response = await fetch(url, {...init, signal: controller.signal, credentials: "omit", redirect: "error", cache: "no-store", referrerPolicy: "no-referrer"});
    if (!response.ok || response.redirected || Number(response.headers.get("Content-Length")) > 2048) throw new Error("bridge_unavailable");
    const reader = response.body?.getReader();
    const decoder = new TextDecoder("utf-8", {fatal: true});
    let body = "", size = 0;
    if (reader) {
      try {
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 2048) {await reader.cancel(); throw new Error("invalid_bridge_response");}
          body += decoder.decode(value, {stream: true});
        }
        body += decoder.decode();
      } finally {reader.releaseLock();}
    }
    signal.throwIfAborted();
    if (body.length > 2048) throw new Error("invalid_bridge_response");
    return body ? JSON.parse(body) as unknown : null;
  } finally {clearTimeout(timer); signal.removeEventListener("abort", abort);}
}

/** No auth material is issued until an installed, compatible bridge answers. */
export async function discoverLocalBridge(signal: AbortSignal): Promise<LocalBridge | null> {
  if (signal.aborted) return null;
  const candidates = await Promise.all(Array.from({length: 16}, async (_, offset) => {
    const baseUrl = `http://127.0.0.1:${43127 + offset}`;
    try {
      const value = await localRequest(`${baseUrl}/v1/status`, signal);
      if (!value || typeof value !== "object" || Object.keys(value).sort().join(",") !== "challenge,version") return null;
      const {version, challenge} = value as Record<string, unknown>;
      return typeof version === "string" && compatible(version) && typeof challenge === "string" && nonce.test(challenge)
        ? {baseUrl, version, challenge} : null;
    } catch {return null;}
  }));
  return signal.aborted ? null : candidates.find(value => value !== null) || null;
}

/** A challenge is consumed before I/O, including an ambiguous network failure. Never replay a ticket. */
export async function postLocalTicket(bridge: LocalBridge, operation: "connect" | "launch", uri: string, expiresAt: string, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (operation !== "connect" && operation !== "launch") throw new Error("invalid_bridge_operation");
  const match = new RegExp(`^acti-sync:\\/\\/${operation}\\?ticket=([A-Za-z0-9_-]{43})$`).exec(uri);
  if (!endpoint.test(bridge.baseUrl) || !compatible(bridge.version) || !nonce.test(bridge.challenge) ||
      !match || !(Date.parse(expiresAt) > Date.now()) || consumed.has(bridge.challenge)) throw new Error("invalid_bridge_ticket");
  consumed.add(bridge.challenge);
  if (consumed.size > 256) consumed.delete(consumed.values().next().value!);
  const result = await localRequest(`${bridge.baseUrl}/v1/${operation}`, signal, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ticket: match[1], challenge: bridge.challenge})}, 45000);
  if (!result || typeof result !== "object" || (result as {ok?: unknown}).ok !== true) throw new Error("bridge_unavailable");
}
