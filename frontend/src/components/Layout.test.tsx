import { cleanup, fireEvent, render, screen, within, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Layout } from "./Layout";

vi.mock("../auth/AuthProvider", () => ({useAuth: () => ({user: {id: "test", display_name: "Тест", roles: ["admin"]}, logout: vi.fn()})}));
afterEach(() => {cleanup(); localStorage.clear(); vi.useRealTimers();});
function setup() {
  return render(<Layout section="Документы" setSection={vi.fn()} space="work" setSpace={vi.fn()}><h1>Документы</h1><input aria-label="Текст документа"/></Layout>);
}
it("keeps the working menu focused on documents, personal history and numbers", () => {
  setup();
  const nav = within(screen.getByRole("navigation", {name: "Основная навигация"}));
  expect(nav.getAllByRole("button").map(button => button.textContent)).toEqual(["⌂Документы", "◇Моя работа", "◇Реестр номеров", "◇Мой пароль"]);
});
it("reveals at the edge, closes after leaving, and pinning survives remount", () => {
  vi.useFakeTimers();
  const view = setup();
  fireEvent.pointerDown(screen.getByRole("main"));
  act(() => vi.advanceTimersByTime(300));
  const reveal = screen.getByRole("button", {name: "Открыть навигацию"});
  expect(reveal.getAttribute("aria-expanded")).toBe("false");
  fireEvent.pointerEnter(screen.getByRole("button", {name: "Показать навигацию у края"}), {pointerType: "mouse"});
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
  fireEvent.click(screen.getByRole("button", {name: "Открыть навигацию"}));
  fireEvent.click(screen.getByRole("button", {name: "Открыть навигацию"}));
  fireEvent.keyDown(screen.getByRole("complementary"), {key: "Escape"});
  expect((screen.getByLabelText("Текст документа") as HTMLInputElement).value).toBe("Несохранённый текст");
  expect(screen.getByRole("button", {name: "Открыть навигацию"}).getAttribute("aria-expanded")).toBe("false");
});
