# ACTI v3 Task 001: приёмка платформенного ядра

Дата: 2026-10-15, Asia/Yekaterinburg. **Результат: PASS**.
Проверены новая реализация, развёрнутая основная ВМ и реальный browser/API flow.
Этот результат относится к Task 001, а не к готовности полного реестра или миграции.

## Версии и границы

- Ветка: `codex/task-001-platform-foundation`.
- Исходный baseline: `ced0b59b0a53a1f02bba3bc64327d77f7a8e2137`.
- Развёрнутый код/images: **`eeb9d0d4c21a23d3d7eec7b33c9d9dafaad8647f`**.
  Значение проверено в image label и через административный API из браузера.
  Последующие изменения tests/документации не изменяют этот runtime code tree.
- Приложение активно, systemd service включён. Мигратор завершился с кодом 0;
  PostgreSQL, Redis, API, worker, frontend/Nginx имеют healthy status.
- Python 3.12, SQLAlchemy 2.0.54, Alembic 1.20.0, FastAPI 0.143.1,
  Redis client 6.4.0; React 19.2.8, Vite 7.3.7, Vitest 4.1.11, Node 24.
  Точные зависимости и image digests находятся в lockfiles.
- Ветка сохранена локально; push/PR/merge не выполнялись и не требуются для
  критерия этого задания. GitNexus main по-прежнему индексирует baseline,
  а не непубликовавшуюся feature branch. Новая архитектура проверена по source
  и tests; отсутствие новых symbols в индексе не трактовалось как отсутствие
  зависимостей. Ручного refresh/sync не было.

## Реализовано

- Модульный FastAPI backend, SQLAlchemy/PostgreSQL и начальная Alembic revision.
- User, LocalCredential, Role, UserRole, AuthSession, AuditEvent, OutboxEvent,
  ProcessedEvent. Стабильный User UUID отделён от login/будущего identity adapter.
- Атомарный bootstrap первого администратора, Argon2id, непрозрачная серверная
  сессия на 12 часов, HttpOnly/Secure/SameSite cookie, Origin/CSRF и rate limit.
- Версионированный API, защищённый router по умолчанию, текущий пользователь,
  logout, admin role check, единые ошибки/request ID и типизированный OpenAPI.
- Раздельные live/ready; readiness имеет отдельный ограниченный pool и timeout
  соединения/checkout/SQL/lock. Неполадки не оставляют бесконечную проверку БД.
- Транзакционный аудит и worker/outbox с receipts, retry и терминальной ошибкой.
- LocalFileStorage с атомарной публикацией, запретом перезаписи/escape/symlink
  доступа; import JSON Schema и документированный preliminary mapping.
- React shell: login, sidebar/topbar, user/logout, loading/error states,
  admin status и восемь разделов roadmap с честным статусом будущих функций.
- Digest-pinned Compose, Nginx HTTPS, mount/UUID guard, systemd, раздельные роли
  owner/app PostgreSQL, закрытые секреты, backup и disposable restore script.

## Автоматические проверки

Команды выполнялись в новых каталогах backend/frontend на ВМ. Для backend
использованы отдельная PostgreSQL `_test` и отдельная Redis DB; credentials
передавались из закрытой конфигурации, не из исходного кода.

| Команда | Фактический результат |
| --- | --- |
| `uv sync` / зафиксированный `uv.lock` | Установка завершена |
| `uv run pytest -q` | **39 passed**, 7.24 s в финальном запуске |
| `uv run ruff check app tests` | PASS |
| `uv run alembic upgrade head` и повтор | PASS, `0001_platform`, две системные роли без дубликатов |
| Конкурентные migrators и bootstrap tests | PASS |
| `uv run alembic check` | `No new upgrade operations detected` |
| `npm ci --no-audit` | PASS по package-lock |
| `npm test -- --run` | **7 passed**, три test files |
| `npm run typecheck` | PASS |
| `npm run build` | PASS, production assets собраны |
| Compose `config --quiet` и `build` | PASS, все требуемые images собраны |
| Реальный preflight с неверным ожидаемым UUID | Startup запрещён до пишущих services |
| `git diff --check` | PASS |

