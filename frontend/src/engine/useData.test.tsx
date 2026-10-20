import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useData } from "./useData";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it("retains a loaded GET result during refresh but clears it when access is revoked", async () => {
  const pending: ((response: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => pending.push(resolve))));
  const {result} = renderHook(() => useData<{title: string}>("/documents/a", {retainOnReload: true}));
  await act(async () => pending[0](new Response(JSON.stringify({title: "Сохранённая карточка"}))));
  const initial = result.current.data;
  act(() => result.current.reload());
  expect(result.current.data).toBe(initial);
  await act(async () => pending[1](new Response(JSON.stringify({error: {message: "Доступ отозван"}}), {status: 403})));
  expect(result.current.data).toBeNull();
  expect(result.current.error).toBe("Доступ отозван");
});

it("never retains another document's data when the request identity changes", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => url.endsWith("/a")
    ? Promise.resolve(new Response(JSON.stringify({id: "a"})))
    : new Promise<Response>(() => {})));
  const {result, rerender} = renderHook(({id}) => useData<{id: string}>(`/documents/${id}`, {retainOnReload: true}), {initialProps: {id: "a"}});
  await act(async () => {});
  expect(result.current.data?.id).toBe("a");
  rerender({id: "b"});
  expect(result.current.data).toBeNull();
});
