/** Real HTTPS acceptance. Uses only owned synthetic records; no response mocking.
 * Private config: origin, adminCredentialsFile, playwrightModule, executablePath,
 * outputDir, expectedSha. Temporary users are disabled and grants removed.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomBytes} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export async function runDocumentWorkspace({browser, origin, admin, outputDir, expectedSha}) {
  const contexts=[], accounts=[], registries=[], checks=[], errors=[];
  let ap, registry, stage='setup';
  await fs.mkdir(outputDir,{recursive:true});
  async function pageFor() {
    const context=await browser.newContext({viewport:{width:1600,height:1050},ignoreHTTPSErrors:false});contexts.push(context);
    const page=await context.newPage();page.setDefaultTimeout(20000);
    page.on('pageerror',error=>errors.push(error.name));return page;
  }
  async function json(page, endpoint, method='GET', data, expected=200) {
    const response=await page.evaluate(async ({endpoint,method,data})=>{
      const me=method==='GET'?null:await (await fetch('/api/v1/users/me')).json();
      const result=await fetch('/api/v1'+endpoint,{method,headers:{Accept:'application/json',...(data===undefined?{}:{'Content-Type':'application/json'}),...(me?{'X-CSRF-Token':me.csrf_token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
      return {status:result.status,data:await result.json().catch(()=>null)};
    },{endpoint,method,data});
    assert.equal(response.status,expected,`${method} ${endpoint.split('?')[0]}`);return response.data;
  }
  async function login(page, credentials) {
    await page.goto(origin+'/work');
    await page.getByLabel('Логин',{exact:true}).fill(credentials.login);
    await page.getByLabel('Пароль',{exact:true}).fill(credentials.password);
    await page.getByRole('button',{name:'Войти в рабочее пространство',exact:true}).click();
    await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();
  }
  async function dismissNav(page) {
    await page.mouse.move(1100,100);await page.keyboard.press('Escape');
    await page.waitForFunction(()=>document.getElementById('main-navigation')?.inert);
  }
  const pass=name=>{checks.push(name);console.log('PASS '+name);};
  try {
    ap=await pageFor();await login(ap,admin);
    assert.equal((await json(ap,'/admin/platform')).source_sha,expectedSha);
    await json(ap,'/health/ready');pass('Strict HTTPS login and deployed revision');
    stage='owned fixtures';
    const code='layout_'+Date.now().toString(36), me=await json(ap,'/users/me');
    const type=await json(ap,'/document-types','POST',{code,name:'Проверка компоновки '+code},201);
    registry=await json(ap,'/registries','POST',{code,name:'Проверка документов '+code,document_type_ids:[type.id]},201);registries.push(registry);
    const version=(await json(ap,`/document-types/${type.id}/versions`))[0];
    const schema={sections:[{key:'main',title:'Реквизиты',fields:[
      {key:'caption',label:'Наименование LAYOUT',type:'text',system:'title',editable:true,required:true,searchable:true,filterable:true},
      {key:'number',label:'Номер LAYOUT',type:'text',system:'reg_number',editable:true,filterable:true},
      {key:'day',label:'Дата LAYOUT',type:'date',system:'reg_date',editable:true,filterable:true},
      {key:'custom_kind',label:'Классификация LAYOUT',type:'text',editable:true,filterable:true,exportable:true,read_roles:['editor']},
      {key:'custom_topic',label:'Вопрос LAYOUT',type:'text',editable:true,filterable:true,exportable:true},
      {key:'custom_owner',label:'Куратор LAYOUT',type:'user',editable:true},
      {key:'original',label:'Оригинал LAYOUT',type:'file',editable:true},
      ...Array.from({length:12},(_,i)=>({key:'extra'+i,label:'Сведения '+i,type:'textarea',editable:true})),
    ]}],list_view:{columns:[{key:'custom_kind',label:'Вид документа'},{key:'custom_topic',label:'Тема'},{key:'custom_owner',label:'Ответственный исполнитель'}]}};
    await json(ap,`/form-schemas/${version.id}`,'PUT',{schema});await json(ap,`/form-schemas/${version.id}/publish`,'POST',{});
    const roles={};
    for(const role of ['editor','viewer']) {
      const temporary=randomBytes(24).toString('base64url'), password=randomBytes(24).toString('base64url');
      const account=await json(ap,'/admin/users','POST',{login:code+'_'+role,display_name:'Сотрудник проверки '+role,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:code+'_'+role,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Документы',exact:true,level:1}).waitFor();roles[role]={page,account};
    }
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[{user_id:me.id,permissions:['read','create','update','upload','download']},...Object.entries(roles).map(([role,value])=>({user_id:value.account.id,permissions:role==='editor'?['read','create','update','upload','download']:['read']}))]});
    const ep=roles.editor.page,vp=roles.viewer.page,docs=[];
    for(let i=0;i<22;i++)docs.push(await json(ep,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,
      values:{caption:`Компоновка ${String(i+1).padStart(2,'0')}. Тестовый документ`,number:`LAYOUT-${i+1}`,day:'2026-10-16',custom_kind:'Закрытая классификация',custom_topic:'Организация работы',custom_owner:roles.editor.account.id,...Object.fromEntries(Array.from({length:12},(_,j)=>['extra'+j,'Синтетические сведения для проверки прокрутки.']))},
      ...(i===0?{files:[{field_key:'original',name:'layout.txt',mime:'text/plain',content_base64:Buffer.from('Synthetic layout acceptance document.').toString('base64')}]}:{})},201));
    for(const hidden of [false,true]) {
      const extra=await json(ap,'/registries','POST',{code:code+(hidden?'_hidden':'_extra'),name:'Проверка области поиска '+(hidden?'закрытый ':'доступный ')+code,document_type_ids:[type.id]},201);registries.push(extra);
      await json(ap,`/admin/access/${extra.id}`,'PUT',{entries:[{user_id:me.id,permissions:['read','create','update','download']},...(hidden?[]:Object.values(roles).map(value=>({user_id:value.account.id,permissions:['read']})))]});
      const probe=await json(ap,'/documents','POST',{registry_id:extra.id,document_type_id:type.id,schema_version_id:version.id,values:{caption:'Компоновка 00. '+(hidden?'Закрытая':'Доступная')+' запись',number:hidden?'HIDDEN-01':'EXTRA-01',day:'2026-10-16'}},201);
      for(let i=0;i<60;i++){if((await json(ap,`/documents/${probe.id}/search-index`)).ready)break;await delay(500);}
    }
    for(let i=0;i<60;i++){if((await json(ep,`/documents/${docs.at(-1).id}/search-index`)).ready)break;await delay(1000);}
    await ep.goto(`${origin}/work?registry_id=${registry.id}&document_type_id=${type.id}&sort=title&direction=asc`);await dismissNav(ep);
    const mode=name=>ep.getByRole('radio',{name,exact:true}),row=id=>ep.locator(`tr[data-document-id="${id}"]`);
    const list=ep.getByRole('region',{name:'Список документов',exact:true}),detail=ep.getByRole('region',{name:'Содержимое карточки',exact:true});
    await row(docs[0].id).waitFor();assert.equal(await mode('Вместе').getAttribute('aria-checked'),'true');
    await row(docs[0].id).click();await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();
    assert.equal(await ep.getByRole('button',{name:'Открыть карточку',exact:true}).count(),0);
    pass('Default split, click selection and removal of per-row open buttons');
    stage='collapsible search and compact toolbar';
    const searchPanel=ep.getByRole('group',{name:'Поиск документов',exact:true});
    const toolbar=ep.getByRole('group',{name:'Вид и сортировка документов',exact:true});
    const searchToggle=searchPanel.locator(':scope > summary');
    assert.equal(await toolbar.getByRole('radio').count(),3);assert((await toolbar.boundingBox()).height<=40);
    await searchPanel.getByText('Экспорт и импорт',{exact:true}).click();
    const downloading=ep.waitForEvent('download');await searchPanel.getByRole('button',{name:'Экспорт CSV',exact:true}).click();
    const csv=await downloading;assert.equal(await csv.failure(),null);await csv.saveAs(path.join(outputDir,'documents.csv'));assert((await fs.stat(path.join(outputDir,'documents.csv'))).size>20);
    const query=ep.getByRole('searchbox');await query.fill('Черновик запроса');
    await searchToggle.click();assert.equal(await query.isVisible(),false);assert((await searchPanel.boundingBox()).height<45);
    await ep.screenshot({path:path.join(outputDir,'collapsed-search.png'),fullPage:true,mask:[ep.locator('.account')]});
    await searchToggle.click();assert.equal(await query.inputValue(),'Черновик запроса');await query.fill('');
    await ep.getByRole('button',{name:'Все реестры',exact:true}).click();await ep.getByText('Найдено: 23',{exact:true}).waitFor();
    await ep.getByText('По всем доступным реестрам',{exact:true}).waitFor();assert.equal(await ep.getByText('Выберите реестр для просмотра документов.',{exact:true}).count(),0);
    const across=await json(ep,'/search','POST',{query:'Компоновка',registry_ids:[],document_type_ids:[type.id],sort:'title',direction:'asc',mode:'editor'});
    assert.equal(across.total,23);assert.equal(new Set(across.items.map(hit=>hit.document.registry_id)).size,2);
    assert(!across.items.some(hit=>hit.document.reg_number==='HIDDEN-01'));
    await ep.getByRole('button',{name:registry.name,exact:true}).click();
    await ep.getByLabel('Тип',{exact:true}).selectOption(type.id);await ep.getByRole('button',{name:'Найти',exact:true}).click();
    await ep.getByLabel('Сортировка',{exact:true}).selectOption('title');await ep.getByLabel('Направление сортировки',{exact:true}).selectOption('asc');
    await ep.getByText('Найдено: 22',{exact:true}).waitFor();
    pass('Flat toolbar, collapsed search keeps draft query; CSV downloads from grouped controls; all registries spans two allowed registries and excludes a third');
    stage='mouse resizing and responsive columns';
    const separator=ep.getByRole('separator',{name:'Размер списка документов и карточки',exact:true});
    const workspace=ep.locator('.document-workspace');
    async function dragTo(ratio, release=true) {
      await separator.scrollIntoViewIfNeeded();
      const bounds=await workspace.boundingBox(), grip=await separator.boundingBox();
      await ep.mouse.move(grip.x+grip.width/2,grip.y+grip.height/2);await ep.mouse.down();
      await ep.mouse.move(bounds.x+grip.width/2+(bounds.width-grip.width)*ratio,grip.y+grip.height/2,{steps:14});
      if(release)await ep.mouse.up();
    }
    assert.equal(await row(docs[0].id).getByRole('gridcell').count(),3);
    await row(docs[0].id).getByText('2026-10-16',{exact:true}).waitFor();
    await list.evaluate(el=>el.scrollTop=900);await delay(100);
    const deepScroll=await list.evaluate(el=>el.scrollTop);
    await dragTo(.79,false);await ep.keyboard.press('Escape');await ep.mouse.up();assert.equal(await list.evaluate(el=>el.scrollTop),deepScroll);
    await dragTo(.79,false);
    const deepBounds=await workspace.boundingBox(), deepGrip=await separator.boundingBox();
    await ep.mouse.move(deepBounds.x+deepGrip.width/2+(deepBounds.width-deepGrip.width)*.35,deepGrip.y+deepGrip.height/2,{steps:12});await ep.mouse.up();
    assert(Math.abs(await list.evaluate(el=>el.scrollTop)-deepScroll)<2);
    await list.evaluate(el=>el.scrollTop=0);
    const visible=[];
    for(const ratio of [.46,.57,.68,.79]){await dragTo(ratio);visible.push(await row(docs[0].id).getByRole('gridcell').count());}
    assert.deepEqual(visible,[4,5,6,7]);
    await workspace.screenshot({path:path.join(outputDir,'expanded-list.png')});
    await dragTo(.98);assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');
    await dragTo(.02);assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');
    await dragTo(.35);assert.equal(await mode('Вместе').getAttribute('aria-checked'),'true');
    const original=await separator.getAttribute('aria-valuenow');
    await dragTo(.7,false);await ep.keyboard.press('Escape');await ep.mouse.up();assert.equal(await separator.getAttribute('aria-valuenow'),original);
    await workspace.screenshot({path:path.join(outputDir,'compact-list.png')});
    pass('Mouse resizing reveals title/number/date then 4/5/6/7 columns; both edges snap modes; dragging restores split; Escape cancels');
    stage='draft and scroll';
    await list.evaluate(el=>el.scrollTop=180);await detail.evaluate(el=>el.scrollTop=240);await delay(100);
    const before=await ep.evaluate(()=>[document.querySelector('[aria-label="Список документов"]').scrollTop,document.querySelector('[aria-label="Содержимое карточки"]').scrollTop]);assert(before.every(x=>x>0));
    for(const name of ['Таблица','Карточка','Вместе'])await mode(name).click();
    assert.deepEqual(await ep.evaluate(()=>[document.querySelector('[aria-label="Список документов"]').scrollTop,document.querySelector('[aria-label="Содержимое карточки"]').scrollTop]),before);
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
    await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).fill('Компоновка 01. Сохранённый документ');
    await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).evaluate(el=>window.layoutInput=el);
    await dragTo(.7);await dragTo(.35);
    await searchToggle.click();await searchToggle.click();
    for(const name of ['Таблица','Карточка','Вместе'])await mode(name).click();
    assert.equal(await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).evaluate(el=>el===window.layoutInput),true);
    await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();await mode('Карточка').click();await mode('Вместе').click();assert((await ep.getByRole('button',{name:'Файлы и версии',exact:true}).getAttribute('class')).includes('active'));
    await ep.getByRole('button',{name:'Карточка',exact:true}).click();ep.once('dialog',dialog=>dialog.dismiss());await row(docs[1].id).click();
    assert.equal(await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).inputValue(),'Компоновка 01. Сохранённый документ');
    await mode('Карточка').click();
    const save=ep.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/documents/${docs[0].id}`&&r.request().method()==='PUT');
    await ep.getByRole('button',{name:'Сохранить карточку',exact:true}).click();assert.equal((await save).status(),200);
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();
    assert.equal((await json(ep,`/documents/${docs[0].id}`)).revision,2);
    pass('Independent scroll, mounted draft, tabs, cancelled navigation and real save');
    stage='history and files';
    await ep.getByRole('button',{name:'История изменений',exact:true}).click();await ep.getByText(/Редакция 2 ·/).waitFor();
    await ep.getByRole('button',{name:'Файлы и версии',exact:true}).click();await ep.getByText('layout.txt',{exact:false}).first().waitFor();
    await ep.getByRole('button',{name:'Карточка',exact:true}).click();
    await ep.getByRole('button',{name:'Показать список документов',exact:true}).click();await mode('Таблица').click();
    await ep.getByRole('button',{name:'Сортировать: Тема',exact:true}).waitFor();assert.equal(await ep.getByRole('columnheader').count(),7);
    const first=row(docs[0].id);await first.getByText('Сотрудник проверки editor',{exact:true}).waitFor();assert(!(await first.innerText()).includes(roles.editor.account.id));
    await ep.getByRole('button',{name:'Сортировать: Тема',exact:true}).click();assert.equal(await ep.getByRole('columnheader').filter({hasText:'Тема'}).getAttribute('aria-sort'),'ascending');
    await first.dblclick();assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');await ep.getByRole('button',{name:'К списку',exact:true}).click();assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');
    await first.focus();await ep.keyboard.press('Enter');await ep.getByRole('button',{name:'Показать список документов',exact:true}).click();await mode('Таблица').click();await ep.getByRole('button',{name:'Показать карточку документа',exact:true}).click();
    pass('Seven configured columns, resolved user, sorting, double-click, Enter, return and rails; history and file remain available');
    stage='search and pagination';
    await ep.getByRole('searchbox').fill('Компоновка');await ep.getByRole('button',{name:'Найти',exact:true}).click();await ep.getByText('Найдено: 22',{exact:true}).waitFor();
    await ep.getByRole('button',{name:'Вперёд',exact:true}).click();await ep.getByText('Страница 2',{exact:true}).waitFor();
    const url=ep.url();for(const name of ['Таблица','Карточка','Вместе'])await mode(name).click();assert.equal(ep.url(),url);assert.equal(await ep.getByRole('searchbox').inputValue(),'Компоновка');
    await ep.getByRole('button',{name:'Назад',exact:true}).click();
    await ep.waitForFunction(()=>document.querySelector('.document-list-pane')?.getAttribute('aria-busy')==='false' && document.querySelectorAll('tbody tr[data-document-id]').length===20);
    await dragTo(.55);const savedWidth=await separator.getAttribute('aria-valuenow');
    for(const name of ['Таблица','Вместе','Карточка']){await mode(name).click();await ep.locator('.document-detail-body').evaluate(el=>el.scrollTop=0);await ep.locator('.document-list-body .document-pane-scroll').evaluate(el=>el.scrollTop=0);await ep.screenshot({path:path.join(outputDir,name+'.png'),fullPage:true,mask:[ep.locator('.account')]});}
    await ep.reload();await dismissNav(ep);assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');
    await ep.getByRole('button',{name:'Показать список документов',exact:true}).click();assert.equal(await separator.getAttribute('aria-valuenow'),savedWidth);
    await ep.setViewportSize({width:390,height:844});for(const name of ['Таблица','Вместе','Карточка']){await mode(name).click();assert(await ep.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
    await mode('Вместе').click();assert.equal(await separator.getAttribute('aria-orientation'),'horizontal');await separator.focus();await ep.keyboard.press('End');assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');assert(await list.evaluate(el=>el.scrollWidth>el.clientWidth));await ep.screenshot({path:path.join(outputDir,'mobile.png'),fullPage:true,mask:[ep.locator('.account')]});
    pass('Real search, cursor page, preserved URL, persisted preference and mobile horizontal scrolling');
    stage='viewer ACL';await vp.goto(`${origin}/view?registry_id=${registry.id}&document_type_id=${type.id}&sort=title&direction=asc`);await dismissNav(vp);
    await vp.getByRole('radio',{name:'Таблица',exact:true}).click();const viewerRow=vp.locator(`tr[data-document-id="${docs[0].id}"]`);
    await viewerRow.getByText('Организация работы',{exact:true}).waitFor();assert.equal((await viewerRow.getByRole('gridcell').nth(4).innerText()).trim(),'—');assert(!(await viewerRow.innerText()).includes('Закрытая классификация'));
    await vp.getByRole('radio',{name:'Вместе',exact:true}).click();await viewerRow.click();await vp.getByRole('button',{name:'История изменений',exact:true}).waitFor();assert.equal(await vp.getByRole('button',{name:'Редактировать карточку',exact:true}).count(),0);
    await vp.getByRole('button',{name:'Файлы и версии',exact:true}).click();assert.equal(await vp.getByRole('button',{name:/Скачать/}).count(),0);
    assert.deepEqual(errors,[]);pass('Viewer ACL redacts field values and preserves read-only card and file restrictions; no runtime errors');
    return {status:'PASS',source_sha:expectedSha,checks,errors};
  } catch(error) {
    await fs.writeFile(path.join(outputDir,'failure.txt'),stage+'\n'+String(error.stack||error));throw error;
  } finally {
    if(ap){for(const owned of registries)await json(ap,`/admin/access/${owned.id}`,'PUT',{entries:[]});for(const id of accounts)await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}
    for(const context of contexts){for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`last-${contexts.indexOf(context)}.png`),fullPage:true,mask:[page.locator('.account')]}).catch(()=>{});await context.close();}
    await fs.writeFile(path.join(outputDir,'checks.json'),JSON.stringify({checks,stage,errors},null,2));
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runDocumentWorkspace({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Document workspace HTTPS PASS: ${receipt.checks.length} scenarios.`);}catch(error){console.error(`Document workspace FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
