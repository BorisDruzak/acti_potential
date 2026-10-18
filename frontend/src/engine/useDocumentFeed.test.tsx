import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useDocumentFeed } from "./useDocumentFeed";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
function fixture() {
  const pending: {body: {cursor: string | null; query: string}; signal: AbortSignal; resolve: (value: Response) => void}[] = [];
  vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>(resolve => pending.push({body: JSON.parse(String(init.body)), signal: init.signal!, resolve}))));
  const answer = async (index: number, ids: string[], cursor: string | null, status = 200) => {
    await act(async () => pending[index].resolve(new Response(JSON.stringify(status === 200 ? {items: ids.map(id => ({document: {id}, matches: []})), total: 4, next_cursor: cursor} : {error: {message: "Сбой загрузки"}}), {status})));
  };
  return {pending, answer};
}

it("appends and deduplicates cursor batches with one in-flight load and stops at exhaustion", async () => {
  const {pending, answer} = fixture();
  const {result} = renderHook(() => useDocumentFeed({query: "one"}, "csrf"));
  await answer(0, ["a", "b"], "opaque");
  act(() => {result.current.loadMore(); result.current.loadMore(); result.current.loadMore();});
  expect(pending).toHaveLength(2);
  expect(pending[1].body.cursor).toBe("opaque");
  await answer(1, ["b", "c"], null);
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["a", "b", "c"]);
  act(() => result.current.loadMore());
  expect(pending).toHaveLength(2);
});

it("aborts the previous query and ignores its late append response after reset", async () => {
  const {pending, answer} = fixture();
  const {result, rerender} = renderHook(({query}) => useDocumentFeed({query}, "csrf"), {initialProps: {query: "old"}});
  await answer(0, ["a"], "old-cursor");
  act(() => result.current.loadMore());
  rerender({query: "new"});
  expect(pending[1].signal.aborted).toBe(true);
  expect(pending[2].body).toEqual({query: "new", cursor: null});
  expect(result.current.data).toBeNull();
  await answer(2, ["new"], null);
  await answer(1, ["stale"], null);
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["new"]);
});

it("keeps existing results on append failure and retries the same cursor", async () => {
  const {pending, answer} = fixture();
  const {result} = renderHook(() => useDocumentFeed({query: "one"}, "csrf"));
  await answer(0, ["a"], "retry-cursor");
  act(() => result.current.loadMore());
  await answer(1, [], null, 503);
  expect(result.current.appendError).toBe("Сбой загрузки");
  expect(result.current.data?.items[0].document.id).toBe("a");
  act(() => result.current.loadMore());
  expect(pending[2].body.cursor).toBe("retry-cursor");
  await answer(2, ["b"], null);
  await waitFor(() => expect(result.current.appendError).toBe(""));
});

it("does not loop when a server repeats an already consumed cursor", async () => {
  const {pending, answer} = fixture();
  const {result} = renderHook(() => useDocumentFeed({}, "csrf"));
  await answer(0, ["a"], "same");
  act(() => result.current.loadMore());
  await answer(1, ["b"], "same");
  act(() => result.current.loadMore());
  expect(pending).toHaveLength(2);
  expect(result.current.data?.next_cursor).toBeNull();
});

it("refreshes every loaded batch atomically and drops disappeared rows without clearing the feed", async () => {
  const {pending, answer} = fixture();
  const {result} = renderHook(() => useDocumentFeed({query: "one"}, "csrf"));
  await answer(0, ["a", "b"], "second");
  act(() => result.current.loadMore());
  await answer(1, ["c"], "third");
  act(() => result.current.reload());
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["a", "b", "c"]);
  await answer(2, ["b", "d"], "second-new");
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["a", "b", "c"]);
  await answer(3, ["e"], "third-new");
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["b", "d", "e"]);
  expect(pending).toHaveLength(4);
});

it("keeps the explicit search duration and search spinner unchanged during paging and background refresh", async () => {
  const {answer} = fixture();
  const clock = vi.spyOn(performance, "now").mockReturnValue(100);
  const {result} = renderHook(() => useDocumentFeed({query: "one"}, "csrf"));
  expect(result.current.searching).toBe(true);
  clock.mockReturnValue(350);
  await answer(0, ["a"], "second");
  expect(result.current.durationMs).toBe(250);
  expect(result.current.searching).toBe(false);
  clock.mockReturnValue(1000);
  act(() => result.current.loadMore());
  expect(result.current.searching).toBe(false);
  expect(result.current.startedAt).toBeNull();
  clock.mockReturnValue(1800);
  await answer(1, ["b"], null);
  expect(result.current.durationMs).toBe(250);
  act(() => result.current.reload());
  expect(result.current.searching).toBe(false);
  clock.mockReturnValue(3000);
  await answer(2, ["a", "b"], null);
  expect(result.current.durationMs).toBe(250);
  clock.mockRestore();
});

it("retries an explicit search while a background request is pending and ignores its late response", async () => {
  const {pending, answer} = fixture();
  const {result} = renderHook(() => useDocumentFeed({query: "one"}, "csrf"));
  await answer(0, ["a"], null);
  act(() => result.current.reload());
  act(() => result.current.retry());
  expect(pending[1].signal.aborted).toBe(true);
  expect(result.current.searching).toBe(true);
  await answer(2, ["retry"], null);
  const measured = result.current.durationMs;
  await answer(1, ["stale"], null);
  expect(result.current.data?.items.map(hit => hit.document.id)).toEqual(["retry"]);
  expect(result.current.durationMs).toBe(measured);
});
