import { useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";
export type Space = "admin" | "work" | "view";
const spaceNames: Record<Space, string> = {
  admin: "Администратор",
  work: "Редактор",
  view: "Читатель",
};
export function Layout({
  section,
  setSection,
  space,
  setSpace,
  children,
}: {
  section: string;
  setSection: (value: string) => void;
  space: Space;
  setSpace: (space: Space) => void;
  children: ReactNode;
}) {
  const auth = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const roles = auth.user?.roles || [];
  const spaces: Space[] = [
    ...(roles.includes("admin") ? ["admin" as Space] : []),
    ...(roles.some((role) => ["admin", "editor", "user"].includes(role))
      ? ["work" as Space]
      : []),
    "view",
  ];
  const sections =
    space === "admin"
      ? [
          "Обзор системы",
          "Реестры",
          "Типы документов",
          "Конструктор форм",
          "Конструктор процессов",
          "Справочники",
          "Серии номеров",
          "Пользователи",
          "Роли и доступ",
          "История и аудит",
          "Состояние платформы",
          "Обработка содержания",
          "Мой пароль",
        ]
      : ["Мои реестры", "Процессы", "Шаблоны", "Как делали раньше", "Реестр номеров", "Мой пароль"];
  async function logout() {
    setBusy(true);
    setError("");
    try {
      await auth.logout();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось выйти");
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
        <label className="space-picker">
          Рабочее пространство
          <select
            value={space}
            onChange={(event) => setSpace(event.target.value as Space)}
          >
            {spaces.map((item) => (
              <option key={item} value={item}>
                {spaceNames[item]}
              </option>
            ))}
          </select>
        </label>
        <p className="sidebar-caption">{spaceNames[space].toUpperCase()}</p>
        <nav aria-label="Основная навигация">
          {sections.map((title, index) => (
            <button
              key={title}
              className={section === title ? "nav-item active" : "nav-item"}
              onClick={() => setSection(title)}
              aria-current={section === title ? "page" : undefined}
            >
              <span className="nav-mark" aria-hidden="true">
                {index === 0 ? "⌂" : "◇"}
              </span>
              <span>{title}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <i />
          Универсальный реестр<span>v3</span>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <span className="breadcrumb">
            {spaceNames[space]}
            <b>/</b>
            <strong>{section}</strong>
          </span>
          <div className="account">
            <span className="avatar" aria-hidden="true">
              {auth.user?.display_name.charAt(0).toUpperCase()}
            </span>
            <span className="account-name">
              {auth.user?.display_name}
              <small>{spaceNames[space]}</small>
            </span>
            <button
              className="logout"
              onClick={() => void logout()}
              disabled={busy}
            >
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
          ACTI v3<span>Документы · Реестры · Совместная работа</span>
        </footer>
      </div>
    </div>
  );
}
