import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DocumentDialog } from "./DocumentDialog";
import { LookupCombobox } from "../engine/LookupCombobox";

afterEach(cleanup);
it("restores the opener when a child receives React autofocus", () => {
  const opener = document.createElement("button");
  document.body.append(opener);
  opener.focus();
  const dialog = render(<DocumentDialog label="Окно" onClose={vi.fn()}><button autoFocus>Закрыть</button></DocumentDialog>);
  expect(document.activeElement).toBe(screen.getByRole("button", {name: "Закрыть"}));
  dialog.unmount();
  expect(document.activeElement).toBe(opener);
  opener.remove();
});
it("lets Escape close lookup suggestions without cancelling the search dialog", () => {
  const close = vi.fn();
  render(<DocumentDialog label="Расширенный поиск" onClose={close}>
    <LookupCombobox id="filter" label="Ответственный" choices={[{id: "person", label: "Сотрудник"}]} selected={[]}
      multiple={false} query="" onQuery={vi.fn()} onSelect={vi.fn()} onRemove={vi.fn()} disabled={false}
      busy={false} atLimit={false} required={false} validation="" placeholder="Поиск"/>
    <button>Найти</button>
  </DocumentDialog>);
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  expect(screen.getByRole("listbox")).toBeTruthy();
  fireEvent.keyDown(input, {key: "Escape"});
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(close).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("button", {name: "Найти"}), {key: "Escape"});
  expect(close).toHaveBeenCalledOnce();
});
