import { cleanup, fireEvent, render, screen, within, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Layout } from "./Layout";

const logout = vi.hoisted(() => vi.fn());
vi.mock("../auth/AuthProvider", () => ({useAuth: () => ({user: {id: "test", display_name: "Тест", roles: ["admin"]}, logout})}));
vi.mock("../api/client", async original => ({...await original<typeof import("../api/client")>(), apiRequest: vi.fn(async () => ({online: false, last_seen_at: null, client_version: null, connections: [], documents: []}))}));
afterEach(() => {cleanup(); localStorage.clear(); vi.useRealTimers(); vi.clearAllMocks();});
function setup() {
  return render(<Layout section="Документы" setSection={vi.fn()} space="work" setSpace={vi.fn()}><h1>Документы</h1><input aria-label="Текст документа"/></Layout>);
}
it("opens the account from the sidebar and keeps it available with navigation hidden", () => {
  setup();
  fireEvent.click(screen.getByRole("button", {name: "Учётная запись в панели"}));
  expect(screen.getByRole("dialog", {name: "Учётная запись"}).textContent).toContain("Редактор");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", {name: "Закрыть"}));
  fireEvent.pointerDown(screen.getByRole("main"));
  fireEvent.click(screen.getByRole("button", {name: "Открыть навигацию"}));
  fireEvent.click(screen.getByRole("button", {name: "Учётная запись в панели"}));
  expect(screen.getByRole("dialog", {name: "Учётная запись"})).toBeTruthy();
});
it("keeps the working menu focused on documents, personal history and numbers", () => {
  setup();
  const nav = within(screen.getByRole("navigation", {name: "Основная навигация"}));
  expect(nav.getAllByRole("button").map(button => button.textContent)).toEqual(["⌂Документы", "◇Моя работа", "◇Реестр номеров", "◇Справочники", "◇Мой пароль"]);
});
it("reveals at the edge, closes after leaving, and pinning survives remount", () => {
  vi.useFakeTimers();
  const view = setup();
  fireEvent.pointerDown(screen.getByRole("main"));
  act(() => vi.advanceTimersByTime(300));
  const reveal = screen.getByRole("button", {name: "Открыть навигацию"});
  expect(reveal.getAttribute("aria-expanded")).toBe("false");
  fireEvent.pointerEnter(reveal, {pointerType: "mouse"});
  expect(reveal.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(screen.getByRole("button", {name: "Закрепить панель"}));
  fireEvent.pointerDown(screen.getByRole("main"));
  act(() => vi.advanceTimersByTime(300));
  expect(screen.getByRole("button", {name: "Открепить панель"}).getAttribute("aria-pressed")).toBe("true");
  view.unmount(); setup();
  expect(screen.getByRole("button", {name: "Открепить панель"})).toBeTruthy();
});
it("supports keyboard reveal and Escape without losing document edits", () => {
  setup();
  fireEvent.change(screen.getByLabelText("Текст документа"), {target: {value: "Несохранённый текст"}});
  fireEvent.pointerDown(screen.getByRole("main"));
  fireEvent.focus(screen.getByRole("button", {name: "Открыть навигацию"}));
  fireEvent.keyDown(screen.getByRole("complementary"), {key: "Escape"});
  expect((screen.getByLabelText("Текст документа") as HTMLInputElement).value).toBe("Несохранённый текст");
  expect(screen.getByRole("button", {name: "Открыть навигацию"}).getAttribute("aria-expanded")).toBe("false");
});
it("keeps Sync and logout in navigation and respects the dirty-document logout guard", async () => {
  setup();
  await act(async () => {});
  const nav = within(screen.getByRole("complementary"));
  expect(nav.getByRole("button", {name: "ACTI Sync: нет связи"})).toBeTruthy();
  const guard = (event: Event) => event.preventDefault();
  window.addEventListener("acti:before-workspace-navigation", guard);
  try {
    fireEvent.click(nav.getByRole("button", {name: "Выйти"}));
    expect(logout).not.toHaveBeenCalled();
  } finally {window.removeEventListener("acti:before-workspace-navigation", guard);}
  await act(async () => fireEvent.click(nav.getByRole("button", {name: "Выйти"})));
  expect(logout).toHaveBeenCalledOnce();
});
it("keeps sidebar dialogs open across pointer leave and returns Escape focus to the edge", async () => {
  vi.useFakeTimers();
  setup();
  await act(async () => {});
  const trigger = within(screen.getByRole("complementary")).getByRole("button", {name: "ACTI Sync: нет связи"});
  trigger.focus(); fireEvent.click(trigger);
  fireEvent.pointerLeave(screen.getByRole("complementary").parentElement!);
  act(() => vi.advanceTimersByTime(300));
  expect(screen.getByRole("button", {name: "Открыть навигацию"}).getAttribute("aria-expanded")).toBe("true");
  fireEvent.keyDown(screen.getByRole("dialog", {name: "ACTI Sync"}), {key: "Escape"});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  fireEvent.keyDown(screen.getByRole("complementary"), {key: "Escape"});
  expect(document.activeElement).toBe(screen.getByRole("button", {name: "Открыть навигацию"}));
});
it.each(["admin", "work", "view"] as const)("makes agent status available in %s without losing the page draft", async space => {
  render(<Layout section="Документы" setSection={vi.fn()} space={space} setSpace={vi.fn()}><input aria-label="Черновик карточки" defaultValue="Несохранённое"/></Layout>);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", {name: "ACTI Sync: нет связи"}));
  const panel = screen.getByRole("dialog", {name: "ACTI Sync"});
  expect(within(panel).queryByRole("button", {name: "Подключить агент"})).toBeNull();
  fireEvent.click(within(panel).getByRole("button", {name: "Закрыть"}));
  expect((screen.getByLabelText("Черновик карточки") as HTMLInputElement).value).toBe("Несохранённое");
});
