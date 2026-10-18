import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { Layout, type Space } from "../components/Layout";
import { RegistriesAdmin, ReferencesAdmin, TypesAdmin } from "./Admin";
import {
  AccessAdmin,
  AuditAdmin,
  PlatformStatus,
  UsersAdmin,
} from "./AdminIdentity";
import { Documents } from "./Documents";
import { Password } from "./Password";
import { NumberBook } from "./NumberBook";
import { ContentErrorsAdmin } from "./DocumentContent";
import { Processes } from "./Processes";
import { UserHistory } from "./UserHistory";
import { ServiceAccountsAdmin } from "./ServiceAccountsAdmin";
import { OperationsAdmin } from "./OperationsAdmin";
export function Workspace() {
  const auth = useAuth();
  const roles = auth.user?.roles || [];
  const defaultSpace: Space = roles.includes("admin")
    ? "admin"
    : roles.some((role) => ["editor"].includes(role))
      ? "work"
      : "view";
  function resolve(): Space {
    const path = window.location.pathname.split("/")[1];
    return path === "admin" && roles.includes("admin")
      ? "admin"
      : path === "work" &&
          roles.some((role) => ["admin", "editor"].includes(role))
        ? "work"
        : path === "view"
          ? "view"
          : defaultSpace;
  }
  const [space, setSpaceState] = useState<Space>(resolve);
  const currentSpace = useRef(space);
  currentSpace.current = space;
  const lastWorkspaceLocation = useRef(window.location.pathname + window.location.search + window.location.hash);
  const [section, setSection] = useState(
    space === "admin" ? "Обзор системы" : "Документы",
  );
  function allowNavigation() {return window.dispatchEvent(new Event("acti:before-workspace-navigation", {cancelable: true}));}
  function chooseSection(next: string) {if (next !== section && allowNavigation()) setSection(next);}
  function setSpace(next: Space) {
    if (next === space || !allowNavigation()) return;
    setSpaceState(next);
    setSection(next === "admin" ? "Обзор системы" : "Документы");
    window.history.pushState(null, "", "/" + next);
    lastWorkspaceLocation.current = window.location.pathname + window.location.search + window.location.hash;
  }
  useEffect(() => {
    window.history.replaceState(null, "", "/" + space + window.location.search);
    const pop = (event: PopStateEvent & {actiDocumentGuarded?: boolean}) => {
      const next = resolve();
      if (next !== currentSpace.current && !event.actiDocumentGuarded && !allowNavigation()) {window.history.pushState(null, "", lastWorkspaceLocation.current); return;}
      lastWorkspaceLocation.current = window.location.pathname + window.location.search + window.location.hash;
      setSpaceState(next);
      setSection(next === "admin" ? "Обзор системы" : "Документы");
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  let content;
  if (section === "Мой пароль") content = <Password />;
  else if (section === "Моя работа")
    content = <UserHistory key={space}/>;
  else if (section === "Конструктор процессов")
    content = <Processes admin={space === "admin"} readOnly={space === "view"}/>;
  else if (section === "Серии номеров" || section === "Реестр номеров")
    content = <NumberBook admin={space === "admin"} readOnly={space === "view"} />;
  else if (section === "Справочники" && space === "work") content = <ReferencesAdmin />;
  else if (space !== "admin")
    content = <Documents key={space} readOnly={space === "view"} />;
  else if (section === "Реестры") content = <RegistriesAdmin />;
  else if (section === "Типы документов" || section === "Конструктор форм")
    content = <TypesAdmin />;
  else if (section === "Справочники") content = <ReferencesAdmin />;
  else if (section === "Пользователи") content = <UsersAdmin />;
  else if (section === "API и сервисные учётные записи") content = <ServiceAccountsAdmin />;
  else if (section === "Роли и доступ") content = <AccessAdmin />;
  else if (section === "История и аудит") content = <AuditAdmin />;
  else if (section === "Состояние платформы") content = <PlatformStatus />;
  else if (section === "Эксплуатация") content = <OperationsAdmin />;
  else if (section === "Обработка содержания") content = <ContentErrorsAdmin />;
  else
    content = (
      <>
        <div className="page-heading">
          <span className="eyebrow">УПРАВЛЕНИЕ ПЛАТФОРМОЙ</span>
          <h1>Обзор системы</h1>
          <p>Добро пожаловать, {auth.user?.display_name}.</p>
        </div>
        <section className="welcome-panel">
          <span className="badge green">Конструктор и реестры</span>
          <h2>Структура документов под вашу работу</h2>
          <p>
            Создайте тип, настройте и опубликуйте форму. Назначьте её реестру и
            предоставьте сотрудникам доступ.
          </p>
        </section>
        <div className="module-grid admin-shortcuts">
          {[
            ["Реестры", "Логические коллекции и доступные типы."],
            ["Типы документов", "Визуальный конструктор и версии форм."],
            ["Справочники", "Значения и связи полей."],
            ["Пользователи", "Локальные учётные записи и роли."],
            ["Роли и доступ", "Права на реестры и отдельные документы."],
            ["История и аудит", "События конфигурации и изменений."],
          ].map(([title, text]) => (
            <button
              key={title}
              className="module-card"
              onClick={() => setSection(title)}
            >
              <h3>
                {title}
                <span>↗</span>
              </h3>
              <p>{text}</p>
            </button>
          ))}
        </div>
        <p className="foundation-note">
          Для просмотра содержимого переключитесь в рабочее пространство
          редактора или читателя. Доступ определяется разрешениями реестров.
        </p>
      </>
    );
  return (
    <Layout
      section={section}
      setSection={chooseSection}
      space={space}
      setSpace={setSpace}
    >
      {content}
    </Layout>
  );
}