В backend test runner остаётся один upstream deprecation warning: текущий
Starlette TestClient рекомендует `httpx2` вместо поддерживаемого `httpx`.
Это не runtime error и не падение tests; test transport зафиксирован в lockfile.

TDD выполнен по этапам: отсутствующие реализации/контракт сначала давали RED,
после реализации проверки стали GREEN. Дополнительно commit-before-response
регрессия сначала воспроизвела ложный 200 при сбое commit, затем подтвердила
500 без session cookie. Tests с блокировкой БД, недоступной БД, неожиданной
revision и занятым readiness pool проверяют корректные 503 и независимый live.

## Проверки на развёрнутой ВМ

| Проверка | Результат |
| --- | --- |
| Оба обязательных mountpoint и UUID | Сверены с закрытым локальным контрактом |
| Service identities | PostgreSQL UID/GID получены из image; ACTI user из backend image подтверждён |
| PostgreSQL/WAL и document storage | Выделенные подкаталоги соответствующих томов; ОС не используется как fallback |
| Сетевая изоляция | Только Nginx публикует 80/443; PostgreSQL/Redis/API снаружи не опубликованы |
| TLS | curl с CA, Windows TLS validation и Chromium успешно проверяют сертификат без bypass |
| Health | live=200, ready=200, database/redis/storage=true |
| Admin | login=200, me=200, admin status=200; worker доступен |
| Anonymous и CSRF | me=401 без сессии; logout без CSRF=403 |
| Logout | 204, последующий protected request=401 |
| Обычная роль | me=200 с role=user, admin=403; acceptance account затем отключён |
| Runtime DB role | Не superuser, без CREATEDB/CREATEROLE; UPDATE/DELETE audit запрещены |
| Worker | Bootstrap event обработан; receipt существует |
| Повтор bootstrap CLI | Интерактивный пароль не отображался; существующая настройка отвергнута без сброса |
| Пересоздание контейнеров | systemd restart выполняет down/up через preflight; вход, User UUID и audit rows сохранились |
| Proxy trust после пересоздания | Текущая внутренняя subnet совпала с закрытой настройкой trust |
| Backup/restore | Dump восстановлен только в `acti_restore_test`; revision, user-ID digest и audit count совпали |
| Restore cleanup | Созданная disposable БД удалена; рабочая БД не перезаписывалась |

Снимок restore сверки: обе revision `0001_platform`, одинаковый digest набора
user IDs и по **10 audit rows**. Новые browser/acceptance действия после этой
проверки закономерно увеличивают аудит. Исходные SQL Server не подключались,
DDL/DML в `okrug`/`raion` не выполнялись, оригинальные документы не копировались.
ВМ не перезагружалась; проверка после reboot не заявляется.

## Браузер

Использован существующий Playwright MCP, отдельный изолированный tab.
Browser plugin skill отсутствует; это причина выбора штатного Playwright MCP.
Desktop: 1440×1000; mobile: 390×844. Снимки сохранены вне репозитория.

Путь проверки: HTTPS login -> текущий пользователь -> «Моя работа» -> все пункты
навигации -> notices будущих модулей -> admin status/worker -> logout -> 401.
После пересоздания проверены reload сохранённой сессии и повторный login.
На узком экране нет горизонтального переполнения страницы, меню прокручивается
отдельно; Tab переводит focus на доступный control.

Неверный пароль реально вернул 401 с сообщением входа. Для UI recovery
вызваны отдельные перехваченные ответы 503 и сетевой отказ: интерфейс показал
недоступность/соединение, а не ошибку пароля. Перехваты после проверки сняты.
Console содержит только ожидаемые сетевые сообщения этих 401/503/отказов;
ошибок JavaScript, framework overlay, пустого экрана и неожиданных запросов нет.
Frontend обращается к реальному same-origin API; статический макет не выдаётся
за интеграцию. Credential material в снимках/отчёте отсутствует.

