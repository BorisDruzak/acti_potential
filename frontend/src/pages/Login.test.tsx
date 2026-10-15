import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { Login } from "./Login";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("submits a pending login only once", async () => {
  let finish: (value: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  let posts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string) => {
      if (path.endsWith("/auth/login")) {
        posts++;
        return pending;
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            error: { code: "unauthorized", message: "Войдите" },
          }),
          { status: 401 },
        ),
      );
    }),
  );
  render(
    <AuthProvider>
      <Login />
    </AuthProvider>,
  );
  fireEvent.change(screen.getByLabelText("Логин"), {
    target: { value: "admin" },
  });
  fireEvent.change(screen.getByLabelText("Пароль"), {
    target: { value: "synthetic-password" },
  });
  const form = screen.getByRole("form", { name: "Вход в ACTI" });
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(posts).toBe(1);
  finish(
    new Response(
      JSON.stringify({
        error: {
          code: "auth_unavailable",
          message: "Вход временно недоступен",
        },
      }),
      { status: 503 },
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Вход временно недоступен",
    ),
  );
});
