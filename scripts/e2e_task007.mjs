/** Real Task007 HTTPS Process Knowledge acceptance with isolated browser roles.
 * Private configuration matches Task005: origin, adminCredentialsFile, outputDir,
 * playwrightModule, fixturesDir. Synthetic sources only; no execution instances,
 * response interception, source SQL Server access or external document downloads.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes} from 'node:crypto';
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
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: buttonName, exact: true }).click();
    const value = await pending;
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
  return { pageFor, json, login, mutation, download, pass, contexts, runtimeErrors, results, getStage: () => stage };
}

export async function runTask007({browser,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir);
  const {pageFor,json,login,mutation,pass}=h;
  const suffix=Date.now().toString(36),code=`task007_${suffix}`,title=`Task007 ${suffix}`;
  const accounts=[],sensitive=[admin.password],temporaryRegistries=new Set(),receipt={};
  let ap;
  try {
    ap=await pageFor();await login(ap,admin);
    const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');
    pass('administrator strict HTTPS login and deployed source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:`${title} форма подготовки`},201);
    const form=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const formSchema={sections:[{key:'main',title:'Синтетическая подготовка документа',fields:[
      {key:'title',label:'Название Task007',type:'text',system:'title',required:true,editable:true,filterable:true,exportable:true},
      {key:'route',label:'Маршрут подготовки',type:'text',editable:true},
      {key:'public_file',label:'Оригинал Task007',type:'file',editable:true},
    ]}]};
    await json(ap,`/form-schemas/${form.id}`,'PUT',{schema:formSchema});await json(ap,`/form-schemas/${form.id}/publish`,'POST',{});
    const sourceRegistry=await json(ap,'/registries','POST',{code:`${code}_sources`,name:`${title} основания`,document_type_ids:[type.id]},201);
    const processRegistry=await json(ap,'/registries','POST',{code:`${code}_processes`,name:`${title} процессы`,document_type_ids:[type.id]},201);
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'),password=randomBytes(24).toString('base64url');sensitive.push(temporary,password);
      const account=await json(ap,'/admin/users','POST',{login:`${code}_${role}`,display_name:`${title} ${role}`,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:`${code}_${role}`,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Мои реестры',exact:true}).waitFor();
      roles[role]={page,account,permissions:role==='editor'?['read','create','update','upload','download']:['read','download']};
    }
    const ep=roles.editor.page,vp=roles.viewer.page;
    for(const registry of [sourceRegistry,processRegistry])await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','create','update','upload','download']},...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});
    const source=await json(ep,'/documents','POST',{registry_id:sourceRegistry.id,document_type_id:type.id,schema_version_id:form.id,values:{title:`${title} синтетическое основание`,route:'standard'},files:[{field_key:'public_file',name:'public.docx',mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',content_base64:(await fs.readFile(path.join(fixturesDir,'public.docx'))).toString('base64')}]},201);
    let extraction;
    const deadline=Date.now()+120000;
    while(Date.now()<deadline) {
      const content=await json(ep,`/documents/${source.id}/content`);extraction=content.items.find(item=>item.name==='public.docx');
      if(extraction?.state==='completed')break;
      if(extraction&&['failed','unsupported','encrypted','no_text'].includes(extraction.state))throw new Error(`Synthetic source extraction ${extraction.state}/${extraction.error_code}`);
      await delay(1000);
    }
    assert.equal(extraction?.state,'completed');const block=extraction.blocks.find(item=>item.kind==='paragraph');assert.ok(block);
    pass('separate process and source registry grants with actual DOCX paragraph extraction');
    // Process topology, step instructions and source selection are authored in the UI.
    await ap.getByRole('button',{name:'Конструктор процессов',exact:true}).click();
    await ap.getByRole('button',{name:'Создать процесс',exact:true}).click();
    await ap.getByLabel('Название процесса',{exact:true}).fill(`${title} Подготовка документа`);
    await ap.getByLabel('Код процесса',{exact:true}).fill(code);
    await ap.getByLabel('Реестр процесса',{exact:false}).selectOption(processRegistry.id);
    await ap.getByLabel('Подразделение процесса',{exact:true}).fill('Синтетическое подразделение');
    await ap.getByLabel('Категория процесса',{exact:true}).fill('Подготовка документа/закупки');
    await ap.getByLabel('Назначение процесса',{exact:true}).fill('Синтетическое описание процесса с двумя условиями; без выполнения и юридических утверждений');
    const process=await mutation(ap,'/processes',()=>ap.getByRole('button',{name:'Сохранить процесс',exact:true}).click(),'POST',201);
    const draft=(await json(ap,`/processes/${process.id}/versions`))[0];
    await ap.getByLabel('Дата начала действия версии',{exact:true}).fill('2026-01-01');
    // Empty graph is a useful visible server-side rejection before any valid publication.
    await mutation(ap,`/process-versions/${draft.id}/publish`,()=>ap.getByRole('button',{name:'Опубликовать процесс',exact:true}).click(),'POST',422);
    await ap.getByRole('alert').first().waitFor();assert.ok((await ap.getByRole('alert').first().innerText()).trim().length>10);
    const steps=[];
    for(const [index,name] of ['Определить маршрут','Подготовить документ','Подготовить закупку','Уточнить исходные данные','Завершить подготовку'].entries()) {
      await ap.getByRole('button',{name:'+ Добавить шаг',exact:true}).click();
      const properties=ap.locator('.process-builder .property-panel');
      await properties.getByLabel('Название шага',{exact:true}).fill(name);
      await properties.getByLabel(/^Инструкция шага/).fill(`Task007 инструкция ${index+1}: ${name}. Используйте только разрешённое синтетическое основание.`);
      await properties.getByLabel('Исполнитель шага',{exact:true}).fill('Ответственный сотрудник');
      await properties.getByLabel('Входные данные',{exact:true}).fill('Исходные сведения');await properties.getByLabel('Результаты шага',{exact:true}).fill('Результат подготовки');
      await properties.getByLabel('Срок шага',{exact:true}).fill('1 рабочий день');await properties.getByLabel('Информационная система',{exact:true}).fill('ACTI');
      if(index===0)await properties.getByLabel('Начальный шаг',{exact:true}).check();if(index===4)await properties.getByLabel('Завершающий шаг',{exact:true}).check();
      steps.push({key:await ap.getByLabel('Перейти к шагу',{exact:false}).inputValue(),title:name});
    }
    await ap.getByLabel('Перейти к шагу',{exact:false}).selectOption(steps[0].key);
    const properties=ap.locator('.process-builder .property-panel');
    await properties.getByLabel('Тип формы шага',{exact:false}).selectOption(type.id);
    await properties.getByLabel('Опубликованная форма процесса',{exact:false}).selectOption(form.id);
    const sources=properties.getByRole('group',{name:'Основания шага',exact:true});
    await sources.getByRole('button',{name:'+ Основание шага',exact:true}).click();
    await sources.getByLabel('Поиск документа основания',{exact:true}).fill(source.title);
    await sources.getByLabel('Документ основания',{exact:false}).selectOption(source.id);
    await sources.getByLabel('Версия: Основание шага',{exact:false}).selectOption(extraction.version_id);
    await sources.getByLabel('Оригинал: Основание шага',{exact:false}).selectOption(extraction.id);
    await sources.getByLabel('Фрагмент: Основание шага',{exact:false}).selectOption(block.id);
    await sources.getByLabel('Пояснение основания',{exact:true}).fill('Почему шаг существует: синтетический абзац версии 1');
    await sources.getByRole('button',{name:'Добавить основание',exact:true}).click();
    for(const route of [
      {from:0,to:1,condition:'standard',label:'Документ'},
      {from:0,to:2,condition:'urgent',label:'Закупка'},
      {from:0,to:3,otherwise:true,label:'Иначе'},
      {from:1,to:4,label:'Документ подготовлен'},
      {from:2,to:4,label:'Материалы подготовлены'},
      {from:3,to:4,label:'Исходные сведения уточнены'},
    ]) {
      await ap.getByRole('button',{name:'+ Добавить переход',exact:true}).click();const edge=ap.locator('.process-builder .property-panel');
      await edge.getByLabel('Из шага',{exact:false}).selectOption(steps[route.from].key);await edge.getByLabel('В шаг',{exact:false}).selectOption(steps[route.to].key);
      await edge.getByLabel('Название перехода',{exact:true}).fill(route.label);
      await edge.getByLabel('Правило перехода',{exact:false}).selectOption(route.otherwise?'else':route.condition?'condition':'always');
      if(route.condition){await edge.getByLabel('Поле условия',{exact:false}).selectOption('route');await edge.getByLabel('Оператор условия',{exact:false}).selectOption('eq');await edge.getByLabel('Значение условия',{exact:false}).fill(route.condition);}
    }
    await mutation(ap,`/process-versions/${draft.id}/validate`,()=>ap.getByRole('button',{name:'Проверить процесс',exact:true}).click(),'POST',200);
    await mutation(ap,`/process-versions/${draft.id}/publish`,()=>ap.getByRole('button',{name:'Опубликовать процесс',exact:true}).click(),'POST',200);
    const v1=await json(ap,`/process-versions/${draft.id}`);assert.equal(v1.state,'published');assert.equal(v1.steps.length,5);assert.equal(v1.transitions.filter(edge=>edge.condition).length,2);assert.equal(v1.transitions.filter(edge=>edge.is_else).length,1);
    await ap.locator('.process-builder .process-graph h3').scrollIntoViewIfNeeded();await ap.screenshot({path:path.join(outputDir,'admin-process-builder-viewport.png'),fullPage:false});
    pass('administrator visually authors five steps two conditions explicit else and exact published form paragraph then publishes v1');
    await ap.getByRole('button',{name:'Как читатель',exact:true}).click();const previewPanel=ap.locator('.process-builder .preview');
    await previewPanel.getByLabel('Маршрут подготовки',{exact:false}).fill('urgent');
    const uiPreview=await mutation(ap,`/process-versions/${v1.id}/preview`,()=>previewPanel.getByRole('button',{name:'Проверить маршрут',exact:true}).click(),'POST',200);assert.deepEqual(uiPreview.next_step_keys,[steps[2].key]);
    await previewPanel.getByText(`Следующий шаг: ${steps[2].title}`,{exact:true}).waitFor();await previewPanel.getByRole('button',{name:'Закрыть предпросмотр процесса',exact:true}).click();
    for(const [value,expected] of [['standard',steps[1].key],['urgent',steps[2].key],['other',steps[3].key]]) {
      const preview=await json(ap,`/process-versions/${v1.id}/preview`,'POST',{step_key:steps[0].key,values:{route:value}});assert.deepEqual(preview.next_step_keys,[expected]);
    }
    pass('real declarative preview selects both typed conditional routes and the explicit else without process execution');
    const v2=await mutation(ap,`/processes/${process.id}/versions`,()=>ap.getByRole('button',{name:'Новая версия процесса',exact:true}).click(),'POST',201);
    await ap.getByLabel('Перейти к шагу',{exact:false}).selectOption(steps[1].key);
    await ap.locator('.process-builder .property-panel').getByLabel(/^Инструкция шага/).fill('Task007 инструкция редакции 2: уточнённый синтетический порядок подготовки документа.');
    await ap.getByLabel('Дата начала действия версии',{exact:true}).fill('2026-02-01');
    await mutation(ap,`/process-versions/${v2.id}/publish`,()=>ap.getByRole('button',{name:'Опубликовать процесс',exact:true}).click(),'POST',200);
    const published2=await json(ap,`/process-versions/${v2.id}`);assert.equal(published2.state,'published');
    const after2=await json(ap,`/process-versions/${v1.id}`);assert.deepEqual(after2.steps,v1.steps);assert.deepEqual(after2.transitions,v1.transitions);
    pass('administrator clones and publishes v2 while the complete v1 instructions graph form and source pins remain unchanged');
    // Publish another form and upload genuinely different source bytes after both process versions.
    const nextForm=await json(ap,`/document-types/${type.id}/versions`,'POST',{schema:{sections:[{key:'main',title:'Новая форма',fields:[{key:'title',label:'Название новой формы',type:'text',system:'title',editable:true}]}]}},201);
    await json(ap,`/form-schemas/${nextForm.id}/publish`,'POST',{});
    await ep.goto(`${origin}/work?document=${source.id}`);await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();
    await mutation(ep,`/documents/${source.id}/files`,async()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await fs.readFile(path.join(fixturesDir,'private.docx'))}),'POST',201);
    const stillV1=await json(ap,`/process-versions/${v1.id}`);assert.deepEqual(stillV1.steps,v1.steps);assert.deepEqual(stillV1.transitions,v1.transitions);
    const v1route=await json(ap,`/process-versions/${v1.id}/preview`,'POST',{step_key:steps[0].key,values:{route:'standard'}});assert.deepEqual(v1route.next_step_keys,[steps[1].key]);
    pass('later form and original publications do not rewrite prior process conditions or source version');
    async function openProcess(page,versionId) {
      await page.goto(`${origin}/${page===vp?'view':'work'}`);await page.getByRole('button',{name:'Процессы',exact:true}).first().click();
      await page.getByRole('combobox',{name:/^Реестр процессов/}).selectOption(processRegistry.id);
      await page.getByRole('combobox',{name:/^Процесс(?:\s|$)/}).selectOption(process.id);
      if(versionId)await page.getByRole('combobox',{name:/^Версия процесса/}).selectOption(versionId);
      await page.locator('.process-view').waitFor();
    }
    await openProcess(ep);await ep.getByRole('heading',{name:`${process.name} · версия 2`,exact:true}).waitFor();
    await ep.getByLabel('Шаг процесса',{exact:false}).selectOption(steps[1].key);await ep.getByText('Task007 инструкция редакции 2: уточнённый синтетический порядок подготовки документа.',{exact:true}).waitFor();
    await openProcess(vp,v1.id);await vp.getByLabel('Шаг процесса',{exact:false}).selectOption(steps[0].key);
    await vp.getByRole('button',{name:'Почему этот шаг существует?',exact:true}).click();
    const why=vp.locator('.process-basis').filter({has:vp.getByRole('button',{name:'Почему этот шаг существует?',exact:true})});
    await why.getByRole('heading',{name:`${source.title} · оригинал версии 1`,exact:true}).waitFor();
    assert.ok((await why.locator('blockquote.content-text').innerText()).includes(block.text));
    const pending=vp.waitForEvent('download');await why.getByRole('button',{name:'Скачать оригинал основания версии 1',exact:true}).click();const downloaded=await pending;assert.equal(await downloaded.failure(),null);
    const oldOriginal=path.join(outputDir,'process-basis-version-1.docx');await downloaded.saveAs(oldOriginal);assert.deepEqual(await fs.readFile(oldOriginal),await fs.readFile(path.join(fixturesDir,'public.docx')));
    await vp.screenshot({path:path.join(outputDir,'reader-process-why-viewport.png'),fullPage:false});
    await why.getByRole('link',{name:'Открыть документ основания',exact:true}).click();await vp.locator('.content-block.selected').waitFor();assert.ok((await vp.locator('.content-block.selected').innerText()).includes(block.text));
    await openProcess(vp,v1.id);await vp.setViewportSize({width:430,height:932});await vp.locator('.process-view .process-graph h3').scrollIntoViewIfNeeded();await vp.screenshot({path:path.join(outputDir,'reader-process-narrow-viewport.png'),fullPage:false});await vp.setViewportSize({width:1440,height:1000});
    pass('editor sees current v2 reader opens v1 exact Why paragraph and downloads original v1 with desktop and narrow viewport evidence');
    const invalidDraft=await json(ap,`/processes/${process.id}/versions`,'POST',{},201);
    function payload(version) {
      const stepKeys=['key','title','instruction','executor_role','department','duration_hours','deadline','inputs','outputs','system','control_note','is_start','is_terminal','form_schema_id'];
      return {effective_from:'2026-03-01',steps:version.steps.map(step=>({...Object.fromEntries(stepKeys.filter(key=>step[key]!==undefined).map(key=>[key,step[key]])),sources:step.sources.map(item=>Object.fromEntries(['document_id','version_id','block_id','note'].filter(key=>item[key]!==undefined).map(key=>[key,item[key]]))),templates:[]})),transitions:version.transitions.map(edge=>Object.fromEntries(['key','from_step','to_step','label','condition','is_else'].map(key=>[key,edge[key]])))};
    }
    const foreignType=await json(ap,'/document-types','POST',{code:`${code}_foreign`,name:`${title} посторонняя форма`},201);
    const foreignForm=(await json(ap,`/document-types/${foreignType.id}/versions`))[0];
    await json(ap,`/form-schemas/${foreignForm.id}`,'PUT',{schema:formSchema});await json(ap,`/form-schemas/${foreignForm.id}/publish`,'POST',{});
    let invalidRevision=invalidDraft.revision;
    for(const kind of ['missing_start','unreachable','ambiguous_else','cycle','foreign_schema']) {
      const body=payload(published2);
      if(kind==='missing_start')body.steps.forEach(step=>{step.is_start=false;});
      if(kind==='unreachable')body.steps.push({...body.steps[4],key:'orphan',title:'Недостижимый шаг'});
      if(kind==='ambiguous_else')body.transitions.push({...body.transitions.find(edge=>edge.is_else),key:'duplicate_else'});
      if(kind==='cycle') {body.transitions.find(edge=>edge.from_step===steps[1].key).to_step=steps[2].key;body.transitions.find(edge=>edge.from_step===steps[2].key).to_step=steps[1].key;}
      if(kind==='foreign_schema')body.steps[0].form_schema_id=foreignForm.id;
      const saved=await json(ap,`/process-versions/${invalidDraft.id}`,'PUT',{...body,revision:invalidRevision});invalidRevision=saved.revision;
      const rejected=await json(ap,`/process-versions/${invalidDraft.id}/publish`,'POST',{revision:invalidRevision},422);assert.ok(rejected.error.code&&rejected.error.message);
    }
    pass('real server rejects missing start unreachable step ambiguous else cycle and foreign schema before publication');
    await json(ap,`/documents/${source.id}/acl`,'PUT',{entries:[{user_id:administrator.id,permissions:['read','update','upload','download']},{user_id:roles.editor.account.id,permissions:['read','update','upload','download']}]});
    const hiddenVersion=await json(vp,`/process-versions/${v1.id}?mode=viewer`),hiddenBasis=await json(vp,`/process-versions/${v1.id}/steps/${steps[0].key}/basis?mode=viewer`);
    assert.equal(hiddenVersion.steps.length,5);assert.deepEqual(hiddenBasis.items,[{restricted:true,label:'Основание ограничено'}]);
    for(const secret of [source.id,source.title,extraction.version_id,extraction.file_id,block.id,block.text,'Почему шаг существует: синтетический абзац версии 1'])assert.equal((JSON.stringify(hiddenVersion)+JSON.stringify(hiddenBasis)).includes(secret),false);
    await openProcess(vp,v1.id);await vp.getByRole('button',{name:'Почему этот шаг существует?',exact:true}).click();await vp.getByText('Основание ограничено',{exact:true}).first().waitFor();
    assert.equal((await vp.locator('.process-view').innerText()).includes(source.title),false);await vp.screenshot({path:path.join(outputDir,'reader-process-restricted-viewport.png'),fullPage:false});
    pass('source ACL hides source title identifiers note and paragraph while process instructions remain usable');
    const current=await json(ap,`/admin/access/${processRegistry.id}`);
    await json(ap,`/admin/access/${processRegistry.id}`,'PUT',{entries:current.entries.filter(entry=>entry.user_id!==roles.viewer.account.id&&entry.user_id!==administrator.id)});
    for(const page of [vp,ap]) {
      for(const endpoint of [`/processes/${process.id}`,`/process-versions/${v1.id}`,`/process-versions/${v1.id}/steps/${steps[0].key}/basis`])await json(page,endpoint,'GET',undefined,404);
      const listed=await json(page,`/processes?registry_id=${processRegistry.id}`);assert.equal(listed.length,0);assert.equal(JSON.stringify(listed).includes(process.name),false);
    }
    await json(vp,`/process-versions/${v1.id}/preview`,'POST',{step_key:steps[0].key,values:{route:'standard'}},404);
    pass('process registry revocation hides list counts direct versions and basis for reader and administrator without content bypass');
    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id));
    const previous=await json(ap,`/admin/access/${legacyRegistry.id}`);await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...previous.entries,...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000007');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');
    const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);assert.equal(legacyCard.reg_number,'000007');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000007',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();
    pass('ACTI v2 number nullable publication reference and volume remain compatible');
    for(const page of [ep,vp]) {await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('editor reader forbidden administration login logout and no application errors');
    Object.assign(receipt,{status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{process_id:process.id,version_ids:[v1.id,v2.id],process_registry_id:processRegistry.id,source_document_id:source.id}});return receipt;
  } catch(error) {
    const failure={stage:h.getStage(),message:String(error.message),stack:String(error.stack||'')};
    await fs.writeFile(path.join(outputDir,'failure.txt'),sensitive.reduce((message,value)=>message.split(value).join('[redacted]'),JSON.stringify(failure,null,2)));
    console.error(`Task007 failed operation: ${failure.stage}`);let index=0;
    for(const context of h.contexts)for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`failure-page-${index++}.png`),fullPage:false}).catch(()=>{});
    throw error;
  } finally {
    const failures=[];let accountsVerified=false;
    if(ap)for(const registryId of temporaryRegistries)try {const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:current.entries.filter(value=>!accounts.includes(value.user_id))});const restored=await json(ap,`/admin/access/${registryId}`);assert.equal(restored.entries.some(value=>accounts.includes(value.user_id)),false);}catch {failures.push('temporary registry grants');}
    if(ap)for(const id of accounts)try {await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch {failures.push('synthetic account deactivation');}
    if(ap&&accounts.length)try {const users=await json(ap,'/admin/users');assert.ok(accounts.every(id=>users.some(user=>user.id===id&&!user.active)));accountsVerified=true;}catch {failures.push('synthetic account verification');}
    receipt.cleanup_verified=accountsVerified&&failures.length===0;
    for(const context of h.contexts)await context.close();await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));await fs.writeFile(path.join(outputDir,'cleanup.json'),JSON.stringify({status:failures.length?'FAIL':'PASS',cleanup_verified:receipt.cleanup_verified,accounts_disabled:accounts.length,temporary_grants_removed:temporaryRegistries.size,failures},null,2));assert.deepEqual(failures,[],'Synthetic cleanup must succeed');
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runTask007({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task007 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`Task007 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
