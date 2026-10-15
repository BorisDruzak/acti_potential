import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { Feedback } from "../components/Feedback";
import { useAction } from "../engine/useData";
export function Password({
  required = false,
  onDone,
}: {
  required?: boolean;
  onDone?: () => void;
}) {
  const auth = useAuth();
  const action = useAction();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  return (
    <section className={required ? "state-screen" : "panel"}>
      <div className="compact-form">
        <h1>{required ? "Установите свой пароль" : "Сменить пароль"}</h1>
        <p>
          {required
            ? "Первоначальный пароль нужно заменить перед началом работы."
            : "Используйте уникальный пароль длиной не менее 14 символов."}
        </p>
        <form
          className="compact-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (password !== confirm) {
              action.setError("Пароли не совпадают");
              return;
            }
            void action.run(async () => {
              await action.request("/users/me/password", "POST", {
                current_password: current,
                new_password: password,
              });
              await auth.refresh();
              onDone?.();
            }, "Пароль изменён");
          }}
        >
          <label>
            Текущий пароль
            <input
              type="password"
              required
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </label>
          <label>
            Новый пароль
            <input
              type="password"
              minLength={14}
              maxLength={1024}
              required
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <label>
            Повторите новый пароль
            <input
              type="password"
              required
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          </label>
          <Feedback {...action} />
          <button className="primary" disabled={action.busy}>
            Изменить пароль
          </button>
        </form>
        {required && (
          <button
            className="text-button"
            onClick={() => void action.run(auth.logout, "")}
          >
            Выйти
          </button>
        )}
      </div>
    </section>
  );
}
