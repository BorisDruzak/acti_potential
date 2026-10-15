/** Real Task008 HTTPS templates and previous-practice acceptance.
 * Same private config shape as Task005; own synthetic records only.
 * No response interception, external AI, execution instances or SQL Server writes.
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

export async function runTask008({browser,origin,admin,outputDir,fixturesDir}) {
  const h=await helpers(browser,origin,outputDir);
  const {pageFor,json,login,mutation,pass}=h;
  const suffix=Date.now().toString(36),code=`task008_${suffix}`,title=`Task008 ${suffix}`;
  const accounts=[],sensitive=[admin.password],temporaryRegistries=new Set(),receipt={};
  let ap;
  try {
    ap=await pageFor();await login(ap,admin);
    const platform=await json(ap,'/admin/platform'),administrator=await json(ap,'/users/me');
    pass('administrator strict HTTPS login and deployed source',{source_sha:platform.source_sha});
    const type=await json(ap,'/document-types','POST',{code,name:`${title} форма подготовки`},201);
    const form=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const formSchema={sections:[{key:'main',title:'Синтетическая подготовка документа',fields:[
      {key:'title',label:'Название Task008',type:'text',system:'title',required:true,editable:true,filterable:true,exportable:true},
      {key:'route',label:'Маршрут подготовки',type:'text',editable:true},
      {key:'public_file',label:'Оригинал Task008',type:'file',editable:true},
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
    const process=await json(ap,'/processes','POST',{registry_id:processRegistry.id,code:`${code}_process`,name:`${title} Подготовка документа`,purpose:'Синтетический контекст шаблонов и предыдущей практики'},201);
    const processDraft=(await json(ap,`/processes/${process.id}/versions`))[0];
    const processBody={revision:processDraft.revision,effective_from:'2026-01-01',steps:[
      {key:'prepare',title:'Подготовить документ',instruction:'Используйте разрешённый шаблон и подтверждённый пример предыдущей практики.',is_start:true,is_terminal:false,form_schema_id:form.id,sources:[{document_id:source.id,version_id:extraction.version_id,block_id:block.id,note:'Синтетическое основание подготовки'}],templates:[{document_id:source.id,version_id:extraction.version_id,file_id:extraction.file_id,note:'Прежний формат StepTemplate Task007'}]},
      {key:'complete',title:'Завершить подготовку',instruction:'Проверьте синтетический результат.',is_start:false,is_terminal:true},
    ],transitions:[{key:'next',from_step:'prepare',to_step:'complete',label:'Материалы подготовлены',condition:null,is_else:false}]};
    const savedProcess=await json(ap,`/process-versions/${processDraft.id}`,'PUT',processBody);
    const processV1=await json(ap,`/process-versions/${processDraft.id}/publish`,'POST',{revision:savedProcess.revision});
    const legacyTemplate=await json(vp,`/process-versions/${processV1.id}/steps/prepare/templates?mode=viewer`);assert.equal(legacyTemplate.items[0].files[0].id,extraction.file_id);
    pass('published process context retains Task007 original StepTemplate basis and source paragraph');
    async function catalog(page,kind) {
      await page.goto(`${origin}/${page===vp?'view':'work'}`);await page.getByRole('button',{name:kind==='templates'?'Шаблоны':'Как делали раньше',exact:true}).first().click();
      await page.getByRole('combobox',{name:/^Реестр знаний/}).selectOption(processRegistry.id);
    }
    async function targets(formElement,visibility='registry') {
      await formElement.getByRole('combobox',{name:/^Видимость знания/}).selectOption(visibility);
      await formElement.getByRole('combobox',{name:/^Тип документа знания/}).selectOption(type.id);
      await formElement.getByRole('combobox',{name:/^Форма знания/}).selectOption(form.id);
      await formElement.getByRole('combobox',{name:/^Процесс знания/}).selectOption(process.id);
      await formElement.getByRole('combobox',{name:/^Версия процесса знания/}).selectOption(processV1.id);
      await formElement.getByRole('combobox',{name:/^Шаг процесса знания/}).selectOption('prepare');
    }
    async function chooseOriginal(formElement,versionId,fileId) {
      const picker=formElement.getByRole('group',{name:'Оригинал шаблона',exact:true});
      await picker.getByLabel('Поиск документа шаблона',{exact:true}).fill(source.title);
      await picker.getByRole('combobox',{name:/^Документ шаблона/}).selectOption(source.id);
      await picker.getByLabel('Версия: Оригинал шаблона',{exact:false}).selectOption(versionId);
      await picker.getByLabel('Файл оригинала шаблона',{exact:false}).selectOption(fileId);
    }
    await catalog(ep,'templates');await ep.getByRole('button',{name:'Создать шаблон',exact:true}).click();
    const templateForm=ep.locator('form.knowledge-editor');
    await templateForm.getByLabel('Название шаблона',{exact:true}).fill(`${title} шаблон`);
    await templateForm.getByLabel(/^Назначение шаблона/).fill('Синтетический шаблон подготовки документа по проверенному примеру');
    await templateForm.getByLabel('Категория знания',{exact:true}).fill('Подготовка документа');await targets(templateForm);await chooseOriginal(templateForm,extraction.version_id,extraction.file_id);
    const template=await mutation(ep,'/knowledge/templates',()=>templateForm.getByRole('button',{name:'Сохранить шаблон',exact:true}).click(),'POST',201);
    const templateV1=await mutation(ep,`/knowledge/template-versions/${template.version.id}/publish`,()=>ep.getByRole('button',{name:'Опубликовать шаблон',exact:true}).click(),'POST',200);
    async function createCase(name,visibility) {
      await catalog(ep,'cases');await ep.getByRole('button',{name:'Создать случай',exact:true}).click();const formElement=ep.locator('form.knowledge-editor');
      await formElement.getByLabel('Название случая',{exact:true}).fill(name);
      await formElement.getByLabel(/^Описание выполненного случая/).fill('Синтетический случай: подготовили документ по исходному абзацу, проверили маршрут и результат.');
      await formElement.getByLabel(/^Результат случая/).fill('Синтетический проект подготовлен и проверен без утверждений о реальном регламенте.');
      await formElement.getByLabel('Категория знания',{exact:true}).fill('Подготовка документа');await targets(formElement,visibility);
      const sources=formElement.getByRole('group',{name:'Основания шага',exact:true});await sources.getByRole('button',{name:'+ Основание шага',exact:true}).click();
      await sources.getByLabel('Поиск документа основания',{exact:true}).fill(source.title);await sources.getByLabel('Документ основания',{exact:false}).selectOption(source.id);
      await sources.getByLabel('Версия: Основание шага',{exact:false}).selectOption(extraction.version_id);await sources.getByLabel('Оригинал: Основание шага',{exact:false}).selectOption(extraction.id);await sources.getByLabel('Фрагмент: Основание шага',{exact:false}).selectOption(block.id);
      await sources.getByLabel(/^Пояснение основания/).fill('Подтверждающий синтетический абзац Task008');await sources.getByRole('button',{name:'Добавить основание',exact:true}).click();
      const created=await mutation(ep,'/knowledge/cases',()=>formElement.getByRole('button',{name:'Сохранить случай',exact:true}).click(),'POST',201);
      return mutation(ep,`/knowledge/cases/${created.id}/publish`,()=>ep.getByRole('button',{name:'Опубликовать случай',exact:true}).click(),'POST',200);
    }
    const publicCase=await createCase(`${title} открытый случай`,'registry'),privateCase=await createCase(`${title} закрытый случай`,'private');
    pass('editor publishes a universal versioned template and two sourced synthetic practice cases through the UI');
    for(const page of [ep,vp,ap])await json(page,`/knowledge/cases/${publicCase.id}/curate`,'POST',{action:'confirm',reason:'Нет отдельного полномочия'},403);
    await json(vp,`/knowledge/cases/${privateCase.id}`,'GET',undefined,404);await json(ap,`/knowledge/cases/${privateCase.id}`,'GET',undefined,404);
    await ap.getByRole('button',{name:'Роли и доступ',exact:true}).click();await ap.getByRole('combobox',{name:/^Реестр\s/}).selectOption(processRegistry.id);
    for(const actor of [administrator,roles.editor.account])await ap.getByLabel(`${actor.display_name}: Подтверждение и рекомендация практики`,{exact:true}).check();
    await mutation(ap,`/admin/access/${processRegistry.id}`,()=>ap.getByRole('button',{name:'Сохранить права',exact:true}).click(),'PUT',200);
    await catalog(ep,'cases');await ep.locator('.knowledge-cards').getByRole('button',{name:new RegExp(publicCase.name)}).click();
    async function curate(action,button,reason) {
      const detail=ep.locator('.case-detail');await detail.getByRole('button',{name:button,exact:true}).click();await detail.getByLabel(/^Причина решения по практике/).fill(reason);
      const updated=await mutation(ep,`/knowledge/cases/${publicCase.id}/curate`,()=>detail.getByRole('button',{name:'Сохранить решение по практике',exact:true}).click(),'POST',200);return updated;
    }
    await curate('confirm','Подтвердить случай','Проверены синтетические исходные основания');await curate('recommend','Рекомендовать пример','Подходящий синтетический пример для подготовки документа');
    const removed=await curate('unrecommend','Снять рекомендацию','Синтетическая проверка сохранения истории при снятии рекомендации');assert.equal(removed.recommended,false);
    await ep.getByRole('button',{name:'История практики',exact:true}).click();await ep.getByText('Синтетическая проверка сохранения истории при снятии рекомендации',{exact:true}).waitFor();
    const caseHistory=await json(ep,`/knowledge/cases/${publicCase.id}/history`);assert.ok(caseHistory.items.some(event=>event.action==='unrecommended'||event.action==='unrecommend'));
    await curate('recommend','Рекомендовать пример','Повторная рекомендация синтетического примера');
    pass('administrator grants explicit curation editor confirms recommends and unpins with preserved reasoned history');
    const metricBaseline=await json(ap,`/knowledge/metrics?registry_id=${processRegistry.id}`);
    // A draft can use the universal object while the published Task007 snapshot stays intact.
    await ap.getByRole('button',{name:'Конструктор процессов',exact:true}).click();await ap.getByRole('combobox',{name:/^Процесс(?:\s|$)/}).selectOption(process.id);
    const nextProcess=await mutation(ap,`/processes/${process.id}/versions`,()=>ap.getByRole('button',{name:'Новая версия процесса',exact:true}).click(),'POST',201);
    const universal=ap.getByRole('group',{name:'Версии универсальных шаблонов',exact:true});
    await universal.getByLabel('Универсальный шаблон',{exact:false}).selectOption(template.id);await universal.getByLabel('Закреплённая версия шаблона',{exact:false}).selectOption(templateV1.id);await universal.getByRole('button',{name:'Закрепить версию шаблона',exact:true}).click();
    await mutation(ap,`/process-versions/${nextProcess.id}`,()=>ap.getByRole('button',{name:'Сохранить черновик процесса',exact:true}).click(),'PUT',200);
    await mutation(ap,`/process-versions/${nextProcess.id}/validate`,()=>ap.getByRole('button',{name:'Проверить процесс',exact:true}).click(),'POST',200);
    await mutation(ap,`/process-versions/${nextProcess.id}/publish`,()=>ap.getByRole('button',{name:'Опубликовать процесс',exact:true}).click(),'POST',200);
    const oldProcess=await json(ap,`/process-versions/${processV1.id}`);assert.deepEqual(oldProcess.steps,processV1.steps);assert.deepEqual(oldProcess.transitions,processV1.transitions);
    const bound=await json(ap,`/process-versions/${nextProcess.id}`);assert.ok(bound.steps[0].template_versions.some(item=>item.template_version_id===templateV1.id));
    pass('administrator publishes process v2 with a pinned universal template while prior Task007 sources and original StepTemplate stay unchanged');
    // The next original has different bytes and is processed by the real worker.
    await ep.goto(`${origin}/work?document=${source.id}`);await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();
    const uploaded=await mutation(ep,`/documents/${source.id}/files`,async()=>ep.getByLabel('Добавить файл или следующую версию',{exact:false}).setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await fs.readFile(path.join(fixturesDir,'private.docx'))}),'POST',201);
    const fileV2=uploaded.files.reduce((latest,file)=>!latest||file.version>latest.version?file:latest,null);let extractionV2;
    const nextDeadline=Date.now()+120000;
    while(Date.now()<nextDeadline){extractionV2=(await json(ep,`/documents/${source.id}/content`)).items.find(item=>item.file_id===fileV2.id);if(extractionV2?.state==='completed')break;if(extractionV2&&['failed','unsupported','encrypted','no_text'].includes(extractionV2.state))throw new Error(`Second synthetic original ${extractionV2.state}`);await delay(1000);}
    assert.equal(extractionV2?.state,'completed');
    await catalog(ep,'templates');await ep.locator('.knowledge-cards').getByRole('button',{name:new RegExp(template.name)}).click();
    const templateDraft=await mutation(ep,`/knowledge/templates/${template.id}/versions`,()=>ep.getByRole('button',{name:'Новая версия шаблона',exact:true}).click(),'POST',201);
    const editTemplate=ep.locator('form.knowledge-editor');await editTemplate.getByLabel(/^Назначение шаблона/).fill('Синтетический шаблон редакции 2 с другим оригиналом');await chooseOriginal(editTemplate,extractionV2.version_id,fileV2.id);
    await mutation(ep,`/knowledge/template-versions/${templateDraft.id}`,()=>editTemplate.getByRole('button',{name:'Сохранить шаблон',exact:true}).click(),'PUT',200);
    const templateV2=await mutation(ep,`/knowledge/template-versions/${templateDraft.id}/publish`,()=>ep.getByRole('button',{name:'Опубликовать шаблон',exact:true}).click(),'POST',200);
    assert.equal(templateV2.version,2);const pinnedV1=await json(vp,`/knowledge/template-versions/${templateV1.id}?mode=viewer`);assert.equal(pinnedV1.original.file_id,extraction.file_id);
    pass('editor publishes template v2 from different extracted bytes while independent v1 keeps its exact original');
    async function openProcess(page,versionId=processV1.id) {
      await page.goto(`${origin}/${page===vp?'view':'work'}`);await page.getByRole('button',{name:'Процессы',exact:true}).first().click();await page.getByRole('combobox',{name:/^Реестр процессов/}).selectOption(processRegistry.id);await page.getByRole('combobox',{name:/^Процесс(?:\s|$)/}).selectOption(process.id);await page.getByRole('combobox',{name:/^Версия процесса/}).selectOption(versionId);await page.getByLabel('Шаг процесса',{exact:false}).selectOption('prepare');await page.locator('.related-knowledge').waitFor();
    }
    await openProcess(vp,nextProcess.id);const pinnedPanel=vp.locator('.process-basis').filter({has:vp.getByRole('button',{name:'Шаблоны и образцы шага',exact:true})});await pinnedPanel.getByRole('button',{name:'Шаблоны и образцы шага',exact:true}).click();
    const pinnedPending=vp.waitForEvent('download');await pinnedPanel.getByRole('button',{name:'Скачать закреплённый шаблон версии 1',exact:true}).click();const pinnedDownload=await pinnedPending;assert.equal(await pinnedDownload.failure(),null);const pinnedPath=path.join(outputDir,'published-process-pinned-template-v1.docx');await pinnedDownload.saveAs(pinnedPath);assert.deepEqual(await fs.readFile(pinnedPath),await fs.readFile(path.join(fixturesDir,'public.docx')));
    pass('reader downloads the universal template pinned at v1 from published process v2 after template v2 exists');
    await openProcess(vp);const context=vp.locator('.related-knowledge');
    await context.getByRole('button',{name:'Шаблоны',exact:true}).click();await context.getByLabel('Тип связанных знаний',{exact:false}).selectOption(type.id);
    await context.locator('.knowledge-cards').getByRole('button',{name:new RegExp(template.name)}).click();await context.getByLabel('Версия шаблона',{exact:false}).selectOption(templateV1.id);
    const downloadPending=vp.waitForEvent('download');await context.getByRole('button',{name:'Скачать шаблон версии 1',exact:true}).click();const oldDownload=await downloadPending;assert.equal(await oldDownload.failure(),null);const savedOld=path.join(outputDir,'template-original-v1.docx');await oldDownload.saveAs(savedOld);assert.deepEqual(await fs.readFile(savedOld),await fs.readFile(path.join(fixturesDir,'public.docx')));
    await context.getByLabel('Версия шаблона',{exact:false}).selectOption(templateV2.id);const secondPending=vp.waitForEvent('download');await context.getByRole('button',{name:'Скачать шаблон версии 2',exact:true}).click();const secondDownload=await secondPending;assert.equal(await secondDownload.failure(),null);const savedSecond=path.join(outputDir,'template-original-v2.docx');await secondDownload.saveAs(savedSecond);assert.deepEqual(await fs.readFile(savedSecond),await fs.readFile(path.join(fixturesDir,'private.docx')));assert.notDeepEqual(await fs.readFile(savedSecond),await fs.readFile(savedOld));
    await context.getByRole('button',{name:'Как делали раньше',exact:true}).click();assert.equal((await context.innerText()).includes(privateCase.name),false);await context.locator('.knowledge-cards').getByRole('button',{name:new RegExp(publicCase.name)}).click();
    await context.getByRole('button',{name:'Проверить основания знания',exact:true}).click();await context.locator('blockquote.content-text').waitFor();assert.ok((await context.locator('blockquote.content-text').innerText()).includes(block.text));
    await vp.screenshot({path:path.join(outputDir,'reader-practice-basis-viewport.png'),fullPage:false});
    await context.getByRole('link',{name:'Открыть документ знания',exact:true}).click();await vp.locator('.content-block.selected').waitFor();assert.ok((await vp.locator('.content-block.selected').innerText()).includes(block.text));
    await openProcess(vp);await vp.locator('.related-knowledge').getByRole('button',{name:'Похожие случаи',exact:true}).click();await vp.locator('.related-knowledge .knowledge-cards').getByRole('button',{name:new RegExp(publicCase.name)}).waitFor();
    await vp.setViewportSize({width:430,height:932});await vp.locator('.related-knowledge h3').scrollIntoViewIfNeeded();await vp.screenshot({path:path.join(outputDir,'reader-knowledge-narrow-viewport.png'),fullPage:false});await vp.setViewportSize({width:1440,height:1000});
    pass('reader uses all three process-context tabs type matching case basis and exact byte-checked old and current template downloads');
    await openProcess(vp);const oldTemplates=vp.locator('.process-basis').filter({has:vp.getByRole('button',{name:'Шаблоны и образцы шага',exact:true})});await oldTemplates.getByRole('button',{name:'Шаблоны и образцы шага',exact:true}).click();
    const legacyPending=vp.waitForEvent('download');await oldTemplates.getByRole('button',{name:'Скачать оригинал основания версии 1',exact:true}).click();const legacyDownload=await legacyPending;assert.equal(await legacyDownload.failure(),null);const legacyPath=path.join(outputDir,'legacy-step-template-v1.docx');await legacyDownload.saveAs(legacyPath);assert.deepEqual(await fs.readFile(legacyPath),await fs.readFile(path.join(fixturesDir,'public.docx')));
    await catalog(vp,'templates');await vp.getByLabel('Фильтр типа знания',{exact:false}).selectOption(type.id);await vp.getByLabel('Фильтр категории знания',{exact:true}).fill('Подготовка документа');await vp.getByLabel('Поиск знаний',{exact:true}).fill('ЗАКРЫТЫЙФАЙЛМАРКЕР');await vp.getByText('Найдено: 1 · страница 1',{exact:true}).waitFor();
    await vp.locator('.knowledge-cards').getByRole('button',{name:new RegExp(template.name)}).waitFor();
    await catalog(vp,'cases');await vp.getByLabel('Фильтр типа знания',{exact:false}).selectOption(type.id);await vp.getByLabel('Фильтр категории знания',{exact:true}).fill('Подготовка документа');await vp.getByLabel('Поиск знаний',{exact:true}).fill('подготовили');await vp.getByText('Найдено: 1 · страница 1',{exact:true}).waitFor();assert.equal((await vp.locator('.knowledge-catalog').innerText()).includes(privateCase.name),false);
    const visibleCases=await json(vp,`/knowledge/cases?registry_id=${processRegistry.id}&text=${encodeURIComponent(title)}`);assert.equal(visibleCases.total,1);assert.equal(visibleCases.items[0].id,publicCase.id);
    for(const endpoint of [`/knowledge/cases/${privateCase.id}`,`/knowledge/cases/${privateCase.id}/basis`,`/knowledge/cases/${privateCase.id}/history`])await json(vp,endpoint,'GET',undefined,404);
    await json(vp,`/knowledge/cases/${publicCase.id}/curate`,'POST',{action:'recommend',reason:'Forbidden reader recommendation'},403);
    await json(vp,`/knowledge/templates/${template.id}/versions`,'POST',{},403);
    pass('deterministic type category practice text and extracted template content searches expose only permitted knowledge without AI');
    const afterMetrics=await json(ap,`/knowledge/metrics?registry_id=${processRegistry.id}`),beforeCounts=new Map(metricBaseline.items.map(item=>[item.action,item.count]));
    for(const action of ['template_open','case_open','basis_follow','search','template_download'])assert.ok((afterMetrics.items.find(item=>item.action===action)?.count||0)>(beforeCounts.get(action)||0),`Actual usage metric increased: ${action}`);
    for(const item of afterMetrics.items)assert.deepEqual(Object.keys(item).sort(),['action','count']);
    pass('actual template case basis search and download usage increments aggregate action counters without queries names or personal fields');
    // Revoking source download immediately removes content evidence, not public knowledge metadata.
    const sourceGrants=await json(ap,`/admin/access/${sourceRegistry.id}`);
    await json(ap,`/admin/access/${sourceRegistry.id}`,'PUT',{entries:sourceGrants.entries.map(entry=>entry.user_id===roles.viewer.account.id?{...entry,permissions:['read']}:entry)});
    const restricted=await json(vp,`/knowledge/template-versions/${templateV2.id}?mode=viewer`);assert.equal(restricted.original.restricted,true);
    await json(vp,`/knowledge/template-versions/${templateV2.id}/download?mode=viewer`,'GET',undefined,404);
    assert.equal((await json(vp,`/knowledge/templates?registry_id=${processRegistry.id}&text=${encodeURIComponent('ЗАКРЫТЫЙФАЙЛМАРКЕР')}`)).total,0);
    assert.equal((await json(vp,`/knowledge/cases?registry_id=${processRegistry.id}&text=${encodeURIComponent('подготовили')}`)).total,1);
    const restrictedCase=await json(vp,`/knowledge/cases/${publicCase.id}`),restrictedBasis=await json(vp,`/knowledge/cases/${publicCase.id}/basis?mode=viewer`);
    assert.equal(restrictedCase.links[0].restricted,true);assert.equal(restrictedBasis.items[0].restricted,true);
    for(const value of [source.id,source.title,extraction.file_id,block.id,block.text,'Подтверждающий синтетический абзац Task008'])assert.equal((JSON.stringify(restricted)+JSON.stringify(restrictedCase)+JSON.stringify(restrictedBasis)).includes(value),false);
    assert.equal((await json(vp,`/knowledge/templates?registry_id=${processRegistry.id}&text=${encodeURIComponent(template.name)}`)).total,1);
    await json(ap,`/admin/access/${sourceRegistry.id}`,'PUT',{entries:sourceGrants.entries});
    const objectGrants=await json(ap,`/admin/access/${processRegistry.id}`);
    await json(ap,`/admin/access/${processRegistry.id}`,'PUT',{entries:objectGrants.entries.map(entry=>entry.user_id===roles.viewer.account.id?{...entry,permissions:['read']}:entry)});
    await json(vp,`/knowledge/template-versions/${templateV2.id}/download`,'GET',undefined,403);assert.equal((await json(vp,`/knowledge/templates?registry_id=${processRegistry.id}&text=${encodeURIComponent('ЗАКРЫТЫЙФАЙЛМАРКЕР')}`)).total,0);assert.equal((await json(vp,`/knowledge/templates/${template.id}`)).id,template.id);
    await json(ap,`/admin/access/${processRegistry.id}`,'PUT',{entries:objectGrants.entries.filter(entry=>entry.user_id!==administrator.id)});
    for(const endpoint of [`/knowledge/templates/${template.id}`,`/knowledge/template-versions/${templateV1.id}`,`/knowledge/cases/${publicCase.id}`])await json(ap,endpoint,'GET',undefined,404);
    pass('source and object download revocation hide template content and linked evidence while the permitted case description remains searchable');
    const legacyType=(await json(ap,'/document-types')).find(value=>value.code==='acti-v2'),legacyRegistry=(await json(ap,'/registries')).find(value=>value.document_type_ids.includes(legacyType.id));
    const previous=await json(ap,`/admin/access/${legacyRegistry.id}`);await json(ap,`/admin/access/${legacyRegistry.id}`,'PUT',{entries:[...previous.entries,...Object.values(roles).map(value=>({user_id:value.account.id,permissions:value.permissions}))]});temporaryRegistries.add(legacyRegistry.id);
    await ep.goto(`${origin}/work`);await ep.getByRole('button',{name:legacyRegistry.name,exact:true}).click();await ep.getByRole('button',{name:'Создать документ',exact:true}).click();await ep.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(legacyType.id);await ep.getByLabel('Наименование документа',{exact:false}).fill(`${title} legacy smoke`);await ep.getByLabel('Регистрационный номер',{exact:true}).fill('000008');await ep.getByRole('combobox',{name:'Тип документа ACTI v2',exact:true}).selectOption({label:'Постановление'});await ep.getByLabel('Номер тома',{exact:true}).fill('1-2');
    const legacyCard=await mutation(ep,'/documents',()=>ep.getByRole('button',{name:'Создать документ',exact:true}).click(),'POST',201);assert.equal(legacyCard.reg_number,'000008');assert.equal(legacyCard.values.should_publish??null,null);assert.equal(legacyCard.values.volume_number,'1-2');assert.match(legacyCard.values.type_id,/^[0-9a-f-]{36}$/i);
    await vp.goto(`${origin}/view?document=${legacyCard.id}`);await vp.getByText('000008',{exact:true}).first().waitFor();await vp.getByText('Постановление',{exact:true}).first().waitFor();await vp.getByText('1-2',{exact:true}).first().waitFor();
    pass('ACTI v2 number nullable publication reference and volume remain compatible');
    for(const page of [ep,vp]){await page.goto(`${origin}/admin`);await page.waitForURL(url=>url.pathname!='/admin');await page.getByRole('button',{name:'Выйти',exact:true}).click();await page.getByRole('heading',{name:'Вход в ACTI',exact:true}).waitFor();await json(page,'/users/me','GET',undefined,401);}
    assert.deepEqual(h.runtimeErrors,[]);pass('editor reader forbidden administration login logout and no application errors');
    Object.assign(receipt,{status:'PASS',source_sha:platform.source_sha,scenarios:h.results,synthetic:{template_id:template.id,template_version_ids:[templateV1.id,templateV2.id],case_ids:[publicCase.id,privateCase.id],process_version_id:processV1.id,registry_id:processRegistry.id},usage_metrics:afterMetrics.items});return receipt;
  }catch(error){const failure={stage:h.getStage(),message:String(error.message),stack:String(error.stack||'')};await fs.writeFile(path.join(outputDir,'failure.txt'),sensitive.reduce((message,value)=>message.split(value).join('[redacted]'),JSON.stringify(failure,null,2)));console.error(`Task008 failed operation: ${failure.stage}`);let index=0;for(const context of h.contexts)for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`failure-page-${index++}.png`),fullPage:false}).catch(()=>{});throw error;
  }finally{const failures=[];let verified=false;if(ap)for(const registryId of temporaryRegistries)try{const current=await json(ap,`/admin/access/${registryId}`);await json(ap,`/admin/access/${registryId}`,'PUT',{entries:current.entries.filter(entry=>!accounts.includes(entry.user_id))});const check=await json(ap,`/admin/access/${registryId}`);assert.equal(check.entries.some(entry=>accounts.includes(entry.user_id)),false);}catch{failures.push('temporary registry grants');}if(ap)for(const id of accounts)try{await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}catch{failures.push('synthetic account deactivation');}if(ap&&accounts.length)try{const users=await json(ap,'/admin/users');assert.ok(accounts.every(id=>users.some(user=>user.id===id&&!user.active)));verified=true;}catch{failures.push('synthetic account verification');}receipt.cleanup_verified=verified&&failures.length===0;for(const context of h.contexts)await context.close();await fs.writeFile(path.join(outputDir,'scenarios.json'),JSON.stringify(h.results,null,2));await fs.writeFile(path.join(outputDir,'cleanup.json'),JSON.stringify({cleanup_verified:receipt.cleanup_verified,accounts_disabled:accounts.length,failures},null,2));assert.deepEqual(failures,[],'Synthetic cleanup must succeed');}
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runTask008({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Task008 actual HTTPS browser PASS: ${receipt.scenarios.length} scenarios.`);}catch(error){console.error(`Task008 browser FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
