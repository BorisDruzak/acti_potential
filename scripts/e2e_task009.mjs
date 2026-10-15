/** Real Task009 HTTPS process execution acceptance and read-only restart verification.
 * Private config matches Task005; previousReceiptFile selects verification-only mode.
 * Isolated real browser roles, actual PostgreSQL/API/worker, no response mocks or clock changes.
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

export async function runTask009({browser,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir);
  const {pageFor,json,login,mutation,pass}=h;
  const suffix=Date.now().toString(36),code=`task009_${suffix}`,title=`Task009 ${suffix}`;
  const accounts=[],sensitive=[admin.password],temporaryRegistries=new Set(),adminRestore=new Set(),receipt={};
  let ap,adminId;
  try {
    ap=await pageFor();await login(ap,admin);
    const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');adminId=administrator.id;
    pass('administrator strict HTTPS login and deployed source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:`${title} форма подготовки`},201);
    const form=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const formSchema={sections:[{key:'main',title:'Синтетическая подготовка документа',fields:[
      {key:'title',label:'Название Task009',type:'text',system:'title',required:true,editable:true,filterable:true,exportable:true},
      {key:'route',label:'Маршрут подготовки',type:'text',required:true,editable:true},
      {key:'secret',label:'Скрытый результат Task009',type:'text',editable:true,read_roles:['editor'],write_roles:['editor']},
      {key:'public_file',label:'Оригинал Task009',type:'file',editable:true},
    ]}]};
    await json(ap,`/form-schemas/${form.id}`,'PUT',{schema:formSchema});await json(ap,`/form-schemas/${form.id}/publish`,'POST',{});
    const sourceRegistry=await json(ap,'/registries','POST',{code:`${code}_sources`,name:`${title} основания`,document_type_ids:[type.id]},201);
    const processRegistry=await json(ap,'/registries','POST',{code:`${code}_processes`,name:`${title} процессы`,document_type_ids:[type.id]},201);
    adminRestore.add(sourceRegistry.id);adminRestore.add(processRegistry.id);
    const roles={};
    for(const role of ['editor','editor_other','viewer']) {
      const temporary=randomBytes(24).toString('base64url'),password=randomBytes(24).toString('base64url');sensitive.push(temporary,password);
      const account=await json(ap,'/admin/users','POST',{login:`${code}_${role}`,display_name:`${title} ${role}`,password:temporary,roles:[role==='viewer'?'viewer':'editor']},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:`${code}_${role}`,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Мои реестры',exact:true}).waitFor();
      roles[role]={page,account,permissions:role!=='viewer'?['read','create','update','upload','download']:['read','download']};
    }
    const ep=roles.editor.page,op=roles.editor_other.page,vp=roles.viewer.page;
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
    async function createResultDocument(route) {
      await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:sourceRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(type.id);
      await ep.getByLabel('Название Task009',{exact:false}).fill(`${title} результат ${route}`);await ep.getByLabel('Маршрут подготовки',{exact:false}).fill(route);await ep.getByLabel('Скрытый результат Task009',{exact:false}).fill('СЕКРЕТНЫЙРЕЗУЛЬТАТTASK009');await ep.getByLabel('Оригинал Task009',{exact:false}).setInputFiles(path.join(fixturesDir,'public.docx'));
      const card=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);const options=await json(ep,`/documents/${card.id}/relation-options`);return {...card,version_id:options.versions[0].id};
    }
    const resultStandard=await createResultDocument('standard'),resultUrgent=await createResultDocument('urgent');
    const template=await json(ep,'/knowledge/templates','POST',{registry_id:processRegistry.id,name:`${title} закреплённый шаблон`,purpose:'Синтетический исходный шаблон исполнения',document_type_id:type.id,form_schema_id:form.id,original:{document_id:source.id,version_id:extraction.version_id,file_id:extraction.file_id}},201);
    const templateV1=await json(ep,`/knowledge/template-versions/${template.version.id}/publish`,'POST',{revision:template.version.revision});
    const process=await json(ap,'/processes','POST',{registry_id:processRegistry.id,code,name:`${title} Подготовка документа`,purpose:'Синтетическое исполнение двух допустимых маршрутов без реального регламента'},201);
    const draft=(await json(ap,`/processes/${process.id}/versions`))[0];
    const steps=[
      {key:'start',title:'Проверить исходные сведения',instruction:'Подтвердите обычную карточку результата и её оригинал.',is_start:true,form_schema_id:form.id,inputs:['Исходные сведения проверены'],outputs:['Маршрут подтверждён'],control_note:'Оригинал результата выбран',sources:[{document_id:source.id,version_id:extraction.version_id,block_id:block.id,note:'Синтетический исходный абзац Task009'}],template_versions:[templateV1.id]},
      {key:'document',title:'Подготовить документ',instruction:'Проверьте обычный маршрут подготовки.',inputs:['Проект документа проверен'],outputs:['Результат шага подготовлен']},
      {key:'urgent',title:'Подготовить срочный документ',instruction:'Проверьте срочный маршрут подготовки.',outputs:['Материалы срочного маршрута готовы']},
      {key:'clarify',title:'Уточнить сведения',instruction:'Уточните неподтверждённый маршрут.'},
      {key:'finish',title:'Завершить дело',instruction:'Проверьте итоговый синтетический результат.',is_terminal:true,inputs:['Итоговый результат проверен'],outputs:['Дело завершено']},
    ].map(step=>({executor_role:'Редактор',department:'Синтетическое подразделение',duration_hours:24,...step}));
    const transitions=[
      {key:'normal',from_step:'start',to_step:'document',label:'Обычная подготовка',condition:{field:'route',op:'eq',value:'standard'}},
      {key:'urgent_route',from_step:'start',to_step:'urgent',label:'Срочная подготовка',condition:{field:'route',op:'eq',value:'urgent'}},
      {key:'else_route',from_step:'start',to_step:'clarify',label:'Иначе',is_else:true},
      ...['document','urgent','clarify'].map(key=>({key:`${key}_done`,from_step:key,to_step:'finish',label:'Результат проверен'})),
    ];
    await json(ap,`/process-versions/${draft.id}`,'PUT',{revision:draft.revision,effective_from:'2026-01-01',steps,transitions});
    await ap.getByRole('button',{name:'Конструктор процессов',exact:true}).click();await ap.getByRole('combobox',{name:/^Процесс(?:\s|$)/}).selectOption(process.id);
    const processV1=await mutation(ap,`/process-versions/${draft.id}/publish`,()=>ap.getByRole('button',{name:'Опубликовать процесс',exact:true}).click(),'POST',200);
    pass('administrator publishes a synthetic conditional process with required checklists exact form paragraph and universal template');
    async function work(page) {await page.goto(`${origin}/${page===vp?'view':'work'}`);await page.getByRole('button',{name:page===vp?'Ход работы':'Моя работа',exact:true}).first().click();await page.getByLabel('Реестр дел',{exact:false}).selectOption(processRegistry.id);}
    async function startInstance(name) {
      await work(ep);await ep.getByRole('button',{name:'Начать исполнение процесса',exact:true}).click();await ep.getByLabel('Процесс для исполнения',{exact:false}).selectOption(process.id);await ep.getByLabel('Версия процесса для исполнения',{exact:false}).selectOption(processV1.id);await ep.getByLabel('Название дела',{exact:true}).fill(name);await ep.getByLabel('Ответственный за дело',{exact:false}).selectOption(roles.editor.account.id);
      return mutation(ep,'/execution/instances',()=>ep.getByRole('button',{name:'Создать дело',exact:true}).click(),'POST',201);
    }
    async function openInstance(page,instance) {await work(page);if(page!==vp)await page.getByLabel('Все доступные дела',{exact:true}).check();await page.locator('.execution-cards').getByRole('button',{name:new RegExp(instance.name)}).click();await page.getByRole('heading',{name:instance.name,exact:true}).waitFor();}
    async function actionUI(instance,name,button) {return mutation(ep,`/execution/instances/${instance.id}/${name}`,()=>ep.locator('.execution-detail').getByRole('button',{name:button,exact:true}).click(),'POST',200);}
    async function openStep(instance) {return actionUI(instance,'open','Открыть текущий шаг');}
    async function checkAll(instance) {
      const checklist=ep.locator('.execution-current-step .execution-checklist'),checks=checklist.getByRole('checkbox');
      if(await checks.count()){for(const check of await checks.all())await check.check();return actionUI(instance,'checklist','Сохранить проверки');}return instance;
    }
    async function confirmResult(instance,resultDocument) {
      const formElement=ep.locator('form.execution-result-editor');
      if(resultDocument) {
        await formElement.getByLabel('Способ заполнения результата',{exact:false}).selectOption('document');await formElement.getByLabel('Поиск карточки результата',{exact:true}).fill(resultDocument.title);await formElement.getByLabel('Карточка результата',{exact:false}).selectOption(resultDocument.id);
        const attachments=formElement.getByRole('group',{name:'Документы результата',exact:true});await attachments.getByRole('button',{name:'+ Документ результата',exact:true}).click();await attachments.getByLabel('Поиск документа результата',{exact:true}).fill(resultDocument.title);await attachments.getByRole('combobox',{name:/^Документ результата(?:\s|$)/}).selectOption(resultDocument.id);await attachments.getByLabel('Версия: Документ результата',{exact:false}).selectOption(resultDocument.version_id);await attachments.getByLabel('Файл результата',{exact:false}).selectOption(resultDocument.files[0].id);await attachments.getByLabel(/^Пояснение результата/).fill('Синтетический оригинал подтверждённого результата Task009');await attachments.getByRole('button',{name:'Добавить документ результата',exact:true}).click();
      }
      await formElement.getByLabel(/^Комментарий к результату/).fill('Подтверждённый синтетический результат исполнителя');return actionUI(instance,'result','Подтвердить результат шага');
    }
    async function advance(instance,terminal=false) {return actionUI(instance,'advance',terminal?'Завершить дело':'Завершить шаг');}
    let completed=await startInstance(`${title} обычное завершённое дело`);completed=await openStep(completed);completed=await checkAll(completed);completed=await confirmResult(completed,resultStandard);
    await json(ep,`/execution/instances/${completed.id}/advance`,'POST',{revision:completed.revision,to_step:'urgent'},422);
    for(const page of [op,ap,vp])await json(page,`/execution/instances/${completed.id}/advance`,'POST',{revision:completed.revision},403);
    completed=await advance(completed);assert.equal(completed.current_step_key,'document');completed=await openStep(completed);completed=await checkAll(completed);completed=await confirmResult(completed);completed=await advance(completed);assert.equal(completed.current_step_key,'finish');completed=await openStep(completed);completed=await checkAll(completed);completed=await confirmResult(completed);completed=await advance(completed,true);assert.equal(completed.state,'completed');
    pass('assigned editor confirms real document answers checks requirements follows the server route and completes one instance while forged and non-assignee actions are denied');
    let overdue=await startInstance(`${title} срочное просроченное дело`);overdue=await openStep(overdue);overdue=await checkAll(overdue);overdue=await confirmResult(overdue,resultUrgent);overdue=await advance(overdue);assert.equal(overdue.current_step_key,'urgent');overdue=await openStep(overdue);
    await ep.getByRole('button',{name:'Приостановить дело',exact:true}).click();await ep.getByLabel(/^Причина действия/).fill('Синтетическая приостановка для проверки истории');overdue=await actionUI(overdue,'suspend','Подтвердить действие');assert.equal(overdue.state,'suspended');
    await ep.getByRole('button',{name:'Возобновить дело',exact:true}).click();await ep.getByLabel(/^Причина действия/).fill('Синтетическое возобновление');overdue=await actionUI(overdue,'resume','Подтвердить действие');
    await ep.getByRole('button',{name:'Изменить срок дела',exact:true}).click();
    const past=new Date(Date.now()-2*24*60*60*1000),pastLocal=new Date(past.getTime()-past.getTimezoneOffset()*60000).toISOString().slice(0,16);await ep.getByLabel('Новый срок дела',{exact:false}).fill(pastLocal);await ep.getByLabel(/^Причина действия/).fill('Синтетическая проверка реальной просрочки: явный прошлый срок, часы сервера не менялись');overdue=await actionUI(overdue,'deadline','Подтвердить действие');assert.equal(overdue.state,'active');assert.equal(overdue.overdue,true);
    await ep.screenshot({path:path.join(outputDir,'editor-overdue-viewport.png'),fullPage:false});
    pass('second instance takes the other confirmed route supports suspend resume and becomes overdue against real server time with a reasoned past deadline');
    const beforeFacts=executionFacts(await json(ep,`/execution/instances/${completed.id}`));
    const changedProcess=await json(ap,`/processes/${process.id}/versions`,'POST',{},201);
    const updatedSteps=steps.map(step=>({...step,...(step.key==='start'?{instruction:'НОВАЯ ИНСТРУКЦИЯ МОДЕЛИ НЕ ПЕРЕПИСЫВАЕТ ЗАПУЩЕННОЕ ДЕЛО'}:{})}));
    const savedChanged=await json(ap,`/process-versions/${changedProcess.id}`,'PUT',{revision:changedProcess.revision,effective_from:'2026-02-01',steps:updatedSteps,transitions:transitions.map(edge=>edge.key==='normal'?{...edge,to_step:'urgent'}:edge.key==='urgent_route'?{...edge,to_step:'document'}:edge)});
    await json(ap,`/process-versions/${changedProcess.id}/publish`,'POST',{revision:savedChanged.revision});
    const form2=await json(ap,`/document-types/${type.id}/versions`,'POST',{schema:{sections:[{key:'later',title:'Новая форма',fields:[{key:'title',label:'Новое наименование формы',type:'text',system:'title'}]}]}},201);await json(ap,`/form-schemas/${form2.id}/publish`,'POST',{});
    await ep.goto(`${origin}/work?document=${source.id}`);await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();const newerSource=await mutation(ep,`/documents/${source.id}/files`,async()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await fs.readFile(path.join(fixturesDir,'private.docx'))}),'POST',201);
    const sourceOptions=await json(ep,`/documents/${source.id}/relation-options`),latestFile=newerSource.files.reduce((latest,file)=>!latest||file.version>latest.version?file:latest,null);
    const templateDraft=await json(ep,`/knowledge/templates/${template.id}/versions`,'POST',{},201);
    const templateSaved=await json(ep,`/knowledge/template-versions/${templateDraft.id}`,'PUT',{revision:templateDraft.revision,name:template.name,purpose:'Следующая редакция шаблона не меняет старые дела',category:'',visibility:'registry',document_type_id:type.id,form_schema_id:form.id,original:{document_id:source.id,version_id:sourceOptions.versions[0].id,file_id:latestFile.id}});await json(ep,`/knowledge/template-versions/${templateDraft.id}/publish`,'POST',{revision:templateSaved.revision});
    assert.deepEqual(executionFacts(await json(ep,`/execution/instances/${completed.id}`)),beforeFacts);
    pass('later process rules form schema source original and template publications preserve the started instance snapshot confirmed answers and completed facts');
    await openInstance(vp,completed);await vp.locator('.execution-attempts').getByRole('button',{name:/^Проверить исходные сведения/}).click();
    const readerText=await vp.locator('.execution-detail').innerText();assert.equal(readerText.includes('СЕКРЕТНЫЙРЕЗУЛЬТАТTASK009'),false);assert.equal(readerText.includes('Скрытый результат Task009'),false);
    await vp.getByRole('heading',{name:'История дела',exact:true}).waitFor();
    const templatePanel=vp.locator('.execution-basis').filter({has:vp.getByRole('button',{name:'Шаблоны шага дела',exact:true})});await templatePanel.getByRole('button',{name:'Шаблоны шага дела',exact:true}).click();const templatePending=vp.waitForEvent('download');await templatePanel.getByRole('button',{name:'Скачать шаблон дела версии 1',exact:true}).click();const oldTemplate=await templatePending;assert.equal(await oldTemplate.failure(),null);const pinnedFile=path.join(outputDir,'instance-pinned-template-v1.docx');await oldTemplate.saveAs(pinnedFile);assert.deepEqual(await fs.readFile(pinnedFile),await fs.readFile(path.join(fixturesDir,'public.docx')));
    const resultPanel=vp.locator('.execution-basis').filter({has:vp.getByRole('button',{name:'Документы подтверждённого результата',exact:true})});await resultPanel.getByRole('button',{name:'Документы подтверждённого результата',exact:true}).click();const resultPending=vp.waitForEvent('download');await resultPanel.getByRole('button',{name:'Скачать оригинал дела версии 1',exact:true}).first().click();const resultDownload=await resultPending;assert.equal(await resultDownload.failure(),null);const resultFile=path.join(outputDir,'confirmed-result-original-v1.docx');await resultDownload.saveAs(resultFile);assert.deepEqual(await fs.readFile(resultFile),await fs.readFile(path.join(fixturesDir,'public.docx')));
    await vp.screenshot({path:path.join(outputDir,'reader-execution-result-viewport.png'),fullPage:false});await vp.setViewportSize({width:430,height:932});await vp.getByRole('heading',{name:'Прохождение шагов',exact:true}).scrollIntoViewIfNeeded();await vp.screenshot({path:path.join(outputDir,'reader-execution-narrow-viewport.png'),fullPage:false});await vp.setViewportSize({width:1440,height:1000});
    const readerHistory=await json(vp,`/execution/instances/${completed.id}/history?page_size=100&mode=viewer`);assert.ok(readerHistory.total>=6);assert.equal(JSON.stringify(readerHistory).includes('СЕКРЕТНЫЙРЕЗУЛЬТАТTASK009'),false);
    pass('reader sees authorized execution history and redacted result fields and downloads byte-checked original result and pinned template v1 after later versions');
    await openInstance(ep,overdue);await ep.getByRole('button',{name:'Назначить ответственного',exact:true}).click();await ep.getByLabel('Новый ответственный',{exact:false}).selectOption(roles.editor_other.account.id);overdue=await actionUI(overdue,'assign','Подтвердить действие');assert.equal(overdue.assignee_id,roles.editor_other.account.id);const delegated=await json(op,`/execution/instances/${overdue.id}`);assert.equal(delegated.permissions.work,true);
    await ep.getByRole('button',{name:'Назначить ответственного',exact:true}).click();await ep.getByLabel('Новый ответственный',{exact:false}).selectOption(roles.editor.account.id);overdue=await actionUI(overdue,'assign','Подтвердить действие');assert.equal(overdue.assignee_id,roles.editor.account.id);
    pass('creator assigns the active overdue instance to another authorized editor and restores the assignee through the UI');
    const sourceGrants=await json(ap,`/admin/access/${sourceRegistry.id}`);await json(ap,`/admin/access/${sourceRegistry.id}`,'PUT',{entries:sourceGrants.entries.filter(entry=>entry.user_id!==roles.viewer.account.id)});
    for(const instance of [completed,overdue]) {
      const closed=await json(vp,`/execution/instances/${instance.id}?mode=viewer`);assert.equal(closed.trajectory_restricted,true);assert.equal(closed.current_step_key,null);assert.deepEqual(closed.steps,[]);
      const hiddenHistory=await json(vp,`/execution/instances/${instance.id}/history?mode=viewer`);assert.equal(hiddenHistory.total,0);assert.deepEqual(hiddenHistory.items,[]);
      for(const value of [source.id,source.title,resultStandard.id,resultStandard.title,resultUrgent.id,resultUrgent.title,extraction.file_id,block.id,'СЕКРЕТНЫЙРЕЗУЛЬТАТTASK009'])assert.equal((JSON.stringify(closed)+JSON.stringify(hiddenHistory)).includes(value),false);
    }
    await openInstance(vp,completed);await vp.screenshot({path:path.join(outputDir,'reader-restricted-trajectory-viewport.png'),fullPage:false});
    await json(ap,`/admin/access/${sourceRegistry.id}`,'PUT',{entries:sourceGrants.entries});
    const scopeGrants=await json(ap,`/admin/access/${processRegistry.id}`);await json(ap,`/admin/access/${processRegistry.id}`,'PUT',{entries:scopeGrants.entries.filter(entry=>![roles.viewer.account.id,administrator.id].includes(entry.user_id))});
    for(const page of [vp,ap]) {const list=await json(page,`/execution/instances?registry_id=${processRegistry.id}`);assert.equal(list.total,0);assert.deepEqual(list.items,[]);for(const endpoint of [`/execution/instances/${completed.id}`,`/execution/instances/${completed.id}/history`])await json(page,endpoint,'GET',undefined,404);}
    await json(ap,`/admin/access/${processRegistry.id}`,'PUT',{entries:scopeGrants.entries});
    pass('live source revocation hides imported-result trajectory and history without branch or file leaks and registry revocation denies direct and counted access including administrator');
    const checkpoints=[];
    for(const instance of [completed,overdue]) {
      const detail=await json(ap,`/execution/instances/${instance.id}`),history=await allHistory(ap,instance.id,json);checkpoints.push({id:instance.id,name:instance.name,state:detail.state,process_version_id:detail.process_version_id,facts:executionFacts(detail),history_digest:digest(history.map(historyFact))});
    }
    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id));const previous=await json(ap,`/admin/access/${legacyRegistry.id}`);await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...previous.entries,...[roles.editor,roles.viewer].map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000009');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);assert.equal(legacyCard.reg_number,'000009');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000009',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();pass('ACTI v2 number nullable publication reference and volume remain compatible');
    for(const page of [ep,op,vp]){await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('editor alternate editor and reader forbidden administration login logout and no application errors');
    Object.assign(receipt,{status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{registry_id:processRegistry.id,source_registry_id:sourceRegistry.id,process_version_id:processV1.id,template_version_id:templateV1.id,instance_ids:[completed.id,overdue.id]},restart_checkpoints:checkpoints});return receipt;
  }catch(error){const failure={stage:h.getStage(),message:String(error.message),stack:String(error.stack||'')};await fs.writeFile(path.join(outputDir,'failure.txt'),sensitive.reduce((message,value)=>message.split(value).join('[redacted]'),JSON.stringify(failure,null,2)));console.error(`Task009 failed operation: ${failure.stage}`);let index=0;for(const context of h.contexts)for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`failure-page-${index++}.png`),fullPage:false}).catch(()=>{});throw error;
  }finally{const failures=[];let verified=false;if(ap)for(const registryId of adminRestore)try{const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:[...current.entries.filter(entry=>entry.user_id!==adminId),{user_id:adminId,permissions:['read','create','update','upload','download']}]});const check=await json(ap,`/admin/access/${registryId}`);assert.ok(check.entries.some(entry=>entry.user_id===adminId&&entry.permissions.includes('read')&&entry.permissions.includes('update')));}catch{failures.push('synthetic administrator scope restoration');}if(ap)for(const registryId of temporaryRegistries)try{const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:current.entries.filter(entry=>!accounts.includes(entry.user_id))});const check=await json(ap,`/admin/access/${registryId}`);assert.equal(check.entries.some(entry=>accounts.includes(entry.user_id)),false);}catch{failures.push('temporary registry grants');}if(ap)for(const id of accounts)try{await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch{failures.push('synthetic account deactivation');}if(ap&&accounts.length)try{const users=await json(ap,'/admin/users');assert.ok(accounts.every(id=>users.some(user=>user.id===id&&!user.active)));verified=true;}catch{failures.push('synthetic account verification');}receipt.cleanup_verified=verified&&failures.length===0;for(const context of h.contexts)await context.close();await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));await fs.writeFile(path.join(outputDir,'cleanup.json'),JSON.stringify({cleanup_verified:receipt.cleanup_verified,accounts_disabled:accounts.length,failures},null,2));assert.deepEqual(failures,[],'Synthetic cleanup must succeed');}
}

function digest(value){return createHash('sha256').update(JSON.stringify(value)).digest('hex');}
function historyFact(event){return {id:event.id,action:event.action,created_at:event.created_at,step_key:event.step_key??null,step_execution_id:event.step_execution_id??null};}
function executionFacts(instance){return {id:instance.id,state:instance.state,revision:instance.revision,registry_id:instance.registry_id,process_version_id:instance.process_version_id,current_step_key:instance.current_step_key,assignee_id:instance.assignee_id,deadline_at:instance.deadline_at,overdue:instance.overdue,snapshot_digest:digest(instance.process_snapshot),steps:instance.steps.map(step=>({id:step.id,step_key:step.step_key,attempt:step.attempt,state:step.state,assignee_id:step.assignee_id,started_at:step.started_at,completed_at:step.completed_at,deadline_at:step.deadline_at,checklist:step.checklist.map(check=>({id:check.id,checked:check.checked})),result:step.result?{id:step.result.id,created_at:step.result.created_at,values_digest:digest(step.result.values),attachments_digest:digest(step.result.attachments)}:null}))};}
async function allHistory(page,instanceId,json){const events=[];let pageNumber=1;while(true){const response=await json(page,`/execution/instances/${instanceId}/history?page_size=100&page=${pageNumber++}`);events.push(...response.items);if(events.length>=response.total)return events;assert.ok(response.items.length,'History pagination must progress');}}

export async function verifyTask009({browser,origin,admin,outputDir,previousReceiptFile}){
  const previous=JSON.parse(await fs.readFile(previousReceiptFile,'utf8'));assert.equal(previous.status,'PASS');assert.equal(previous.cleanup_verified,true);assert.equal(previous.restart_checkpoints.length,2);
  const h=await helpers(browser,origin,outputDir),page=await h.pageFor();
  try{await h.login(page,admin);const platform=await h.json(page,'/admin/platform');for(const checkpoint of previous.restart_checkpoints){const detail=await h.json(page,`/execution/instances/${checkpoint.id}`),history=await allHistory(page,checkpoint.id,h.json);assert.deepEqual(executionFacts(detail),checkpoint.facts);assert.equal(digest(history.map(historyFact)),checkpoint.history_digest);await page.goto(`${origin}/work`);await page.getByRole('button',{name:'Моя работа',exact:true}).first().click();await page.getByLabel('Все доступные дела',{exact:true}).check();await page.getByLabel('Реестр дел',{exact:false}).selectOption(previous.synthetic.registry_id);await page.locator('.execution-cards').getByRole('button',{name:new RegExp(checkpoint.name)}).click();await page.getByRole('heading',{name:checkpoint.name,exact:true}).waitFor();h.pass('restart preserved instance state attempts results immutable snapshot and complete history',{instance_id:checkpoint.id,state:detail.state});}await page.screenshot({path:path.join(outputDir,'restart-verified-viewport.png'),fullPage:false});await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await h.json(page,'/users/me','GET',undefined,401);assert.deepEqual(h.runtimeErrors,[]);return {status:'PASS',mode:'restart_verification',source_sha:platform.source_sha,scenarios:h.results,cleanup_verified:true};}finally{for(const context of h.contexts)await context.close();}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await(config.previousReceiptFile?verifyTask009:runTask009)({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task009 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`Task009 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
