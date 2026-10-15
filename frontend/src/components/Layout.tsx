import { useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";

export const sections = [
  "Моя работа",
  "Документы",
  "Процессы",
  "Поиск",
  "Шаблоны",
  "Справочники",
  "Администрирование",
  "Аудит",
];
const marks = ["⌂", "▤", "⇄", "⌕", "▦", "☷", "⚙", "◷"];
export function Layout({
  section,
  setSection,
  children,
}: {
  section: string;
  setSection: (value: string) => void;
  children: ReactNode;
}) {
  const auth = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function logout() {
    setBusy(true);
    setError("");
    try {
      await auth.logout();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выйти");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">
        Перейти к содержанию
      </a>
      <aside className="sidebar">
        <div className="brand">
          ACTI<span>v3</span>
        </div>
        <p className="sidebar-caption">РАБОЧЕЕ ПРОСТРАНСТВО</p>
        <nav aria-label="Основная навигация">
          {sections.map((title, index) => {
            const available =
              index === 0 ||
              (index === 6 && auth.user?.roles.includes("admin"));
            return (
              <button
                key={title}
                className={section === title ? "nav-item active" : "nav-item"}
                onClick={() => setSection(title)}
                aria-current={section === title ? "page" : undefined}
              >
                <span className="nav-mark" aria-hidden="true">
                  {marks[index]}
                </span>
                <span>
                  {title}
                  {!available && <small>В разработке</small>}
                </span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <i /> Платформенное ядро <span>0.1</span>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <span className="breadcrumb">
            Рабочее пространство <b>/</b> <strong>{section}</strong>
          </span>
          <div className="account">
            <span className="avatar" aria-hidden="true">
              {auth.user?.display_name.charAt(0).toUpperCase()}
            </span>
            <span className="account-name">
              {auth.user?.display_name}
              <small>
                {auth.user?.roles.includes("admin")
                  ? "Администратор"
                  : "Пользователь"}
              </small>
            </span>
            <button className="logout" onClick={logout} disabled={busy}>
              {busy ? "Выход…" : "Выйти"}
            </button>
          </div>
        </header>
        {error && (
          <div role="alert" className="error top-error">
            {error}
          </div>
        )}
        <main id="content" tabIndex={-1}>
          {children}
        </main>
        <footer className="page-footer">
          ACTI v3<span>Документы · Процессы · Организационная память</span>
        </footer>
      </div>
    </div>
  );
}
