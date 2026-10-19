import { afterEach, expect, it, vi } from "vitest";
import { discoverLocalBridge, postLocalTicket } from "./localOfficeSync";

const challenge = "c".repeat(43);
const ticket = "t".repeat(43);
const status = () => new Response(JSON.stringify({version: "1.3.0", challenge}), {headers: {"Content-Type": "application/json"}});
afterEach(() => {vi.unstubAllGlobals(); vi.useRealTimers();});

it("discovers only the bounded IPv4 loopback ports without cookies or redirects", async () => {
  const fetcher = vi.fn(async (url: string) => url === "http://127.0.0.1:43142/v1/status" ? status() : Promise.reject(new TypeError("unavailable")));
  vi.stubGlobal("fetch", fetcher);
  const bridge = await discoverLocalBridge(new AbortController().signal);
  expect(bridge).toEqual({baseUrl: "http://127.0.0.1:43142", version: "1.3.0", challenge});
  expect(fetcher).toHaveBeenCalledTimes(16);
  for (const [url, init] of fetcher.mock.calls as unknown as [string, RequestInit][]) {
    expect(url).toMatch(/^http:\/\/127\.0\.0\.1:431(?:2[7-9]|3[0-9]|4[0-2])\/v1\/status$/);
    expect(init).toMatchObject({credentials: "omit", cache: "no-store", redirect: "error"});
  }
});

it.each([{version: "1.2.9", challenge}, {version: "2.0.0", challenge}, {version: "1.3.0", challenge: "short"}, {version: "1.3.0", challenge, baseUrl: "https://foreign.test"}])("ignores incompatible or malformed status %j", async body => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body))));
  expect(await discoverLocalBridge(new AbortController().signal)).toBeNull();
});

it("posts only the validated ticket and fresh challenge and rejects challenge replay", async () => {
  const fetcher = vi.fn(async () => new Response('{"ok":true}'));
  vi.stubGlobal("fetch", fetcher);
  const bridge = {baseUrl: "http://127.0.0.1:43127", version: "1.3.0", challenge};
  await postLocalTicket(bridge, "launch", `acti-sync://launch?ticket=${ticket}`, "2099-01-01", new AbortController().signal);
  expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:43127/v1/launch", expect.objectContaining({method: "POST", credentials: "omit", redirect: "error", body: JSON.stringify({ticket, challenge})}));
  await expect(postLocalTicket(bridge, "launch", `acti-sync://launch?ticket=${ticket}`, "2099-01-01", new AbortController().signal)).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  ["https://foreign.test", `acti-sync://launch?ticket=${ticket}`, "2099-01-01"],
  ["http://127.0.0.1:43126", `acti-sync://launch?ticket=${ticket}`, "2099-01-01"],
  ["http://127.0.0.1:43127", `acti-sync://connect?ticket=${ticket}`, "2099-01-01"],
  ["http://127.0.0.1:43127", `acti-sync://launch?ticket=${ticket}&origin=x`, "2099-01-01"],
  ["http://127.0.0.1:43127", `acti-sync://launch?ticket=${ticket}`, "2020-01-01"],
  ["http://127.0.0.1:43127\n", `acti-sync://launch?ticket=${ticket}`, "2099-01-01"],
  ["http://127.0.0.1:43127", `acti-sync://launch?ticket=${ticket}\n`, "2099-01-01"],
])("rejects an untrusted endpoint, operation or expiry before POST", async (baseUrl, uri, expires) => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(postLocalTicket({baseUrl, version: "1.3.0", challenge}, "launch", uri, expires, new AbortController().signal)).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});

it("aborts discovery and cannot post a late result after logout", async () => {
  const controller = new AbortController();
  vi.stubGlobal("fetch", vi.fn(async () => {controller.abort(); return status();}));
  expect(await discoverLocalBridge(controller.signal)).toBeNull();
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(postLocalTicket({baseUrl: "http://127.0.0.1:43127", version: "1.3.0", challenge}, "connect", `acti-sync://connect?ticket=${ticket}`, "2099-01-01", controller.signal)).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});

it("rejects an unknown operation at runtime before any local POST", async () => {
  const fetcher = vi.fn(async () => new Response('{"ok":true}')); vi.stubGlobal("fetch", fetcher);
  await expect(postLocalTicket({baseUrl: "http://127.0.0.1:43127", version: "1.3.0", challenge: "j".repeat(43)},
    "reveal" as "launch", `acti-sync://reveal?ticket=${ticket}`, "2099-01-01", new AbortController().signal)).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});

it("bounds hanging status requests and returns quietly when no agent is installed", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))));
  const result = discoverLocalBridge(new AbortController().signal);
  await vi.advanceTimersByTimeAsync(1500);
  expect(await result).toBeNull();
});

it("stops reading an oversized streamed response before it can exhaust memory", async () => {
  const cancel = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => {
    let chunks = 0;
    return new Response(new ReadableStream({pull(controller) {if (++chunks < 32) controller.enqueue(new Uint8Array(4096)); else controller.close();}, cancel}));
  }));
  expect(await discoverLocalBridge(new AbortController().signal)).toBeNull();
  expect(cancel).toHaveBeenCalledTimes(16);
});
