import { useRef, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

export function Login() {
  const auth = useAuth();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await auth.login(login, password);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message +
              (failure.requestId ? ` Код обращения: ${failure.requestId}` : "")
          : "Не удалось выполнить вход",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <section className="login-intro">
        <a className="brand" href="/" aria-label="ACTI, главная">
          ACTI<span>v3</span>
        </a>
        <div>
          <span className="eyebrow">ОРГАНИЗАЦИОННАЯ ПАМЯТЬ</span>
          <h1>
            Знания и процессы.
            <br />В одном пространстве.
          </h1>
          <p>
            Основа для работы с документами,
            <br />
            основаниями и опытом администрации.
          </p>
        </div>
        <span className="intro-footer">
          Единая система · Локальная инфраструктура
        </span>
      </section>
      <section className="login-content">
        <div className="login-card">
          <span className="eyebrow">ДОБРО ПОЖАЛОВАТЬ</span>
          <h2>Вход в ACTI</h2>
          <p className="muted">Используйте локальную учётную запись.</p>
          <form onSubmit={submit} aria-label="Вход в ACTI">
            <label htmlFor="login">Логин</label>
            <input
              id="login"
              name="username"
              autoComplete="username"
              required
              maxLength={128}
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              autoFocus
            />
            <label htmlFor="password">Пароль</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={1024}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Выполняется вход…" : "Войти в рабочее пространство"}
              <span aria-hidden="true">→</span>
            </button>
          </form>
          <p className="login-help">
            Если у вас нет учётной записи, обратитесь к администратору системы.
          </p>
        </div>
        <span className="login-caption">ACTI · Платформенное ядро</span>
      </section>
    </main>
  );
}