## Ревью и решения исполнителя

Выполнено одно read-only ревью ветки субагентом. Critical не найдено.
Исполнитель исправил замечания одной серией с RED→GREEN и полным suite:

1. При отказе входа outer transaction освобождается до отдельного committed
   failure-audit, чтобы pool не исчерпывался ожиданием второго connection.
2. Readiness получил отдельный pool и ограничение SQL/lock/checkout, чтобы
   блокировка схемы не делала ready бесконечным.
3. OpenAPI приведён к фактическим success/error models и статусам. Замечание
   повышено из Minor до Important: неверный базовый контракт сломал бы будущих
   генерируемых API-клиентов, хотя текущий handwritten frontend работал.

Остальные принятые решения: работа в согласованной feature branch с сохранением
пользовательского diff; PowerShell ledger вместо bash helper; function-scoped
HTTP transaction до отправки response; owner/app DB roles раздельны; Docker
restart отключён ради mount guard, восстановление контейнера оператором описано
в runbook. Цена этих решений — дисциплина сохранения user diff/ledger,
небольшой дополнительный DB pool для health, сопровождение grants/схем
OpenAPI и ручная реакция на аварийный процесс вместо автоматического restart.
Отложенных замечаний ревью нет. Субагент не проверял живую ВМ или секреты;
эти проверки выполнил основной агент, а scope legacy подтверждён Git diff.

## Ограничения и вход в Task 002

Реестр, реальные документы, полный ACL, поиск, конструкторы, AD/OIDC и массовый
импорт ещё не реализованы. Управление пользователями пока без web UI.
Независимые внешние backup, расписание, retention, renewal сертификата,
нагрузочные показатели и reboot acceptance относятся к дальнейшей эксплуатации.
Сертификат локального CA требует отдельного доверия на других клиентах.

Task 002 может строить Document/DocumentVersion/DocumentFile и пользовательские
потоки реестра поверх работающей базы. До реального импорта отдельно подтвердить
живые source-схемы и неоднозначный mapping, состояния файлов, ACL и контролируемый
план переноса/восстановления. Task 002 автоматически не начиналась.

## Коммиты реализации

| Полный SHA | Изменение |
| --- | --- |
| `1b7719610e44149c490a337ba455290f9080062e` | Спецификация и критерии приёмки |
| `fbb680651ad7a0f3b7032dda7b21ac70dbe9cf3f` | План реализации |
| `449c69a3f9cc71c6f551d7c751d9a6db0c0aaad1` | PostgreSQL schema и Alembic |
| `294f0f6882a8735385503c60ccc2462c1387c469` | Identity и серверные сессии |
| `a1bbbfaf96eb3e022624086df842c8b3a8fc9fa2` | Защищённый API |
| `9fee66df157ea9177fab5fd7bb1e473641d30eea` | Аудит и outbox worker |
| `a4537d1b750f921a75142fc22520271bd577ebe2` | Файловый провайдер и контракт импорта |
| `7fe2cccaf5ee1b680bc1d50ca0cac93ab1250fef` | React web-оболочка |
| `ef7823b10800e96031627956df26be46b76527e5` | Развёртывание и восстановление |
| `eeb9d0d4c21a23d3d7eec7b33c9d9dafaad8647f` | Исправления ревью; развёрнутый runtime |
| `06e637d04ffef00a56920be9c3ddd9d7180461af` | Дополнительные readiness regression tests |

Последний documentation commit содержит этот отчёт, README, состояние проекта,
инфраструктурные заметки, завершённый план и статус в AGENTS.md. Его SHA сообщён
пользователю отдельно, поскольку файл не может содержать SHA собственного commit.
