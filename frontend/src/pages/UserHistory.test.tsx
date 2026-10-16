import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UserHistory } from "./UserHistory";
import { App } from "../App";
import { AuthProvider } from "../auth/AuthProvider";

afterEach(() => {cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/");});
it.each(["editor", "viewer"])("opens personal history from the %s workspace instead of execution tasks", async role => {
  window.history.replaceState(null, "", role === "viewer" ? "/view" : "/work");
  const fetch = vi.fn(async (url: string) => {
    const data = url.endsWith("/users/me") ? {id: "u", display_name: "Сотрудник", roles: [role], csrf_token: "csrf"}
      : url.includes("/users/me/activity?") ? {items: [{id: "a", created_at: "2026-10-16T07:00:00Z", action: "document.created", object_type: "document"}], total: 1, page: 1, page_size: 20} : [];
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetch);
  render(<AuthProvider><App/></AuthProvider>);
  await screen.findByRole("heading", {name: "Документы"});
  expect(screen.queryByRole("button", {name: "Процессы"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Моя работа"}));
  await screen.findByText("Создан документ");
  expect(fetch.mock.calls.some(([url]) => url.includes("/execution/"))).toBe(false);
});
it("shows personal actions with readable labels and applies filters and pagination", async () => {
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify({
    items: [{id: "a", created_at: "2026-10-16T07:00:00Z", action: "document.created", object_type: "document"}],
    total: 25, page: Number(new URL(url, "https://test").searchParams.get("page")), page_size: 20,
  })));
  vi.stubGlobal("fetch", fetch);
  render(<UserHistory/>);
  await screen.findByText("Создан документ");
  expect(screen.queryByText("document.created")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Следующая страница истории"}));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toContain("page=2"));
  fireEvent.change(screen.getByLabelText("Раздел истории"), {target: {value: "documents"}});
  fireEvent.change(screen.getByLabelText("Период с"), {target: {value: "2026-10-15"}});
  fireEvent.click(screen.getByRole("button", {name: "Показать"}));
  await waitFor(() => {
    expect(fetch.mock.calls.at(-1)?.[0]).toContain("category=documents");
    expect(fetch.mock.calls.at(-1)?.[0]).toContain("since=");
    expect(fetch.mock.calls.at(-1)?.[0]).toContain("page=1");
  });
  fireEvent.click(screen.getByRole("button", {name: "Сбросить"}));
  await waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).not.toContain("since="));
});
it("distinguishes an unavailable history from an empty history", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({error: {message: "История временно недоступна"}}), {status: 503})));
  render(<UserHistory/>);
  await screen.findByRole("alert");
  expect(screen.queryByText("За выбранный период действий нет.")).toBeNull();
});
