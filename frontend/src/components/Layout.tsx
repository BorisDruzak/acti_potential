import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthProvider";
import { DocumentDialog } from "./DocumentDialog";
import { OfficeSyncAgent } from "./OfficeSyncAgent";
import { SignOutIcon } from "@phosphor-icons/react/dist/csr/SignOut";
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
  const [accountOpen, setAccountOpen] = useState(false);
  const pinKey = `acti.navigation.v1:${auth.user?.id ?? "local"}`;
  const [pinned, setPinned] = useState(() => {
    try { return localStorage.getItem(pinKey) === "pinned"; } catch { return false; }
  });
  const [expanded, setExpanded] = useState(true);
  const [desktop, setDesktop] = useState(() => window.innerWidth > 700);
  const sidebar = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const restoreEdgeFocus = useRef(false);
  const focusNavigation = useRef(false);
  const suppressEdgeReveal = useRef(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const docked = pinned && desktop;
  const open = docked || expanded;
  function reveal() {
    clearTimeout(hideTimer.current);
    setExpanded(true);
  }
  function enterNavigation() {
    focusNavigation.current = true;
    reveal();
  }
  function hide() {
    clearTimeout(hideTimer.current);
    if (!docked) hideTimer.current = setTimeout(() => {
      if (!sidebar.current?.contains(document.activeElement) && !document.activeElement?.closest('[role="dialog"]')) setExpanded(false);
    }, 220);
  }
  function close() {
    clearTimeout(hideTimer.current);
    if (!docked) {
      restoreEdgeFocus.current = !!sidebar.current?.contains(document.activeElement);
      setExpanded(false);
    }
  }
  function togglePin() {
    const next = !pinned;
    clearTimeout(hideTimer.current);
    setPinned(next);
    setExpanded(true);
    try { localStorage.setItem(pinKey, next ? "pinned" : "auto"); } catch { /* Optional preference. */ }
  }
  useEffect(() => () => clearTimeout(hideTimer.current), []);
  useLayoutEffect(() => {
    if (open && focusNavigation.current) {
      focusNavigation.current = false;
      // Apply the visibility/inert change before moving focus from the edge.
      sidebar.current?.getBoundingClientRect();
      sidebar.current?.focus({preventScroll: true});
    } else if (!open && restoreEdgeFocus.current) {
      restoreEdgeFocus.current = false;
      suppressEdgeReveal.current = true;
      menuButton.current?.focus({preventScroll: true});
      suppressEdgeReveal.current = false;
    }
  }, [open]);
  useEffect(() => {
    const media = window.matchMedia?.("(min-width: 701px)");
    if (!media) return;
    const changed = () => setDesktop(media.matches);
    changed();
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  const roles = auth.user?.roles || [];
  const spaces: Space[] = [
    ...(roles.includes("admin") ? ["admin" as Space] : []),
    ...(roles.some((role) => ["admin", "editor"].includes(role))
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
          "API и сервисные учётные записи",
          "Роли и доступ",
          "История и аудит",
          "Состояние платформы",
          "Эксплуатация",
          "Обработка содержания",
          "Мой пароль",
        ]
      : ["Документы", "Моя работа", "Реестр номеров", ...(space === "work" ? ["Справочники"] : []), "Мой пароль"];
  async function logout() {
    if (!window.dispatchEvent(new Event("acti:before-workspace-navigation", {cancelable: true}))) return;
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
    <div className={`app-shell navigation-shell${docked ? " navigation-pinned" : ""}${open ? " navigation-open" : ""}`}>
      <a className="skip-link" href="#content">
        Перейти к содержанию
      </a>
      <div className="sidebar-region" onPointerLeave={hide}>
        <button ref={menuButton} type="button" className="navigation-edge" aria-label="Открыть навигацию" title="Открыть меню"
          aria-controls="main-navigation" aria-expanded={open}
          onPointerEnter={event => { if (event.pointerType !== "touch") reveal(); }}
          onFocus={() => {if (!suppressEdgeReveal.current) enterNavigation();}} onClick={enterNavigation}><span aria-hidden="true">›</span></button>
      <aside id="main-navigation" ref={sidebar} className="sidebar" tabIndex={-1} inert={!open}
        onPointerEnter={event => {if (event.currentTarget.contains(event.target as Node)) reveal();}}
        onFocus={event => {if (event.currentTarget.contains(event.target as Node)) reveal();}}
        onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) hide(); }}
        onKeyDown={event => { if (!event.defaultPrevented && event.key === "Escape" && !docked) { event.preventDefault(); close(); } }}>
        <div className="sidebar-heading">
        <div className="brand">
          ACTI<span>v3</span>
        </div>
        <button type="button" className="navigation-pin" onClick={togglePin}
          aria-pressed={pinned} aria-label={pinned ? "Открепить панель" : "Закрепить панель"}
          title={pinned ? "Открепить панель — включить автоскрытие" : "Закрепить панель — оставить открытой"}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M8 3h8m-7 0v7l-3 4v2h12v-2l-3-4V3M12 16v6"/></svg>
        </button>
        {!docked && <button type="button" className="navigation-close" aria-label="Скрыть панель" onClick={close}>
          <span aria-hidden="true">×</span>
        </button>}
        </div>
        <span className="navigation-mode">{pinned ? "Панель закреплена" : "Панель скрывается автоматически"}</span>
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
              onClick={() => {
                setSection(title);
                if (!docked) {
                  document.getElementById("content")?.focus({preventScroll: true});
                  close();
                }
              }}
              aria-current={section === title ? "page" : undefined}
            >
              <span className="nav-mark" aria-hidden="true">
                {index === 0 ? "⌂" : "◇"}
              </span>
              <span>{title}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-utilities">
          <OfficeSyncAgent owner={auth.user?.id || ""} csrfToken={auth.user?.csrf_token || ""}
            canControl={space !== "view" && roles.some(role => ["admin", "editor"].includes(role))}/>
          <div className="sidebar-account-row">
        <button className="sidebar-user" type="button" aria-label="Учётная запись в панели" aria-haspopup="dialog" onClick={() => setAccountOpen(true)}>
          <span className="avatar" aria-hidden="true">{auth.user?.display_name?.charAt(0).toUpperCase()}</span>
          <span><strong>{auth.user?.display_name}</strong><small>{spaceNames[space]}</small></span>
        </button>
          <button type="button" className="sidebar-logout" aria-label={busy ? "Выход…" : "Выйти"} title="Выйти из учётной записи" onClick={() => void logout()} disabled={busy}>
            <SignOutIcon size={20} aria-hidden="true"/>
          </button>
          </div>
          {error && <p role="alert" className="sidebar-error">{error}</p>}
        </div>
      </aside>
      </div>
      <div className="main-column">
        <main id="content" tabIndex={-1} onPointerEnter={hide} onPointerDown={close} onFocus={hide}>
          {children}
        </main>
        <footer className="page-footer">
          ACTI v3<span>Документы · Реестры · Совместная работа</span>
        </footer>
      </div>
      {accountOpen && <DocumentDialog label="Учётная запись" className="account-menu-sidebar" onClose={() => setAccountOpen(false)}>
        <div className="toolbar"><h2>Учётная запись</h2><button autoFocus onClick={() => setAccountOpen(false)}>Закрыть</button></div>
        <strong>{auth.user?.display_name}</strong><p>Рабочее пространство: {spaceNames[space]}</p>
        <div className="button-row"><button onClick={() => {setAccountOpen(false); setSection("Мой пароль");}}>Мой пароль</button>
          <button disabled={busy} onClick={() => void logout()}>{busy ? "Выход…" : "Выйти"}</button></div>
        {error && <p role="alert" className="error">{error}</p>}
      </DocumentDialog>}
    </div>
  );
}
