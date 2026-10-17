/** Real isolated HTTPS workflow acceptance. No route mocks or live source reads.
 * Private config: origin, playwrightModule, executablePath, outputDir,
 * users:{editor,second,viewer}:{login,password,display_name}, registry_id,
 * type_id, document_ids:[id,id,id] (or documents:[{id},...]), optional field_keys.
 * Existing supplied cards MUST be owned synthetic fixtures in an isolated database.
 * After document checks/deletion pass and only reference setup fails, append
 * --resume-references to preserve the prior receipt and avoid replaying deletion.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash, randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export async function runLegacyParity(config) {
  const {browser, origin, outputDir, users, registry_id: registryId, type_id: typeId} = config;
  assert.equal(new URL(origin).protocol, 'https:', 'Strict HTTPS is required');
  assert(users?.editor && users?.second && users?.viewer, 'Three distinct fixture users are required');
  const ids = (config.document_ids || config.documents || []).map(item => typeof item === 'string' ? item : item.id);
  assert(ids.length >= 3, 'Three owned synthetic document IDs are required');
  const previous = config.resumeReferences ? JSON.parse(await fs.readFile(path.join(outputDir, 'checks.json'), 'utf8')) : null;
  if (previous) {assert.equal(previous.stage, 'registry-specific references'); assert.equal(previous.browserErrors, 0); assert(previous.checks.length >= 8, 'Resume requires prior completed document/deletion checks');}
  const contexts = [], checks = [...(previous?.checks || [])], errors = [], created = [];
  const marker = 'parity_' + Date.now().toString(36);
  let editor, second, viewer, stage = 'login';
  await fs.mkdir(outputDir, {recursive: true});
  const pass = name => {if (!checks.includes(name)) checks.push(name); console.log('PASS ' + name);};
  const fields = schema => schema.sections.flatMap(section => section.fields);
  const escaped = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const textField = (pane, label) => pane.getByRole('textbox', {name: new RegExp('^' + escaped(label) + '(?:\\s+\\*)?$')});
  async function api(page, endpoint, method = 'GET', data, expected = 200) {
    const result = await page.evaluate(async ({endpoint, method, data}) => {
      const me = method === 'GET' || method === 'HEAD' ? null : await (await fetch('/api/v1/users/me')).json();
      const response = await fetch('/api/v1' + endpoint, {method, credentials: 'same-origin', headers: {
        Accept: 'application/json', ...(data === undefined ? {} : {'Content-Type': 'application/json'}), ...(me ? {'X-CSRF-Token': me.csrf_token} : {}),
      }, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
      return {status: response.status, data: await response.json().catch(() => null)};
    }, {endpoint, method, data});
    assert((Array.isArray(expected) ? expected : [expected]).includes(result.status), `${method} ${endpoint.split('?')[0]} returned ${result.status}`);
    return result.data;
  }
  async function eventually(predicate, message, timeout = 20000) {
    const end = Date.now() + timeout;
    do {if (await predicate()) return; await delay(250);} while (Date.now() < end);
    assert.fail(message);
  }
  async function pageFor(credentials) {
    const context = await browser.newContext({viewport: {width: 1650, height: 1000}, ignoreHTTPSErrors: false, acceptDownloads: true});
    contexts.push(context);
    const page = await context.newPage(); page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.name));
    await page.goto(origin + '/work');
    await page.getByLabel('Логин', {exact: true}).fill(credentials.login);
    await page.getByLabel('Пароль', {exact: true}).fill(credentials.password);
    await page.getByRole('button', {name: 'Войти в рабочее пространство', exact: true}).click();
    await page.getByRole('button', {name: 'Выйти', exact: true}).waitFor();
    return page;
  }
  async function dismissNav(page) {
    const close = page.getByRole('button', {name: 'Скрыть панель', exact: true});
    if (await close.isVisible()) await close.click();
    else await page.getByRole('main').click({position: {x: 900, y: 8}});
  }
  const route = (page, document = '', filters = null, readOnly = false) => page.goto(`${origin}/${readOnly ? 'view' : 'work'}?registry_id=${registryId}&document_type_id=${typeId}&sort=title&direction=asc${document ? '&document=' + document : ''}${filters ? '&filters=' + encodeURIComponent(JSON.stringify(filters)) : ''}`);
  const row = (page, id) => page.locator(`tr[data-document-id="${id}"]`);
  const cardPane = page => page.getByRole('region', {name: 'Содержимое карточки', exact: true});
  async function current(page, id) {return api(page, `/documents/${id}?mode=editor`);}
  async function update(page, id, patch) {
    const card = await current(page, id);
    return api(page, `/documents/${id}`, 'PUT', {revision: card.revision, values: {...card.values, ...patch}, card_state: card.card_state, publication_targets: card.publication_targets});
  }
  async function screenshot(page, name) {await page.screenshot({path: path.join(outputDir, name + '.png'), fullPage: true, mask: [page.locator('.account')]});}
  function csvRows(text) {
    const rows = []; let row = [], value = '', quoted = false;
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (char === '"') {if (quoted && text[i + 1] === '"') {value += '"'; i++;} else quoted = !quoted;}
      else if (!quoted && (char === ',' || char === ';')) {row.push(value); value = '';}
      else if (!quoted && char === '\n') {row.push(value.replace(/\r$/, '')); if (row.some(cell => cell)) rows.push(row); row = []; value = '';}
      else value += char;
    }
    if (value || row.length) {row.push(value); rows.push(row);} return rows;
  }
  try {
    editor = await pageFor(users.editor); second = await pageFor(users.second); viewer = await pageFor(users.viewer);
    const me = await api(editor, '/users/me'), other = await api(second, '/users/me');
    assert.notEqual(me.id, other.id);
    await api(editor, '/health/ready');
    if (config.expectedSha) assert.equal((await api(editor, '/admin/platform')).source_sha, config.expectedSha);
    pass('Three independent real HTTPS login sessions and healthy backend');
    if (config.resumeReferences) await api(editor, `/documents/${ids[0]}`, 'GET', undefined, 404);
    if (!config.resumeReferences) {
    stage = 'fixture normalization';
    const initial = await current(editor, ids[0]), schemaFields = fields(initial.schema), keys = config.field_keys || {};
    const titleField = schemaFields.find(field => field.key === keys.title) || schemaFields.find(field => field.system === 'title') || schemaFields.find(field => field.key === 'title');
    const custom = schemaFields.filter(field => ['text', 'textarea'].includes(field.type) && field.filterable && !field.system && !field.visible_when && field.editable !== false);
    const first = schemaFields.find(field => field.key === keys.first_filter) || custom[0];
    const next = schemaFields.find(field => field.key === keys.second_filter) || custom.find(field => field.key !== first?.key);
    assert(titleField && first && next, 'Fixture schema needs title and two editable filterable text fields');
    const one = marker + '_one', two = marker + '_two';
    for (let i = 0; i < 3; i++) await update(editor, ids[i], {[first.key]: i === 2 ? one + '_other' : one, [next.key]: two});
    const cards = await Promise.all(ids.slice(0, 3).map(id => current(editor, id)));
    assert(cards.every(card => card.card_state === 'published' && card.publication_targets.includes('shared')), 'Viewer fixtures must be published/shared');
    // Additional owned cards exercise a genuinely overflowing two-batch feed.
    for (let i = 0; i < 22; i++) {
      const card = await api(editor, '/documents', 'POST', {registry_id: registryId, document_type_id: typeId, schema_version_id: initial.schema_version_id,
        card_state: 'published', publication_targets: ['internal', 'shared'], values: {...cards[2].values, [titleField.key]: `${marker} feed ${String(i).padStart(2, '0')}`}}, 201);
      created.push(card.id);
    }
    await eventually(async () => (await api(editor, '/search', 'POST', {query: '', registry_ids: [registryId], document_type_ids: [typeId], mode: 'editor', page_size: 100})).total >= 25, 'Fixture search indexing did not complete', 60000);

    stage = 'selection and exports';
    await route(editor); await dismissNav(editor); await editor.bringToFront();
    await editor.getByRole('radio', {name: 'Таблица', exact: true}).click();
    await editor.locator('tbody tr[data-document-id]').first().waitFor();
    const initialPane = editor.getByRole('region', {name: 'Список документов', exact: true});
    for (let i = 0; i < 20 && await editor.locator('tr[data-document-id]').count() < 25; i++) {await initialPane.evaluate(element => element.scrollTop = element.scrollHeight); await delay(300);}
    await row(editor, ids[0]).waitFor(); await row(editor, ids[1]).waitFor();
    await row(editor, ids[0]).getByRole('checkbox').check(); await row(editor, ids[1]).getByRole('checkbox').check();
    await row(editor, ids[1]).click({button: 'right'});
    await editor.getByRole('menu', {name: 'Действия с выбранными документами: 2', exact: true}).waitFor();
    const exporting = editor.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/documents/export-selection' && response.request().method() === 'POST');
    const downloadEvent = editor.waitForEvent('download');
    await editor.getByRole('menuitem', {name: 'Экспорт выбранных CSV', exact: true}).click();
    const exportResponse = await exporting;
    assert.equal(exportResponse.status(), 200);
    assert.deepEqual([...exportResponse.request().postDataJSON().document_ids].sort(), ids.slice(0, 2).sort());
    const download = await downloadEvent; assert.equal(await download.failure(), null);
    const exportPath = path.join(outputDir, 'selected-two.csv'); await download.saveAs(exportPath);
    assert.equal(csvRows(await fs.readFile(exportPath, 'utf8')).length, 3, 'CSV must contain a header and exactly two selected documents');
    await screenshot(editor, '01-selection-export');
    pass('Checkbox group survives right-click and bounded POST/download exports exactly two selected IDs');

    stage = 'loaded feed refresh';
    const pane = editor.getByRole('region', {name: 'Список документов', exact: true});
    for (let i = 0; i < 10 && await editor.locator('tr[data-document-id]').count() < 25; i++) {await pane.evaluate(element => element.scrollTop = element.scrollHeight); await delay(300);}
    const loaded = await editor.locator('tr[data-document-id]').count(); assert(loaded >= 25, 'At least two real cursor batches must load');
    await pane.evaluate(element => element.scrollTop = 400); const scroll = await pane.evaluate(element => element.scrollTop); assert(scroll > 0);
    await update(second, created.at(-1), {[next.key]: two + '_changed'});
    await eventually(async () => (await editor.locator('tr[data-document-id]').count()) === loaded && await row(editor, created.at(-1)).count() === 1, 'Refresh lost a loaded batch');
    // Wait through one actual visible refresh, then assert retained DOM state.
    await editor.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/search' && response.request().method() === 'POST' && response.status() === 200 && !!response.request().postDataJSON().cursor, {timeout: 15000});
    await delay(300);
    assert.equal(await editor.locator('tr[data-document-id]').count(), loaded);
    assert.equal(await pane.evaluate(element => element.scrollTop), scroll);
    assert.equal(await row(editor, ids[0]).getByRole('checkbox').isChecked(), true); assert.equal(await row(editor, ids[1]).getByRole('checkbox').isChecked(), true);
    pass('Visible polling refreshes previously loaded cursor batches while preserving selection and nonzero scroll');

    stage = 'multiple filters and configured columns';
    await route(editor); await dismissNav(editor);
    await editor.getByRole('button', {name: 'Фильтры', exact: true}).click();
    for (const [index, field, value] of [[1, first, one], [2, next, two]]) {
      await editor.getByRole('button', {name: 'Добавить условие', exact: true}).click();
      await editor.getByRole('combobox', {name: `Поле фильтра ${index}`, exact: true}).selectOption(field.key);
      await editor.getByLabel(`Значение фильтра ${index}`, {exact: true}).fill(value);
    }
    await editor.getByRole('button', {name: 'Найти', exact: true}).click();
    await eventually(async () => await editor.locator('tr[data-document-id]').count() === 2, 'AND filter must return exactly two matching fixture cards');
    const filteredURL = editor.url();
    assert.equal(Object.keys(JSON.parse(new URL(filteredURL).searchParams.get('filters'))).length, 2);
    await editor.reload(); await dismissNav(editor);
    await editor.getByRole('button', {name: 'Фильтры', exact: true}).click();
    assert.equal(await editor.getByRole('combobox', {name: 'Поле фильтра 1', exact: true}).inputValue(), first.key);
    assert.equal(await editor.getByRole('combobox', {name: 'Поле фильтра 2', exact: true}).inputValue(), next.key);
    await editor.getByRole('radio', {name: 'Таблица', exact: true}).click();
    await row(editor, ids[0]).waitFor();
    const headers = await editor.getByRole('columnheader').allTextContents();
    const configured = initial.schema.list_view?.columns || []; assert(configured.length > 0, 'Fixture must configure list columns');
    let previous = -1;
    for (const column of configured.filter(column => !['title','reg_number','reg_date','card_state','status','document_type'].includes(column.key))) {
      const index = headers.findIndex(text => text.includes(column.label)); assert(index > previous, 'Configured columns must retain administrator order'); previous = index;
    }
    await editor.getByRole('columnheader', {name: 'Статус правового акта', exact: true}).waitFor();
    await row(editor, ids[0]).getByRole('gridcell', {name: 'Состояние карточки: Опубликован', exact: true}).waitFor();
    const legal = schemaFields.find(field => field.key === keys.legal_status) || schemaFields.find(field => field.reference_code === 'status' || field.label === 'Статус правового акта');
    assert(legal && cards[0].display_values[legal.key], 'Fixture must contain a human legal-act status');
    await row(editor, ids[0]).getByRole('gridcell', {name: `Статус правового акта: ${cards[0].display_values[legal.key]}`, exact: true}).waitFor();
    assert.notEqual(cards[0].display_values[legal.key], 'Опубликован');
    await screenshot(editor, '02-two-and-filters-and-columns');
    await editor.getByRole('button', {name: 'Удалить активное условие 1', exact: true}).click();
    await eventually(async () => Object.keys(JSON.parse(new URL(editor.url()).searchParams.get('filters'))).length === 1, 'Removing one condition must preserve the other in the URL');
    pass('Two AND custom filters survive URL reload, remove independently, and configured columns/legal status remain distinct');

    stage = 'clean collaboration refresh';
    await route(editor, ids[0]); await dismissNav(editor); await editor.getByRole('radio', {name: 'Вместе', exact: true}).click();
    await route(second, ids[0]); await dismissNav(second); await second.getByRole('radio', {name: 'Вместе', exact: true}).click();
    const remoteTitle = marker + ' clean remote';
    await cardPane(second).getByRole('button', {name: 'Редактировать карточку', exact: true}).click();
    await textField(cardPane(second), titleField.label).fill(remoteTitle);
    await cardPane(second).getByRole('button', {name: 'Сохранить карточку', exact: true}).click();
    await cardPane(second).getByRole('button', {name: 'Редактировать карточку', exact: true}).waitFor();
    await editor.bringToFront();
    await cardPane(editor).getByRole('heading', {name: remoteTitle, exact: true}).waitFor({timeout: 20000});
    await cardPane(editor).locator('.document-collaboration').filter({hasText: users.second.display_name}).waitFor();
    pass('Second real editor saves through UI and a clean first card refreshes with named latest editor');

    stage = 'dirty remote change';
    await cardPane(editor).getByRole('button', {name: 'Редактировать карточку', exact: true}).click();
    const localTitle = marker + ' local unsaved', finalRemote = marker + ' changed remotely';
    await textField(cardPane(editor), titleField.label).fill(localTitle);
    const staleRevision = (await current(editor, ids[0])).revision;
    await cardPane(second).getByRole('button', {name: 'Редактировать карточку', exact: true}).click();
    await textField(cardPane(second), titleField.label).fill(finalRemote);
    await cardPane(second).getByRole('button', {name: 'Сохранить карточку', exact: true}).click();
    await cardPane(second).getByRole('button', {name: 'Редактировать карточку', exact: true}).waitFor(); await editor.bringToFront();
    await cardPane(editor).getByRole('button', {name: 'Загрузить актуальную редакцию', exact: true}).waitFor({timeout: 20000});
    assert.equal(await textField(cardPane(editor), titleField.label).inputValue(), localTitle);
    assert.equal(await cardPane(editor).getByRole('button', {name: 'Сохранить карточку', exact: true}).isDisabled(), true);
    await api(editor, `/documents/${ids[0]}`, 'PUT', {revision: staleRevision, values: {[titleField.key]: localTitle}}, 409);
    assert.equal((await current(editor, ids[0])).values[titleField.key], finalRemote);
    const presence = await api(second, `/documents/${ids[0]}/collaboration?mode=editor`);
    assert(presence.participants.some(person => person.id === me.id && person.editing), 'First editor must advertise advisory editing presence');
    assert(presence.participants.some(person => person.id === other.id), 'Second viewer/editor must be present');
    await cardPane(editor).getByRole('button', {name: 'История изменений', exact: true}).click();
    await cardPane(editor).locator('details > summary').filter({hasText: users.second.display_name}).first().waitFor();
    const history = await api(editor, `/documents/${ids[0]}/history?mode=editor`);
    assert(history.some(item => item.created_by === other.id));
    assert(history.filter(item => item.created_by === other.id).every(item => item.created_by_name === users.second.display_name));
    await screenshot(editor, '03-dirty-remote-change-and-history');
    editor.once('dialog', dialog => dialog.accept());
    await cardPane(editor).getByRole('button', {name: 'Загрузить актуальную редакцию', exact: true}).click();
    await cardPane(editor).getByRole('heading', {name: finalRemote, exact: true}).waitFor();
    pass('Dirty local values survive named remote change; stale write is409, named history/presence visible, explicit reload discards only on confirmation');

    stage = 'Office original capability';
    const beforeDelete = await current(editor, ids[0]);
    const original = beforeDelete.files.find(file => /\.(docx?|odt|rtf|pdf|txt)$/i.test(file.name));
    assert(original, 'First fixture needs an Office-supported immutable original');
    await cardPane(editor).getByRole('button', {name: 'Файлы и версии', exact: true}).click();
    await cardPane(editor).getByRole('button', {name: 'Открыть в LibreOffice', exact: true}).first().waitFor();
    const issued = await api(editor, `/files/${original.id}/office-open`, 'POST', {application: 'libreoffice', mode: 'editor'});
    const prefix = 'vnd.libreoffice.command:ofv|u|'; assert(issued.uri.startsWith(prefix));
    const nativeURL = issued.uri.slice(prefix.length); assert.equal(new URL(nativeURL).origin, origin);
    const anonymous = await browser.newContext({ignoreHTTPSErrors: false}); contexts.push(anonymous);
    assert.equal((await anonymous.cookies()).length, 0);
    const head = await anonymous.request.head(nativeURL), bytes = await anonymous.request.get(nativeURL);
    assert.equal(head.status(), 200); assert.equal(bytes.status(), 200);
    assert.equal(Number(head.headers()['content-length']), original.size);
    const buffer = await bytes.body(); assert.equal(buffer.length, original.size);
    assert.equal(createHash('sha256').update(buffer).digest('hex'), original.sha256);
    await screenshot(editor, '04-office-immutable-original');
    pass('Office control exists and exact-version URI serves authenticated-session-bound HEAD/GET to cookie-free native client with byte integrity');

    stage = 'viewer boundary and logical deletion';
    await route(viewer, ids[0], null, true); await dismissNav(viewer); await viewer.getByRole('radio', {name: 'Вместе', exact: true}).click();
    await cardPane(viewer).getByRole('button', {name: 'История изменений', exact: true}).waitFor();
    assert.equal(await cardPane(viewer).getByRole('button', {name: 'Редактировать карточку', exact: true}).count(), 0);
    await api(viewer, `/documents/${ids[0]}`, 'PUT', {revision: beforeDelete.revision, values: {}}, [403, 404]);
    await api(viewer, `/documents/${ids[0]}`, 'DELETE', {revision: beforeDelete.revision, reason: 'Forbidden synthetic deletion'}, [403, 404]);
    await editor.bringToFront(); await editor.getByRole('radio', {name: 'Таблица', exact: true}).click();
    await row(editor, ids[0]).click({button: 'right'});
    await editor.getByRole('menuitem', {name: 'Удалить выбранные документы', exact: true}).click();
    const dialog = editor.getByRole('dialog', {name: 'Удалить выбранные документы?', exact: true}); await dialog.waitFor();
    assert.equal(await dialog.getByRole('button', {name: 'Подтвердить удаление', exact: true}).isDisabled(), true);
    await dialog.getByLabel('Причина удаления', {exact: true}).fill('Owned synthetic legacy parity acceptance');
    await dialog.getByRole('button', {name: 'Подтвердить удаление', exact: true}).click();
    await dialog.waitFor({state: 'hidden'}); await row(editor, ids[0]).waitFor({state: 'hidden', timeout: 20000});
    await api(editor, `/documents/${ids[0]}`, 'GET', undefined, 404);
    assert.equal((await anonymous.request.get(nativeURL)).status(), 404, 'Previously issued native capability must fail after logical deletion');
    await screenshot(editor, '05-reasoned-delete-and-list');
    pass('Viewer cannot mutate/delete; required-reason revision-checked UI deletion disappears from list/directAPI and revokes prior native capability');

    }
    stage = 'registry-specific references';
    const foreign = await api(editor, '/registries', 'POST', {code: marker + '_other', name: 'Owned reference boundary ' + marker, document_type_ids: []}, 201);
    const referenceRegistry = await api(editor, '/registries', 'POST', {code: marker + '_own', name: 'Owned participants ' + marker, document_type_ids: []}, 201);
    const code = 'executors';
    const ownSet = await api(editor, '/references', 'POST', {code, name: 'Owned people ' + marker, registry_id: referenceRegistry.id}, 201);
    const otherSet = await api(editor, '/references', 'POST', {code, name: 'Other people ' + marker, registry_id: foreign.id}, 201);
    const label = 'Synthetic participant ' + marker;
    const ownPerson = await api(editor, `/references/${ownSet.id}/items`, 'POST', {label, metadata: {position: 'Synthetic position', department: 'Synthetic department'}}, 201);
    const otherPerson = await api(editor, `/references/${otherSet.id}/items`, 'POST', {label, metadata: {position: 'Other position', department: 'Other department'}}, 201);
    assert.notEqual(ownPerson.id, otherPerson.id);
    // Publish a real consumer only after both registry-scoped books exist.
    const referenceType = await api(editor, '/document-types', 'POST', {code: marker + '_type', name: 'Owned reference type ' + marker}, 201);
    const draft = (await api(editor, `/document-types/${referenceType.id}/versions`))[0];
    const referenceSchema = {sections: [{key: 'main', title: 'Основные сведения', fields: [
      {key: 'title', label: 'Наименование проверки', type: 'text', system: 'title', required: true, editable: true},
      {key: 'participants', label: 'Участники проверки', type: 'multi_reference', reference_code: code, editable: true},
    ]}]};
    await api(editor, `/form-schemas/${draft.id}`, 'PUT', {schema: referenceSchema});
    await api(editor, `/form-schemas/${draft.id}/publish`, 'POST', {});
    await api(editor, `/registries/${referenceRegistry.id}`, 'PATCH', {document_type_ids: [referenceType.id]});
    await api(editor, `/admin/access/${referenceRegistry.id}`, 'PUT', {entries: [{user_id: me.id, permissions: ['read', 'create', 'update', 'download']}, {user_id: other.id, permissions: ['read', 'create', 'update']}]});
    await api(editor, `/registries/${foreign.id}`, 'PATCH', {document_type_ids: [referenceType.id]});
    const mine = await api(editor, `/references/${ownSet.id}/items?mode=editor`), theirs = await api(editor, `/references/${otherSet.id}/items?mode=editor`);
    assert(mine.some(item => item.id === ownPerson.id)); assert(!mine.some(item => item.id === otherPerson.id));
    assert(theirs.some(item => item.id === otherPerson.id));
    const publicItems = await api(second, `/references/${ownSet.id}/items?mode=editor`);
    const exposed = publicItems.find(item => item.id === ownPerson.id); assert(exposed);
    assert.equal(exposed.metadata.position, 'Synthetic position'); assert.equal(exposed.metadata.department, 'Synthetic department');
    for (const key of ['source_database', 'source_entity', 'source_id']) assert.equal(exposed[key], null);
    await api(second, `/references/${otherSet.id}/items?mode=editor`, 'GET', undefined, 404);
    await editor.goto(`${origin}/work?registry_id=${referenceRegistry.id}&document_type_id=${referenceType.id}`); await dismissNav(editor);
    await editor.getByRole('group', {name: 'Вид и сортировка документов', exact: true}).getByRole('button', {name: 'Создать документ', exact: true}).click();
    const creation = editor.locator('.document-create');
    await creation.getByRole('combobox', {name: 'Тип документа', exact: true}).selectOption(referenceType.id);
    await textField(creation, 'Наименование проверки').fill('Owned reference selection ' + marker);
    const group = creation.getByRole('group', {name: 'Участники проверки', exact: true});
    await group.getByRole('checkbox', {name: `${label} · Synthetic position · Synthetic department`, exact: true}).check();
    assert.equal(await group.getByText('Other position', {exact: false}).count(), 0);
    await screenshot(editor, '06-scoped-people-position-department');
    const savingReferenceDocument = editor.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/documents' && response.request().method() === 'POST');
    await creation.getByRole('button', {name: 'Создать документ', exact: true}).click();
    const savedReferenceResponse = await savingReferenceDocument; assert.equal(savedReferenceResponse.status(), 201);
    const savedReference = await savedReferenceResponse.json(); created.push(savedReference.id);
    assert.deepEqual(savedReference.values.participants, [ownPerson.id]);
    pass('Explicit registry-scoped reference creation keeps equal names/codes separate and exposes approved position/department without source provenance');
    assert.deepEqual(errors, []); pass('No browser page runtime errors in any role/context');
    return {status: 'PASS', checks, browserErrors: errors.length};
  } catch (error) {
    await fs.writeFile(path.join(outputDir, 'failure.txt'), stage + '\n' + String(error.stack || error));
    for (const [index, page] of [editor, second, viewer].entries()) if (page) await screenshot(page, `failure-${index}`).catch(() => {});
    throw error;
  } finally {
    if (editor) for (const id of created) {
      try {const card = await current(editor, id); await api(editor, `/documents/${id}`, 'DELETE', {revision: card.revision, reason: 'Owned synthetic acceptance cleanup'}, 204);} catch {}
    }
    await fs.writeFile(path.join(outputDir, 'checks.json'), JSON.stringify({stage, checks, browserErrors: errors.length}, null, 2));
    for (const context of contexts) await context.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  let browser;
  try {
    const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    if (process.argv.includes('--resume-references')) config.resumeReferences = true;
    const {chromium} = await import(config.playwrightModule ? pathToFileURL(config.playwrightModule).href : 'playwright');
    browser = await chromium.launch({headless: true, ...(config.executablePath ? {executablePath: config.executablePath} : {})});
    const receipt = await runLegacyParity({...config, browser});
    await fs.writeFile(path.join(config.outputDir, 'acceptance.json'), JSON.stringify(receipt, null, 2));
    console.log(`Legacy parity HTTPS PASS: ${receipt.checks.length} scenarios.`);
  } catch (error) {console.error(`Legacy parity FAIL: ${error.name}: ${error.message.split('\n')[0]}`); process.exitCode = 1;}
  finally {await browser?.close();}
}
