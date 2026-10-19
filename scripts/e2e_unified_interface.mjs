// Run only against an owned disposable HTTPS environment. Config contains private fixture credentials.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

export async function runUnifiedInterface({browser, origin, outputDir, users, originalPath, run, source, database}) {
  assert.equal(new URL(origin).protocol, 'https:');
  assert.match(run, /^[a-f0-9]{12}$/, 'An owned disposable environment receipt is required');
  assert(source?.endsWith('/' + run) && database?.endsWith('_' + run + '_test'), 'Disposable source/database ownership must match');
  const contexts = [], checks = [], errors = [];
  const pass = text => {checks.push(text); console.log('PASS ' + text);};
  await fs.mkdir(outputDir, {recursive: true});
  let page, reader, stage = 'login';
  async function login(user, route = 'work') {
    const context = await browser.newContext({viewport: {width: 1536, height: 1024}, ignoreHTTPSErrors: false});
    contexts.push(context);
    const p = await context.newPage(); p.setDefaultTimeout(15000);
    p.on('pageerror', error => errors.push(error.name));
    await p.goto(origin + '/' + route);
    await p.getByLabel('Логин', {exact: true}).fill(user.login);
    await p.getByLabel('Пароль', {exact: true}).fill(user.password);
    await p.getByRole('button', {name: 'Войти в рабочее пространство', exact: true}).click();
    await p.getByRole('button', {name: 'Выйти', exact: true}).waitFor();
    p.on('console', message => {if (message.type() === 'error') errors.push('console: ' + message.text());});
    return p;
  }
  async function api(p, endpoint, method = 'GET', body, expected = 200) {
    const result = await p.evaluate(async ({endpoint, method, body}) => {
      const me = method === 'GET' ? null : await (await fetch('/api/v1/users/me')).json();
      const r = await fetch('/api/v1' + endpoint, {method, headers: {Accept: 'application/json', ...(body === undefined ? {} : {'Content-Type': 'application/json'}), ...(me ? {'X-CSRF-Token': me.csrf_token} : {})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      return {status: r.status, body: await r.json().catch(() => null)};
    }, {endpoint, method, body});
    assert.equal(result.status, expected, `${method} ${endpoint}: ${JSON.stringify(result.body)}`);
    return result.body;
  }
  async function hideNav(p) {
    await p.waitForFunction(() => {
      const shell = document.querySelector('.navigation-shell');
      return shell && (!matchMedia('(max-width: 700px)').matches || !shell.classList.contains('navigation-pinned'));
    });
    const close = p.getByRole('button', {name: 'Скрыть панель', exact: true});
    if (await close.isVisible()) {
      await close.click();
      await p.waitForFunction(() => document.getElementById('main-navigation')?.inert);
    }
  }
  const detail = () => page.getByRole('region', {name: 'Содержимое карточки', exact: true});
  const mode = name => page.getByRole('radio', {name, exact: true});
  async function capture(name, p = page) {await p.evaluate(() => window.scrollTo(0, 0)); await p.screenshot({path: path.join(outputDir, name + '.png'), fullPage: false});}
  async function drawer() {await page.getByRole('button', {name: 'Расширенный поиск', exact: true}).click(); return page.getByRole('dialog', {name: 'Расширенный поиск', exact: true});}
  try {
    page = await login(users.editor);
    await api(page, '/health/ready');
    assert((await api(page, '/admin/platform')).source_sha.endsWith('-candidate'), 'Fixture writes require the isolated candidate runtime');
    stage = 'fixtures';
    const code = 'ui_' + Date.now().toString(36);
    const type = await api(page, '/document-types', 'POST', {code, name: 'Нормативный документ'}, 201);
    const registry = await api(page, '/registries', 'POST', {code, name: 'Рабочий реестр', document_type_ids: [type.id]}, 201);
    const v = (await api(page, `/document-types/${type.id}/versions`))[0];
    const schema = {sections: [
      {key: 'main', title: 'Основные сведения', fields: [
        {key: 'title', label: 'Наименование документа', type: 'text', system: 'title', required: true, editable: true, searchable: true, filterable: true, exportable: true},
        {key: 'number', label: 'Регистрационный номер', type: 'text', system: 'reg_number', editable: true, filterable: true, exportable: true},
        {key: 'date', label: 'Дата регистрации', type: 'date', system: 'reg_date', editable: true, filterable: true, exportable: true},
        {key: 'kind', label: 'Вид документа', type: 'text', editable: true, filterable: true, exportable: true},
        {key: 'topic', label: 'Тема', type: 'text', editable: true, filterable: true, exportable: true},
        {key: 'owner', label: 'Ответственный', type: 'text', editable: true, filterable: true, exportable: true},
        {key: 'private', label: 'Сведения редактора', type: 'text', editable: true, read_roles: ['editor']},
      ]},
      {key: 'publication', title: 'Публикация', fields: [{key: 'note', label: 'Примечание', type: 'textarea', editable: true}]},
      {key: 'control', title: 'Контроль исполнения', fields: Array.from({length: 8}, (_, i) => ({key: 'control' + i, label: 'Сведения ' + (i + 1), type: 'textarea', editable: true}))},
      {key: 'files', title: 'Оригиналы и вложения', fields: [{key: 'original', label: 'Оригинал документа', type: 'file', editable: true}]},
    ], list_view: {columns: [{key: 'kind', label: 'Вид документа'}, {key: 'topic', label: 'Тема'}, {key: 'owner', label: 'Ответственный'}]}};
    await api(page, `/form-schemas/${v.id}`, 'PUT', {schema});
    await api(page, `/form-schemas/${v.id}/publish`, 'POST', {});
    const me = await api(page, '/users/me');
    await api(page, `/admin/access/${registry.id}`, 'PUT', {entries: [{user_id: me.id, permissions: ['read', 'create', 'update', 'upload', 'download']}, {user_id: users.viewer.id, permissions: ['read', 'download']}]});
    const titles = ['О внесении изменений в положение об отделе архитектуры', 'Об утверждении плана работы на 2026 год', 'О создании комиссии по рассмотрению обращений', 'Об организации работы с документами', 'О подготовке отчёта об исполнении муниципальной программы'];
    const original = (await fs.readFile(originalPath)).toString('base64');
    const docs = [];
    for (let i = 0; i < 15; i++) docs.push(await api(page, '/documents', 'POST', {registry_id: registry.id, document_type_id: type.id, schema_version_id: v.id, card_state: 'published', publication_targets: ['internal', 'shared'], values: {title: titles[i % titles.length] + (i > 4 ? ` · ${i + 1}` : ''), number: String(123 + i), date: '2026-10-18', kind: i % 2 ? 'Распоряжение' : 'Постановление', topic: 'Организация деятельности', owner: 'Тестовый сотрудник', private: 'Закрытые сведения', note: 'Синтетический документ для проверки интерфейса.'}, ...(i === 0 ? {files: [{field_key: 'original', name: 'original.docx', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', content_base64: original}]} : {})}, 201));
    for (let i = 0; i < 60; i++) {
      const result = await api(page, '/search', 'POST', {query: '', registry_ids: [registry.id], mode: 'editor'});
      if (result.total === 15) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    const route = `?registry_id=${registry.id}&document_type_id=${type.id}&sort=reg_number&direction=asc`;
    let cardWrites = 0;
    page.on('request', request => {if (request.method() === 'PUT' && new URL(request.url()).pathname === `/api/v1/documents/${docs[0].id}`) cardWrites++;});
    await page.goto(origin + '/work' + route); await hideNav(page);
    await page.locator(`tr[data-document-id="${docs[0].id}"]`).click();
    await detail().getByRole('button', {name: 'Сохранить карточку', exact: true}).waitFor();
    pass('Real HTTPS login, published schema, indexed synthetic documents');
    stage = 'layout and drafts';
    const title = page.locator('.document-card').getByRole('textbox', {name: 'Наименование документа *', exact: true, includeHidden: true});
    await title.waitFor({state: 'visible'});
    assert(await title.isEditable(), 'Permitted fields must be editable immediately after opening');
    assert.equal(await detail().getByRole('button', {name: 'Редактировать карточку', exact: true}).count(), 0);
    const save = detail().getByRole('button', {name: 'Сохранить карточку', exact: true});
    assert(await save.isDisabled());
    assert.equal(cardWrites, 0, 'Opening the card must not submit it');
    await title.fill('Несохранённый заголовок');
    assert.equal(await title.evaluate(input => input.closest('.form-field')?.getAttribute('data-dirty')), 'true');
    assert(await title.evaluate(input => input.closest('.form-field')?.classList.contains('field-dirty')));
    assert.deepEqual(await title.evaluate(input => ({border: getComputedStyle(input).borderTopColor, label: getComputedStyle(input.closest('.form-field').querySelector('label')).color})), {border: 'rgb(194, 81, 81)', label: 'rgb(166, 48, 48)'});
    assert.equal(await detail().getByText('Не сохранено', {exact: true}).count(), 1);
    for (const name of ['Таблица', 'Карточка', 'Вместе']) {await mode(name).click(); assert.equal(await title.inputValue(), 'Несохранённый заголовок');}
    let d = await drawer();
    await d.getByLabel('Регистрационный номер', {exact: true}).fill('999');
    await d.getByRole('button', {name: 'Отмена', exact: true}).click();
    assert.equal(await title.inputValue(), 'Несохранённый заголовок'); assert(!new URL(page.url()).searchParams.has('reg_number'));
    d = await drawer(); assert.equal(await d.getByLabel('Регистрационный номер', {exact: true}).inputValue(), '');
    await d.getByLabel('Регистрационный номер', {exact: true}).fill('123');
    await capture('advanced-search');
    await d.getByRole('button', {name: 'Найти', exact: true}).click();
    await page.getByText('Найдено: 1', {exact: true}).waitFor();
    assert.equal(await title.inputValue(), 'Несохранённый заголовок');
    await page.getByRole('button', {name: 'Удалить фильтр reg_number', exact: true}).click();
    await page.getByText('Найдено: 15', {exact: true}).waitFor();
    await detail().getByRole('button', {name: 'Отмена изменений', exact: true}).click();
    assert.equal(await title.inputValue(), titles[0]);
    assert(await title.isEditable(), 'Cancel must retain editable card controls');
    assert.equal(await title.evaluate(input => input.closest('.form-field')?.getAttribute('data-dirty')), null);
    assert(await save.isDisabled());
    assert.equal(cardWrites, 0, 'Draft changes and cancellation must not submit the card');
    await capture('split');
    await mode('Таблица').click(); await capture('table');
    await mode('Карточка').click(); await capture('card');
    pass('Three layouts, draft retention, advanced search apply/cancel and removable filters');
    stage = 'edit and originals';
    await title.fill('');
    await detail().getByRole('button', {name: 'История изменений', exact: true}).click();
    await detail().getByRole('button', {name: 'Сохранить карточку', exact: true}).click();
    assert(await title.isVisible());
    assert(await title.evaluate(input => !input.checkValidity()));
    assert.equal(await title.evaluate(input => input.closest('.form-field')?.getAttribute('data-dirty')), 'true');
    assert.equal(cardWrites, 0, 'Failed validation must retain the unsaved draft without a write');
    await title.fill('О внесении изменений в положение об отделе архитектуры — редакция');
    await detail().getByRole('button', {name: 'История изменений', exact: true}).click();
    const [saved] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/documents/${docs[0].id}` && response.request().method() === 'PUT'),
      save.click(),
    ]);
    assert.equal(saved.status(), 200);
    await detail().getByText('Все изменения сохранены', {exact: true}).waitFor();
    assert.equal(cardWrites, 1, 'Only explicit Save must update the card');
    assert.equal(await title.evaluate(input => input.closest('.form-field')?.getAttribute('data-dirty')), null);
    assert.equal(await detail().getByText('Не сохранено', {exact: true}).count(), 0);
    assert(await title.isEditable(), 'Successful save must retain editable card controls');
    assert(await save.isDisabled());
    assert.equal((await api(page, `/documents/${docs[0].id}`)).card_state, 'published');
    assert.equal(await detail().getByRole('button', {name: 'Опубликовать', exact: true}).count(), 0);
    const downloadDiagnostics = [];
    page.on('response', r => {if (r.url().includes('/download')) downloadDiagnostics.push({status: r.status(), path: new URL(r.url()).pathname});});
    const [download] = await Promise.all([page.waitForEvent('download'), detail().getByRole('button', {name: 'Скачать', exact: true}).click()]).catch(async error => {
      await fs.writeFile(path.join(outputDir, 'download-diagnostics.json'), JSON.stringify({requests: downloadDiagnostics, errors, alerts: await page.getByRole('alert').allTextContents()}, null, 2)); throw error;
    });
    assert.equal(download.suggestedFilename(), 'original.docx'); assert.equal(await download.failure(), null);
    // Keep the OS protocol prompt from taking focus in headless acceptance.
    // The real server still issues the exact-file URI; only native application launch is intercepted.
    await page.evaluate(() => {
      const click = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {
        if (this.href.startsWith('ms-word:ofv|u|')) {window.__actiOfficeHandoff = this.href; return;}
        return click.call(this);
      };
    });
    await detail().getByRole('button', {name: 'Открыть в Word', exact: true}).click();
    await detail().getByText(/Подтвердите открытие приложения в браузере/).waitFor();
    assert(await page.evaluate(() => window.__actiOfficeHandoff?.startsWith('ms-word:ofv|u|' + location.origin + '/api/v1/office/files/')));
    await detail().getByRole('button', {name: 'Загрузить новую версию', exact: true}).click();
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1/documents/${docs[0].id}/files` && r.request().method() === 'POST');
    await detail().locator('.upload-area input').setInputFiles(originalPath);
    assert.equal((await uploaded).status(), 201);
    await detail().getByRole('button', {name: 'Открыть оригинал', exact: true}).waitFor();
    assert.equal((await api(page, `/documents/${docs[0].id}`)).files.length, 2);
    await detail().getByRole('button', {name: 'Открыть оригинал', exact: true}).click();
    const originals = page.getByRole('dialog', {name: 'Выбрать оригинал', exact: true});
    assert.equal(await originals.getByRole('button', {name: 'Скачать', exact: true}).count(), 2);
    assert((await originals.boundingBox()).width <= 650);
    await capture('original-picker');
    await originals.getByRole('button', {name: 'Закрыть', exact: true}).click();
    await detail().getByRole('button', {name: 'История изменений', exact: true}).click();
    await detail().getByText('История реквизитов', {exact: true}).waitFor();
    pass('Direct editing, red unsaved fields, explicit save/cancel, exact-file Word handoff, immutable upload and metadata history');
    stage = 'creation';
    await page.getByRole('button', {name: 'Создать документ', exact: true}).click();
    const creation = page.locator('.document-create');
    await creation.getByRole('combobox', {name: 'Тип документа', exact: true}).selectOption(type.id);
    await creation.getByRole('textbox', {name: 'Наименование документа *', exact: true}).fill('Новый документ из рабочей области');
    await mode('Таблица').click(); await mode('Вместе').click();
    assert.equal(await creation.getByRole('textbox', {name: 'Наименование документа *', exact: true}).inputValue(), 'Новый документ из рабочей области');
    await capture('create');
    await creation.getByRole('button', {name: 'Создать документ', exact: true}).click();
    await detail().getByRole('heading', {name: 'Новый документ из рабочей области', exact: true}).waitFor();
    pass('Creation uses published schema, keeps search and retains input across layouts');
    stage = 'account and navigation';
    const navigationEdge = page.getByRole('button', {name: 'Открыть навигацию', exact: true});
    if (await navigationEdge.getAttribute('aria-expanded') !== 'true') await navigationEdge.click();
    await page.getByRole('button', {name: 'Закрепить панель', exact: true}).click();
    assert.deepEqual(await page.getByRole('navigation').getByRole('button').allTextContents(), ['⌂Документы', '◇Моя работа', '◇Реестр номеров', '◇Мой пароль']);
    await page.getByRole('button', {name: 'Учётная запись в панели', exact: true}).click();
    await page.getByRole('dialog').getByRole('button', {name: 'Мой пароль', exact: true}).click();
    await page.getByRole('heading', {name: 'Сменить пароль', exact: true}).waitFor(); await capture('password');
    await page.getByRole('navigation').getByRole('button', {name: 'Моя работа', exact: true}).click();
    await page.getByRole('heading', {name: 'Моя работа', exact: true}).waitFor(); await capture('history');
    await page.getByRole('navigation').getByRole('button', {name: 'Реестр номеров', exact: true}).click();
    await page.getByRole('heading', {name: 'Реестр номеров', exact: true}).waitFor();
    await capture('number-book');
    pass('Sidebar account access; original navigation, password, history and number book');
    stage = 'pinned layout, resize, saved search and editor mobile draft';
    await page.getByRole('navigation').getByRole('button', {name: 'Документы', exact: true}).click();
    await mode('Вместе').click();
    await page.locator(`tr[data-document-id="${docs[0].id}"]`).click();
    await detail().getByRole('button', {name: 'Сохранить карточку', exact: true}).waitFor();
    const separator = page.getByRole('separator', {name: 'Размер списка документов и карточки', exact: true});
    const ratio = Number(await separator.getAttribute('aria-valuenow'));
    await separator.focus(); await separator.press('ArrowRight');
    assert(Number(await separator.getAttribute('aria-valuenow')) > ratio);
    await capture('pinned-split');
    await mode('Таблица').click(); await capture('pinned-table');
    await page.locator('.document-list-export > summary').click();
    for (const format of ['CSV', 'XLSX']) {
      const [exported] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', {name: 'Экспорт ' + format, exact: true}).click()]);
      assert(exported.suggestedFilename().endsWith('.' + format.toLowerCase())); assert.equal(await exported.failure(), null);
    }
    await page.locator('.document-list-export > summary').click();
    await mode('Вместе').click();
    d = await drawer(); await d.getByText('Сохранённые поиски', {exact: true}).click();
    await d.getByLabel('Название представления', {exact: true}).fill('Рабочие документы');
    await d.getByRole('button', {name: 'Сохранить текущий поиск', exact: true}).click();
    await d.getByRole('button', {name: 'Отмена', exact: true}).click();
    await detail().getByRole('button', {name: 'Сохранить карточку', exact: true}).waitFor();
    await title.fill('Черновик на узком экране');
    await page.setViewportSize({width: 390, height: 844}); await hideNav(page);
    d = await drawer(); await capture('mobile-editor-search');
    await d.getByRole('button', {name: 'Отмена', exact: true}).click();
    assert.equal(await title.inputValue(), 'Черновик на узком экране');
    await detail().getByRole('button', {name: 'Отмена изменений', exact: true}).click();
    const beforeLongTitle = await api(page, `/documents/${docs[0].id}`);
    const longTitle = 'О внесении изменений в положение об отделе архитектуры и организации работы с документами. '.repeat(3);
    await api(page, `/documents/${docs[0].id}`, 'PUT', {revision: beforeLongTitle.revision, card_state: beforeLongTitle.card_state, publication_targets: beforeLongTitle.publication_targets, values: {...beforeLongTitle.values, title: longTitle}});
    await page.reload(); await hideNav(page);
    await detail().getByRole('button', {name: 'Сохранить карточку', exact: true}).waitFor();
    const header = page.locator('.document-card-top');
    const headerBox = await header.boundingBox(), paneBox = await detail().boundingBox();
    assert(headerBox.height < paneBox.height * .7, 'Sticky header must leave room for the mobile form');
    const lowerField = detail().getByRole('textbox', {name: 'Сведения 8', exact: true});
    await lowerField.scrollIntoViewIfNeeded(); await lowerField.click();
    await lowerField.fill('Нижнее поле доступно на телефоне');
    await page.screenshot({path: path.join(outputDir, 'mobile-long-title.png'), fullPage: false});
    await detail().getByRole('button', {name: 'Отмена изменений', exact: true}).click();
    await page.setViewportSize({width: 1536, height: 1024});
    pass('Pinned navigation, divider resize, CSV/XLSX, saved search, mobile drafts and long-title lower fields');
    stage = 'administrative navigation';
    await page.getByRole('combobox', {name: 'Рабочее пространство', exact: true}).selectOption('admin');
    assert.deepEqual(await page.getByRole('navigation').getByRole('button').allTextContents(), ['⌂Обзор системы', '◇Реестры', '◇Типы документов', '◇Конструктор форм', '◇Конструктор процессов', '◇Справочники', '◇Серии номеров', '◇Пользователи', '◇API и сервисные учётные записи', '◇Роли и доступ', '◇История и аудит', '◇Состояние платформы', '◇Эксплуатация', '◇Обработка содержания', '◇Мой пароль']);
    await page.getByRole('navigation').getByRole('button', {name: 'Конструктор форм', exact: true}).click();
    await page.getByRole('heading', {name: 'Типы документов', exact: true}).waitFor();
    await capture('admin-forms');
    pass('Administrative workspace keeps every original navigation item and the form builder');
    stage = 'reader and mobile';
    reader = await login(users.viewer, 'view');
    await reader.goto(origin + '/view' + route + '&document=' + docs[0].id); await hideNav(reader);
    await reader.getByRole('heading', {name: /О внесении изменений/, level: 1}).waitFor();
    assert.equal(await reader.getByRole('button', {name: 'Редактировать карточку', exact: true}).count(), 0);
    assert.equal(await reader.getByRole('button', {name: 'Сохранить карточку', exact: true}).count(), 0);
    assert.equal(await reader.getByRole('button', {name: 'Загрузить новую версию', exact: true}).count(), 0);
    assert.equal(await reader.locator('.document-card input[type="file"]').count(), 0);
    assert.equal(await reader.locator('.document-card').getByRole('textbox', {name: /Наименование документа/}).count(), 0);
    assert.equal(await reader.getByRole('button', {name: 'Создать документ', exact: true}).count(), 0);
    assert.equal(await reader.getByText('Закрытые сведения', {exact: true}).count(), 0);
    await capture('reader', reader);
    await reader.setViewportSize({width: 390, height: 844});
    await reader.getByRole('button', {name: 'Расширенный поиск', exact: true}).click();
    const rd = reader.getByRole('dialog', {name: 'Расширенный поиск', exact: true});
    assert((await rd.boundingBox()).width <= 390);
    assert.equal(await reader.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await capture('mobile-search', reader);
    await rd.getByRole('button', {name: 'Отмена', exact: true}).click();
    await reader.getByRole('radio', {name: 'Карточка', exact: true}).click();
    await reader.locator('.document-detail-pane').scrollIntoViewIfNeeded();
    await reader.screenshot({path: path.join(outputDir, 'mobile-reader.png'), fullPage: false});
    pass('Reader restrictions and full-width search at 390px without horizontal overflow');
    assert.deepEqual(errors, []);
    return {status: 'PASS', checks, browserErrors: 0};
  } catch (error) {
    await fs.writeFile(path.join(outputDir, 'failure.txt'), stage + '\n' + String(error.stack || error) + '\n' + JSON.stringify(errors));
    if (page) {await fs.writeFile(path.join(outputDir, 'failure-dom.txt'), await page.locator('body').ariaSnapshot()); await capture('failure').catch(() => {});}
    throw error;
  } finally {for (const context of contexts) await context.close();}
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    const {chromium} = await import(pathToFileURL(config.playwrightModule).href);
    browser = await chromium.launch({headless: true, executablePath: config.executablePath});
    const result = await runUnifiedInterface({...config, browser});
    await fs.writeFile(path.join(config.outputDir, 'acceptance.json'), JSON.stringify(result, null, 2));
    console.log(`Unified interface: ${result.checks.length} scenarios PASS`);
  } catch (error) {console.error(error.name + ': ' + error.message.split('\n')[0]); process.exitCode = 1;}
  finally {await browser?.close();}
}
