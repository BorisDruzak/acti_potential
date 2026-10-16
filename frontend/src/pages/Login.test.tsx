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
  window.history.replaceState(null, "", "/");
});

function renderLogin() {
  return render(
    <AuthProvider>
      <Login />
    </AuthProvider>,
  );
}

function localButton() {
  return screen.getByRole("button", {
    name: /Войти в рабочее пространство/,
  }) as HTMLButtonElement;
}

function mockLoginEndpoints(
  config: { enabled: boolean; label: string } | null,
  start?: () => Promise<Response>,
) {
  const fetchMock = vi.fn((path: string) => {
    if (path.endsWith("/auth/oidc/config")) {
      return Promise.resolve(
        new Response(JSON.stringify(config), { status: config ? 200 : 503 }),
      );
    }
    if (path.endsWith("/auth/oidc/start") && start) return start();
    return Promise.resolve(
      new Response(
        JSON.stringify({ error: { code: "unauthorized", message: "Войдите" } }),
        { status: 401 },
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it.each([false, null])(
  "keeps local login usable when OIDC is unavailable (%s)",
  async (enabled) => {
    mockLoginEndpoints(enabled === null ? null : { enabled, label: "Единый вход" });
    renderLogin();
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/v1/auth/oidc/config",
        expect.anything(),
      ),
    );
    expect(
      screen.queryByRole("button", { name: "Войти через Единый вход" }),
    ).toBeNull();
    expect(screen.getByLabelText("Логин")).toBeTruthy();
    expect(screen.getByLabelText("Пароль")).toBeTruthy();
    expect(localButton().disabled).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  },
);

it("starts OIDC only once while the redirect is pending and retains local credentials", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetchMock = mockLoginEndpoints(
    { enabled: true, label: "Организация" },
    () => pending,
  );
  const assign = vi.fn();
  const realWindow = window;
  vi.stubGlobal("window", {
    document: realWindow.document,
    location: { href: realWindow.location.href, assign },
    history: realWindow.history,
    addEventListener: realWindow.addEventListener.bind(realWindow),
    removeEventListener: realWindow.removeEventListener.bind(realWindow),
    dispatchEvent: realWindow.dispatchEvent.bind(realWindow),
  });
  renderLogin();
  fireEvent.change(screen.getByLabelText("Логин"), {
    target: { value: "local-admin" },
  });
  const button = await screen.findByRole("button", { name: "Войти через Организация" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(
    fetchMock.mock.calls.filter(([path]) => path.endsWith("/auth/oidc/start")),
  ).toHaveLength(1);
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(localButton().disabled).toBe(true);
  finish(
    new Response(
      JSON.stringify({
        authorization_url: "https://identity.example.test/authorize?state=sample",
      }),
    ),
  );
  await waitFor(() =>
    expect(assign).toHaveBeenCalledWith(
      "https://identity.example.test/authorize?state=sample",
    ),
  );
  expect((screen.getByLabelText("Логин") as HTMLInputElement).value).toBe(
    "local-admin",
  );
});

it("restores local login after a failed OIDC start without exposing provider details", async () => {
  mockLoginEndpoints({ enabled: true, label: "Организация" }, () =>
    Promise.resolve(
      new Response(
        JSON.stringify({ error: { message: "private-provider-detail" } }),
        { status: 503 },
      ),
    ),
  );
  renderLogin();
  fireEvent.click(
    await screen.findByRole("button", { name: "Войти через Организация" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Не удалось выполнить единый вход",
    ),
  );
  expect(screen.getByRole("alert").textContent).not.toContain(
    "private-provider-detail",
  );
  expect(localButton().disabled).toBe(false);
});

it.each([
  "javascript:alert(1)",
  "http://identity.example.test/authorize",
  "https://name:password@identity.example.test/authorize",
])("rejects an unsafe provider redirect (%s)", async (authorization_url) => {
  mockLoginEndpoints({ enabled: true, label: "Организация" }, () =>
    Promise.resolve(new Response(JSON.stringify({ authorization_url }))),
  );
  renderLogin();
  fireEvent.click(
    await screen.findByRole("button", { name: "Войти через Организация" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "Не удалось выполнить единый вход",
    ),
  );
  expect(localButton().disabled).toBe(false);
});

it.each(["oidc_failed", "identity_not_linked", "untrusted-message"])(
  "shows a generic callback error and removes it from the URL (%s)",
  async (code) => {
    window.history.replaceState(
      null,
      "",
      `/?auth_error=${code}&keep=value#workspace`,
    );
    mockLoginEndpoints({ enabled: false, label: "Единый вход" });
    renderLogin();
    expect(screen.getByRole("alert").textContent).toContain(
      "Не удалось выполнить единый вход",
    );
    expect(screen.getByRole("alert").textContent).not.toContain(code);
    await waitFor(() => expect(window.location.search).toBe("?keep=value"));
    expect(window.location.hash).toBe("#workspace");
  },
);
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
