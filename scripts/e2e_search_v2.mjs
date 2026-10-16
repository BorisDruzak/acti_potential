/** Real SEARCH-001 HTTPS content acceptance; private config also supplies fixturesDir.
 * Generate own fixtures with scripts/generate_task005_fixtures.py before running.
 * No response interception, source databases, OCR or LLM calls.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes,createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

async function helpers(browser, origin, outputDir) {
  assert.equal(new URL(origin).protocol, 'https:');
  await fs.mkdir(outputDir, { recursive: true });
  const contexts = [];
  const runtimeErrors = [];
  const results = [];
  let stage = 'browser setup';
  async function pageFor() {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: false });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => runtimeErrors.push(error.name));
    return page;
  }
  async function json(page, endpoint, method = 'GET', data, expected = 200) {
    stage = `API ${method} ${endpoint.split('?')[0]}`;
    const response = await page.evaluate(async ({ endpoint, method, data }) => {
      const me = method === 'GET' ? null : await (await fetch('/api/v1/users/me')).json();
      const response = await fetch('/api/v1' + endpoint, {
        method, headers: { Accept: 'application/json', ...(data === undefined ? {} : { 'Content-Type': 'application/json' }), ...(me ? { 'X-CSRF-Token': me.csrf_token } : {}) },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
      return { status: response.status, body: await response.json().catch(() => null) };
    }, { endpoint, method, data });
    assert.equal(response.status, expected, `${method} ${endpoint.split('?')[0]} status`);
    return response.body;
  }
  async function login(page, credentials) {
    stage = 'role UI login';
    await page.goto(origin);
    await page.getByRole('textbox', { name: 'Логин', exact: true }).fill(credentials.login);
    await page.getByLabel('Пароль', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: 'Войти в рабочее пространство' }).click();
    await page.getByRole('button', { name: 'Выйти', exact: true }).waitFor();
  }
  async function mutation(page, endpoint, action, method = 'POST', expected = 200) {
    stage = `UI ${method} ${endpoint} action`;
    const pending = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1' + endpoint && response.request().method() === method);
    const [response] = await Promise.all([pending, action()]);
    assert.equal(response.status(), expected, `${method} ${endpoint} UI status`);
    const uploaded = endpoint.match(/^\/documents\/([0-9a-f-]{36})\/files$/i);
    if (method === 'POST' && expected === 201 && uploaded) {
      // Chromium may evict binary-upload response bodies; verify canonical state after success.
      const card = await json(page, `/documents/${uploaded[1]}`);
      assert.equal(card.id, uploaded[1]);
      assert.ok(card.files.length > 0);
      return card;
    }
    stage = `UI ${method} ${endpoint} response JSON after verified ${expected}`;
    return expected === 204 ? null : response.json();
  }
  async function download(page, buttonName, filename) {
    stage = `UI download ${filename}`;
    await dismissNavigation(page);
    const [value] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: buttonName, exact: true }).click()]);
    assert.equal(await value.failure(), null);
    const target = path.join(outputDir, filename);
    await value.saveAs(target);
    assert.ok((await fs.stat(target)).size > 20);
    return target;
  }
  function pass(name, details = {}) {
    results.push({ name, status: 'PASS', ...details });
    console.log(`PASS ${name}`);
  }
  async function dismissNavigation(page) {
    await page.mouse.move(1000,100);
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>{const nav=document.getElementById('main-navigation');return !nav || nav.inert || nav.querySelector('.navigation-pin')?.getAttribute('aria-pressed')==='true';});
  }
  return { pageFor, json, login, mutation, download, dismissNavigation, pass, contexts, runtimeErrors, results, getStage: () => stage };
}

export async function runSearch({browser, origin, admin, outputDir, fixturesDir}) {
  const h = await helpers(browser, origin, outputDir);
  const {pageFor, json, login, pass, download, dismissNavigation} = h;
  const code = `search001_${Date.now().toString(36)}`, accounts = [];
  let ap, service, token, registry;
  try {
    ap = await pageFor(); await login(ap, admin);
    const me = await json(ap, '/users/me'), platform = await json(ap, '/admin/platform');
    const type = await json(ap, '/document-types', 'POST', {code, name: code}, 201);
    registry = await json(ap, '/registries', 'POST', {code, name: code, document_type_ids: [type.id]}, 201);
    const version = (await json(ap, `/document-types/${type.id}/versions`))[0];
    const schema = {sections: [{key: 'main', title: 'Сведения', fields: [
      {key: 'title', label: 'Название SEARCH', type: 'text', system: 'title', required: true, editable: true, searchable: true, filterable: true, exportable: true},
      {key: 'number', label: 'Номер SEARCH', type: 'text', system: 'reg_number', editable: true, filterable: true, exportable: true},
      {key: 'secret', label: 'Скрытое SEARCH', type: 'text', editable: true, read_roles: ['editor'], write_roles: ['editor'], searchable: true, exportable: true},
      {key: 'original', label: 'Оригинал SEARCH', type: 'file', editable: true},
    ]}]};
    await json(ap, `/form-schemas/${version.id}`, 'PUT', {schema});
    await json(ap, `/form-schemas/${version.id}/publish`, 'POST', {});
    const roles = {};
    for (const role of ['editor', 'viewer']) {
      const temporary = randomBytes(24).toString('base64url'), password = randomBytes(24).toString('base64url');
      const account = await json(ap, '/admin/users', 'POST', {login: `${code}_${role}`, display_name: `${code} ${role}`, password: temporary, roles: [role]}, 201); accounts.push(account.id);
      const page = await pageFor(); await login(page, {login: `${code}_${role}`, password: temporary});
      await page.getByLabel('Текущий пароль', {exact:true}).fill(temporary);
      await page.getByLabel('Новый пароль', {exact:true}).fill(password);
      await page.getByLabel('Повторите новый пароль', {exact:true}).fill(password);
      await page.getByRole('button', {name:'Изменить пароль', exact:true}).click();
      await page.getByRole('heading', {name:'Документы', exact:true, level:1}).waitFor();
      roles[role] = {page, account};
    }
    await json(ap, `/admin/access/${registry.id}`, 'PUT', {entries:[
      {user_id:me.id, permissions:['read','create','update','upload','download']},
      ...Object.entries(roles).map(([role, value]) => ({user_id:value.account.id, permissions:role === 'editor' ? ['read','create','update','upload','download'] : ['read','download']})),
    ]});
    const ep = roles.editor.page, vp = roles.viewer.page;
    const source = await json(ep, '/documents', 'POST', {registry_id:registry.id, document_type_id:type.id, schema_version_id:version.id,
      values:{title:'Благоустройство городской территории', number:'001/С-2026', secret:'ЗАКРЫТЫЙМАРКЕРSEARCH'},
      files:[{field_key:'original', name:'public.docx', mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', content_base64:(await fs.readFile(path.join(fixturesDir,'public.docx'))).toString('base64')}]}, 201);
    await json(ep, '/documents', 'POST', {registry_id:registry.id, document_type_id:type.id, schema_version_id:version.id, values:{title:'Благоустройство городской территории', number:'002/С-2026'}}, 201);
    for(let i=0;i<35;i++)await json(ep,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,values:{title:`Синтетическая карточка ${i}`,number:`${100+i}/SEARCH`}},201);
    let content;
    for (let i=0;i<120;i++) {
      content = await json(ep, `/documents/${source.id}/content`);
      const state = await json(ep, `/documents/${source.id}/search-index`);
      if (state.ready && content.items.some(x=>x.state==='completed')) break;
      await delay(1000);
    }
    const extraction = content.items.find(x=>x.state==='completed'); assert.ok(extraction);
    async function search(page, query, scope='all') {return json(page, '/search', 'POST', {query, scope, registry_ids:[registry.id], mode:page===vp?'viewer':'editor'});}
    for (const query of ['001с2026','благоустройство','благоустроиство']) assert.ok((await search(vp,query)).items.some(x=>x.document.id===source.id));
    assert.equal((await search(vp,'ЗАКРЫТЫЙМАРКЕРSEARCH')).total,0);
    assert.equal((await search(ep,'ЗАКРЫТЫЙМАРКЕРSEARCH')).total,1);
    pass('real PostgreSQL number title typo OR search and hidden-field ACL');
    await vp.goto(`${origin}/view?registry_id=${registry.id}`);
    await vp.getByText('Найдено: 37',{exact:true}).waitFor();
    await vp.getByRole('button',{name:'Вперёд',exact:true}).click();
    await vp.getByText('Страница 2',{exact:true}).waitFor();
    await vp.waitForFunction(()=>document.querySelectorAll('.search-hit-list > li').length===17);
    const resultList=vp.locator('.search-results');
    await resultList.getByRole('button',{name:'Открыть карточку',exact:true}).nth(6).scrollIntoViewIfNeeded();
    const scrollBefore=await resultList.evaluate(element=>element.scrollTop);
    const cursorBefore=new URL(vp.url()).searchParams.get('cursor');assert.ok(scrollBefore>0&&cursorBefore);
    await resultList.getByRole('button',{name:'Открыть карточку',exact:true}).nth(6).click();
    await vp.getByRole('button',{name:'Содержание',exact:true}).waitFor();
    assert.equal(new URL(vp.url()).searchParams.get('cursor'),cursorBefore);
    assert.ok(Math.abs(await resultList.evaluate(element=>element.scrollTop)-scrollBefore)<2);
    pass('result selection retains actual second-page cursor and scrolled list');
    await vp.getByLabel('Поиск', {exact:true}).fill('благоустройство');
    await vp.getByLabel('Поиск', {exact:true}).press('Enter');
    await vp.getByText('Найдено: 2', {exact:true}).waitFor();
    await vp.getByRole('button',{name:'Открыть карточку',exact:true}).first().click();
    await vp.getByRole('button',{name:'Содержание',exact:true}).waitFor();
    assert.equal(await vp.getByRole('button',{name:'Открыть карточку',exact:true}).count(),2);
    assert.equal(await vp.getByLabel('Поиск',{exact:true}).inputValue(),'благоустройство');
    await vp.reload(); await vp.getByRole('button',{name:'Содержание',exact:true}).waitFor();
    await dismissNavigation(vp);
    assert.equal(new URL(vp.url()).searchParams.get('q'),'благоустройство');
    await vp.screenshot({path:path.join(outputDir,'search-desktop.png'),fullPage:true});
    await vp.evaluate(()=>window.scrollTo(0,0));
    await vp.screenshot({path:path.join(outputDir,'search-viewport.png'),fullPage:false});
    const csvPath = await download(vp,'Экспорт CSV','search.csv');
    assert.equal((await fs.readFile(csvPath,'utf8')).includes('ЗАКРЫТЫЙМАРКЕРSEARCH'),false);
    await download(vp,'Экспорт XLSX','search.xlsx');
    pass('two-pane selection URL reload and ACL-safe CSV XLSX exports');
    await vp.getByLabel('Поиск',{exact:true}).fill('"городском благоустройстве"');
    await vp.getByRole('button',{name:'Найти',exact:true}).click();
    await vp.getByText('Найдено: 1',{exact:true}).waitFor();
    await vp.getByRole('button',{name:'Открыть фрагмент источника',exact:true}).first().click();
    await vp.locator('.content-block.selected').waitFor();
    await vp.screenshot({path:path.join(outputDir,'search-source.png'),fullPage:true});
    await vp.setViewportSize({width:390,height:844});
    assert.ok(await vp.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
    await vp.screenshot({path:path.join(outputDir,'search-mobile.png'),fullPage:true});
    await vp.setViewportSize({width:1440,height:1000});
    pass('source fragment opens selected immutable block and narrow viewport fits');
    await ep.goto(`${origin}/work?registry_id=${registry.id}&document=${source.id}&q=благоустройство`);
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
    await ep.getByLabel('Название SEARCH',{exact:false}).fill('Благоустройство обновлено SEARCH');
    await ep.getByRole('button',{name:'Сохранить карточку',exact:true}).click();
    await ep.locator('.search-results').getByRole('heading',{name:'Благоустройство обновлено SEARCH',exact:true}).waitFor();
    pass('saved card refreshes retained search list after asynchronous indexing');
    service = await json(ap,'/admin/service-accounts','POST',{name:code, registry_ids:[registry.id], operations:['documents','content']},201);
    token = await json(ap,`/admin/service-accounts/${service.id}/tokens`,'POST',{reason:'Синтетическая проверка поиска', expires_in_days:1},201);
    async function consumer(endpoint, method='GET', data, expected=200) {
      const context = await browser.newContext({baseURL:origin,ignoreHTTPSErrors:false,extraHTTPHeaders:{Authorization:`Bearer ${token.token}`}});
      try {const result=await context.request.fetch('/api/service/v1'+endpoint,{method,...(data?{data}:{})}); assert.equal(result.status(),expected);return expected===200?result.json():null;} finally {await context.close();}
    }
    const retrieval = await consumer('/retrieval','POST',{query:'городском благоустройстве',registry_ids:[registry.id]});
    const chunk = retrieval.items.find(x=>x.source_type==='file'); assert.ok(chunk && chunk.checksum && chunk.citation.block_ids.length);
    assert.equal((await consumer(`/chunks/${chunk.chunk_id}`)).checksum,chunk.checksum);
    assert.ok((await consumer(`/chunks/${chunk.chunk_id}/neighbors`)).items.length);
    await json(ap,`/documents/${source.id}/acl`,'PUT',{entries:[{user_id:me.id,permissions:['read','download']},{user_id:roles.editor.account.id,permissions:['read','update','download']}]});
    await consumer(`/chunks/${chunk.chunk_id}`,'GET',undefined,404);
    assert.equal((await search(vp,'001с2026')).total,0);
    pass('service retrieval checksums exact neighbors and immediate document ACL revocation');
    await ap.goto(`${origin}/admin`);
    const navigation = ap.getByRole('button',{name:'Открыть навигацию',exact:true});
    if(await navigation.count() && await navigation.getAttribute('aria-expanded') !== 'true')await navigation.click();
    await ap.getByRole('button',{name:'Эксплуатация',exact:true}).click();
    await ap.getByLabel('Реестр эксплуатационных данных',{exact:false}).selectOption(registry.id);
    await ap.getByRole('heading',{name:'Поисковый индекс',exact:true}).waitFor();
    await ap.getByLabel('Причина перестройки индекса',{exact:true}).fill('Синтетическая проверка SEARCH-001');
    await ap.getByRole('button',{name:'Перестроить индекс реестра',exact:true}).click();
    await ap.getByText('Индекс поставлен на фоновую перестройку',{exact:true}).waitFor();
    await ap.screenshot({path:path.join(outputDir,'search-admin.png'),fullPage:true});
    assert.deepEqual(h.runtimeErrors,[]);
    pass('administrator index diagnostics controlled rebuild and no browser runtime errors');
    return {status:'PASS',source_sha:platform.source_sha,scenarios:h.results};
  } catch(error) {
    await fs.writeFile(path.join(outputDir,'failure.txt'),String(error.stack || error).split(origin).join('[pilot]'));
    throw error;
  } finally {
    if (ap && service) {
      if(token)await json(ap,`/admin/service-accounts/${service.id}/tokens/${token.id}/revoke`,'POST',{reason:'Завершение синтетической проверки'},204);
      await json(ap,`/admin/service-accounts/${service.id}`,'PATCH',{name:service.name,registry_ids:service.registry_ids,operations:service.operations,state:'disabled',reason:'Завершение синтетической проверки'});
    }
    if(ap)for(const id of accounts)await json(ap,`/admin/users/${id}`,'PATCH',{active:false});
    for(const context of h.contexts){for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`last-${h.contexts.indexOf(context)}.png`),fullPage:true}).catch(()=>{});await context.close();}
    await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runSearch({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`SEARCH-001 HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`SEARCH-001 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
