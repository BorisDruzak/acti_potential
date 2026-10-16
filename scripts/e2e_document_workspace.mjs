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
  assert.equal(new URL(origin).protocol,'https:','Acceptance requires strict HTTPS');
  const contexts=[], accounts=[], registries=[], documentAcls=[], checks=[], errors=[];
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
    // Wait for the React workspace, then click blank main padding to close the overlay.
    // Moving the pointer before mount may not emit pointerenter; Escape requires sidebar focus.
    await page.getByRole('main').click({position:{x:1000,y:10}});
    await page.waitForFunction(()=>document.getElementById('main-navigation')?.inert);
  }
  async function fillFeed(page,expected) {
    const pane=page.getByRole('region',{name:'Список документов',exact:true});
    for(let attempt=0;attempt<40;attempt++) {
      const count=await pane.locator('tr[data-document-id]').count();
      if(count===expected)return;
      assert(count<expected,'Feed must not append duplicate rows');
      await pane.evaluate(el=>el.scrollTop=el.scrollHeight);
      await delay(250);
    }
    assert.equal(await pane.locator('tr[data-document-id]').count(),expected,'Expected all cursor batches');
  }
  async function indexed(page,id) {
    for(let attempt=0;attempt<60;attempt++) {
      if((await json(page,`/documents/${id}/search-index`)).ready)return;
      await delay(500);
    }
    assert.fail('Owned document did not finish indexing');
  }
  // A complete tiny PDF generated solely from synthetic text; no source documents.
  function syntheticPdf() {
    const stream='BT /F1 14 Tf 40 140 Td (Synthetic lifecycle acceptance original.) Tj ET';
    const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`];
    let pdf='%PDF-1.4\n';const offsets=[0];
    for(const [index,object] of objects.entries()){offsets.push(Buffer.byteLength(pdf));pdf+=`${index+1} 0 obj\n${object}\nendobj\n`;}
    const xref=Buffer.byteLength(pdf);
    pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(offset=>`${String(offset).padStart(10,'0')} 00000 n \n`).join('');
    pdf+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return Buffer.from(pdf);
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
      {key:'caption',label:'Наименование LAYOUT',type:'text',system:'title',editable:true,required:true,searchable:true,filterable:true,exportable:true},
      {key:'number',label:'Номер LAYOUT',type:'text',system:'reg_number',editable:true,filterable:true},
      {key:'day',label:'Дата LAYOUT',type:'date',system:'reg_date',editable:true,filterable:true},
      {key:'custom_kind',label:'Классификация LAYOUT',type:'text',editable:true,filterable:true,exportable:true,read_roles:['editor']},
      {key:'custom_topic',label:'Вопрос LAYOUT',type:'text',editable:true,filterable:true,exportable:true},
      {key:'custom_owner',label:'Куратор LAYOUT',type:'user',editable:true},
      {key:'original',label:'Оригинал LAYOUT',type:'file',editable:true},
      ...Array.from({length:12},(_,i)=>({key:'extra'+i,label:'Сведения '+i,type:'textarea',editable:true})),
    ]}],list_view:{columns:[{key:'custom_kind',label:'Вид документа'},{key:'custom_topic',label:'Тема'},{key:'custom_owner',label:'Ответственный исполнитель'}]}};
    const fields=schema.sections[0].fields;
    schema.sections=[{key:'main',title:'Основные сведения',fields:fields.slice(0,7)},
      ...['Публикация','Контроль исполнения','Делопроизводство','Подписанты и согласующие','Оригиналы и вложения'].map((title,index)=>({key:'section'+index,title,fields:fields.slice(7+index*2,index===4?undefined:9+index*2)}))];
    schema.sections[0].fields.push({key:'legal_state',label:'Статус правового акта LAYOUT',type:'text',system:'status',editable:true});
    await json(ap,`/form-schemas/${version.id}`,'PUT',{schema});await json(ap,`/form-schemas/${version.id}/publish`,'POST',{});
    const roles={};
    for(const role of ['editor','viewer','user']) {
      const temporary=randomBytes(24).toString('base64url'), password=randomBytes(24).toString('base64url');
      const account=await json(ap,'/admin/users','POST',{login:code+'_'+role,display_name:'Сотрудник проверки '+role,password:temporary,roles:[role]},201);accounts.push(account.id);
      const page=await pageFor();await login(page,{login:code+'_'+role,password:temporary});
      await page.getByLabel('Текущий пароль',{exact:true}).fill(temporary);await page.getByLabel('Новый пароль',{exact:true}).fill(password);await page.getByLabel('Повторите новый пароль',{exact:true}).fill(password);
      await page.getByRole('button',{name:'Изменить пароль',exact:true}).click();await page.getByRole('heading',{name:'Документы',exact:true,level:1}).waitFor();roles[role]={page,account};
    }
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[{user_id:me.id,permissions:['read','create','update','upload','download']},...Object.entries(roles).map(([role,value])=>({user_id:value.account.id,permissions:role==='editor'?['read','create','update','upload','download']:['read']}))]});
    const ep=roles.editor.page,vp=roles.viewer.page,up=roles.user.page,docs=[];
    for(let i=0;i<22;i++)docs.push(await json(ep,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,card_state:'published',publication_targets:['internal','shared'],
      values:{caption:`Компоновка ${String(i+1).padStart(2,'0')}. Тестовый документ`,number:`LAYOUT-${i+1}`,day:'2026-10-16',custom_kind:'Закрытая классификация',custom_topic:'Организация работы',custom_owner:roles.editor.account.id,...Object.fromEntries(Array.from({length:12},(_,j)=>['extra'+j,'Синтетические сведения для проверки прокрутки.']))},
      ...(i===0?{files:[{field_key:'original',name:'layout.txt',mime:'text/plain',content_base64:Buffer.from('Synthetic layout acceptance document.').toString('base64')}]}:{})},201));
    for(const hidden of [false,true]) {
      const extra=await json(ap,'/registries','POST',{code:code+(hidden?'_hidden':'_extra'),name:'Проверка области поиска '+(hidden?'закрытый ':'доступный ')+code,document_type_ids:[type.id]},201);registries.push(extra);
      await json(ap,`/admin/access/${extra.id}`,'PUT',{entries:[{user_id:me.id,permissions:['read','create','update','download']},...(hidden?[]:Object.values(roles).map(value=>({user_id:value.account.id,permissions:['read']})))]});
      const probe=await json(ap,'/documents','POST',{registry_id:extra.id,document_type_id:type.id,schema_version_id:version.id,card_state:'published',publication_targets:['internal','shared'],values:{caption:'Компоновка 00. '+(hidden?'Закрытая':'Доступная')+' запись',number:hidden?'HIDDEN-01':'EXTRA-01',day:'2026-10-16'}},201);
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
    await ep.getByRole('combobox',{name:'Реестр',exact:true}).selectOption('');await ep.getByText('Найдено: 23',{exact:true}).waitFor();
    await ep.getByText('По всем доступным реестрам',{exact:true}).waitFor();assert.equal(await ep.getByText('Выберите реестр для просмотра документов.',{exact:true}).count(),0);
    const across=await json(ep,'/search','POST',{query:'Компоновка',registry_ids:[],document_type_ids:[type.id],sort:'title',direction:'asc',mode:'editor'});
    assert.equal(across.total,23);assert.equal(new Set(across.items.map(hit=>hit.document.registry_id)).size,2);
    assert(!across.items.some(hit=>hit.document.reg_number==='HIDDEN-01'));
    await ep.getByRole('combobox',{name:'Реестр',exact:true}).selectOption(registry.id);
    await ep.getByRole('combobox',{name:'Тип',exact:true}).selectOption(type.id);await ep.getByRole('button',{name:'Найти',exact:true}).click();
    await ep.getByRole('combobox',{name:'Сортировка',exact:true}).selectOption('title');await ep.getByRole('combobox',{name:'Направление сортировки',exact:true}).selectOption('asc');
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
    assert.equal(await row(docs[0].id).getByRole('gridcell').count(),4);
    await row(docs[0].id).getByText('2026-10-16',{exact:true}).waitFor();
    await row(docs[0].id).getByRole('gridcell',{name:'Состояние карточки: Опубликован',exact:true}).waitFor();
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
    assert.deepEqual(visible,[5,6,7,8]);
    await workspace.screenshot({path:path.join(outputDir,'expanded-list.png')});
    await dragTo(.98);assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');
    await dragTo(.02);assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');
    await dragTo(.35);assert.equal(await mode('Вместе').getAttribute('aria-checked'),'true');
    const original=await separator.getAttribute('aria-valuenow');
    await dragTo(.7,false);await ep.keyboard.press('Escape');await ep.mouse.up();assert.equal(await separator.getAttribute('aria-valuenow'),original);
    await workspace.screenshot({path:path.join(outputDir,'compact-list.png')});
    pass('Mouse resizing reveals title/number/date and state, then 5/6/7/8 columns; both edges snap modes; dragging restores split; Escape cancels');
    stage='card section navigation and default table';
    assert.equal(await ep.getByRole('columnheader').count(),4);
    assert.deepEqual(await detail.locator('.document-card > .tabs button').allTextContents(),['Карточка','Файлы и версии','История изменений','Содержание']);
    assert(!/UUID|редакция|LAYOUT-1/.test(await detail.locator('.document-card > .toolbar').innerText()));
    const navigation=detail.getByRole('navigation',{name:'Разделы формы',exact:true});
    await searchToggle.click();await detail.evaluate(el=>el.scrollTop=450);
    const navBounds=await navigation.boundingBox(), fieldBounds=await detail.locator('.form-sections').boundingBox(), paneBounds=await detail.boundingBox();
    assert(navBounds.x>=fieldBounds.x+fieldBounds.width);assert(navBounds.y>=paneBounds.y && navBounds.y+navBounds.height<=paneBounds.y+paneBounds.height);
    await navigation.getByRole('link',{name:'Оригиналы и вложения',exact:true}).click();
    await ep.waitForFunction(()=>{const id=decodeURIComponent(location.hash.slice(1)),a=document.getElementById(id)?.getBoundingClientRect(),b=document.querySelector('.document-detail-body')?.getBoundingClientRect();return a && b && a.top>=b.top && a.top<b.bottom;});
    await ep.screenshot({path:path.join(outputDir,'sticky-navigation.png'),fullPage:true,mask:[ep.locator('.account')]});
    await navigation.getByRole('link',{name:'Основные сведения',exact:true}).click();await searchToggle.click();
    await detail.evaluate(el=>el.scrollTop=0);
    pass('Compact table has three sortable headers plus lifecycle state; only four card tabs; metadata removed; right-side section navigation stays visible and anchors work');
    stage='draft and scroll';
    await list.evaluate(el=>el.scrollTop=180);await detail.evaluate(el=>el.scrollTop=240);await delay(100);
    const before=await ep.evaluate(()=>[document.querySelector('[aria-label="Список документов"]').scrollTop,document.querySelector('[aria-label="Содержимое карточки"]').scrollTop]);assert(before.every(x=>x>0));
    for(const name of ['Таблица','Карточка','Вместе'])await mode(name).click();
    assert.deepEqual(await ep.evaluate(()=>[document.querySelector('[aria-label="Список документов"]').scrollTop,document.querySelector('[aria-label="Содержимое карточки"]').scrollTop]),before);
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
    await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).fill('Компоновка 01. Сохранённый документ');
    await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).evaluate(el=>window.layoutInput=el);
    await navigation.getByRole('link',{name:'Контроль исполнения',exact:true}).focus();await ep.keyboard.press('Enter');
    await ep.goBack();await ep.goForward();
    assert.equal(await detail.getByRole('textbox',{name:/^Наименование LAYOUT/}).evaluate(el=>el===window.layoutInput),true);
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
    await ep.getByRole('button',{name:'Сортировать: Тема',exact:true}).waitFor();assert.equal(await ep.getByRole('columnheader').count(),8);
    const first=row(docs[0].id);await first.getByText('Сотрудник проверки editor',{exact:true}).waitFor();assert(!(await first.innerText()).includes(roles.editor.account.id));
    await ep.getByRole('button',{name:'Сортировать: Тема',exact:true}).click();assert.equal(await ep.getByRole('columnheader').filter({hasText:'Тема'}).getAttribute('aria-sort'),'ascending');
    await ep.waitForFunction(()=>document.querySelector('.document-list-pane')?.getAttribute('aria-busy')==='false');
    // Equal topic values use the API's UUID tiebreaker; the original selection may now be on page two.
    const visibleRow=ep.locator('tbody tr[data-document-id]').first();
    await visibleRow.dblclick();assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');await ep.getByRole('button',{name:'К списку',exact:true}).click();assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');
    await visibleRow.focus();await ep.keyboard.press('Enter');await ep.getByRole('button',{name:'Показать список документов',exact:true}).click();await mode('Таблица').click();await ep.getByRole('button',{name:'Показать карточку документа',exact:true}).click();
    pass('Eight configured columns, resolved user, sorting, double-click, Enter, return and rails; history and file remain available');
    stage='search and infinite feed';
    const feedRequests=[];
    const recordFeed=request=>{if(new URL(request.url()).pathname==='/api/v1/search'&&request.method()==='POST'){const body=request.postDataJSON();if(body.query==='Компоновка')feedRequests.push(body);}};
    ep.on('request',recordFeed);
    await ep.getByRole('searchbox').fill('Компоновка');await ep.getByRole('button',{name:'Найти',exact:true}).click();await ep.getByText('Найдено: 22',{exact:true}).waitFor();
    assert.equal(await ep.getByRole('button',{name:'Вперёд',exact:true}).count(),0);
    assert.equal(await ep.getByText(/^Страница \d+$/).count(),0);
    await fillFeed(ep,22);
    const loadedIds=await list.locator('tr[data-document-id]').evaluateAll(rows=>rows.map(row=>row.dataset.documentId));assert.equal(new Set(loadedIds).size,22);
    const cursorRequests=feedRequests.filter(body=>body.cursor);assert(cursorRequests.length>0);assert.equal(new Set(cursorRequests.map(body=>body.cursor)).size,cursorRequests.length);
    const exhaustedRequests=feedRequests.length;await list.evaluate(el=>el.scrollTop=el.scrollHeight);await delay(400);assert.equal(feedRequests.length,exhaustedRequests);
    ep.off('request',recordFeed);
    const feedScroll=await list.evaluate(el=>el.scrollTop),url=ep.url();
    for(const name of ['Таблица','Карточка','Вместе'])await mode(name).click();
    assert.equal(ep.url(),url);assert.equal(await ep.getByRole('searchbox').inputValue(),'Компоновка');
    assert.equal(await list.locator('tr[data-document-id]').count(),22);assert.equal(await list.evaluate(el=>el.scrollTop),feedScroll);
    assert.equal(new URL(ep.url()).searchParams.has('cursor'),false);assert.equal(new URL(ep.url()).searchParams.has('page'),false);
    await dragTo(.55);const savedWidth=await separator.getAttribute('aria-valuenow');
    for(const name of ['Таблица','Вместе','Карточка']){await mode(name).click();await ep.locator('.document-detail-body').evaluate(el=>el.scrollTop=0);await ep.locator('.document-list-body .document-pane-scroll').evaluate(el=>el.scrollTop=0);await ep.screenshot({path:path.join(outputDir,name+'.png'),fullPage:true,mask:[ep.locator('.account')]});}
    await ep.reload();await dismissNav(ep);assert.equal(await mode('Карточка').getAttribute('aria-checked'),'true');
    await ep.getByRole('button',{name:'Показать список документов',exact:true}).click();assert.equal(await separator.getAttribute('aria-valuenow'),savedWidth);
    await ep.setViewportSize({width:390,height:844});for(const name of ['Таблица','Вместе','Карточка']){await mode(name).click();assert(await ep.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
    assert.equal(await navigation.evaluate(el=>getComputedStyle(el).flexDirection),'row');
    await navigation.getByRole('link',{name:'Основные сведения',exact:true}).click();
    await mode('Вместе').click();assert.equal(await separator.getAttribute('aria-orientation'),'horizontal');await separator.focus();await ep.keyboard.press('End');assert.equal(await mode('Таблица').getAttribute('aria-checked'),'true');assert(await list.evaluate(el=>el.scrollWidth>el.clientWidth));await ep.screenshot({path:path.join(outputDir,'mobile.png'),fullPage:true,mask:[ep.locator('.account')]});
    pass('Real search appends opaque cursor batches without duplicates; loaded rows, scroll and URL survive modes; persisted preference and mobile scrolling');
    stage='viewer ACL';await vp.goto(`${origin}/view?registry_id=${registry.id}&document_type_id=${type.id}&sort=title&direction=asc`);await dismissNav(vp);
    await vp.getByRole('radio',{name:'Таблица',exact:true}).click();const viewerRow=vp.locator(`tr[data-document-id="${docs[0].id}"]`);
    await viewerRow.getByText('Организация работы',{exact:true}).waitFor();assert.equal((await viewerRow.getByRole('gridcell').nth(5).innerText()).trim(),'—');assert(!(await viewerRow.innerText()).includes('Закрытая классификация'));
    await vp.getByRole('radio',{name:'Вместе',exact:true}).click();await viewerRow.click();await vp.getByRole('button',{name:'История изменений',exact:true}).waitFor();assert.equal(await vp.getByRole('button',{name:'Редактировать карточку',exact:true}).count(),0);
    await vp.getByRole('button',{name:'Файлы и версии',exact:true}).click();assert.equal(await vp.getByRole('button',{name:/Скачать/}).count(),0);
    assert.deepEqual(errors,[]);pass('Viewer ACL redacts field values and preserves read-only card and file restrictions; no runtime errors');
    stage='publication fixtures and All-registry creation';
    await ep.setViewportSize({width:1600,height:1050});
    await ep.goto(`${origin}/work?document_type_id=${type.id}`);await dismissNav(ep);
    const publicationToolbar=ep.getByRole('group',{name:'Вид и сортировка документов',exact:true});
    assert.equal(await publicationToolbar.getByRole('combobox',{name:'Реестр',exact:true}).inputValue(),'');
    const createButton=publicationToolbar.getByRole('button',{name:'Создать документ',exact:true});
    assert.equal(await createButton.locator('..').getByRole('radiogroup',{name:'Отображение рабочей области',exact:true}).count(),1);
    const savedViews=ep.locator('.search-saved-views');
    assert(await savedViews.evaluate(el=>!!(document.querySelector('.search-form').compareDocumentPosition(el)&Node.DOCUMENT_POSITION_FOLLOWING)));
    await createButton.click();
    const createPane=ep.locator('.document-create');await createPane.waitFor();
    assert.equal(await ep.getByRole('region',{name:'Список документов',exact:true}).isVisible(),true);assert.equal(await publicationToolbar.isVisible(),true);
    assert.equal(await createPane.getByRole('combobox',{name:'Реестр документа',exact:true}).inputValue(),registry.id);
    await createPane.getByRole('combobox',{name:'Тип документа',exact:true}).selectOption(type.id);
    const createState=createPane.getByRole('combobox',{name:'Состояние карточки',exact:true});await createState.waitFor();
    assert.equal(await createState.inputValue(),'draft');assert.equal(await createPane.getByLabel('Внутренняя база',{exact:true}).isChecked(),true);assert.equal(await createPane.getByLabel('Общая база',{exact:true}).isChecked(),false);
    const publicationNumber='LIFECYCLE-'+code;
    await createPane.getByRole('textbox',{name:/^Наименование LAYOUT/}).fill('Публикация из всех реестров '+code);
    await createPane.getByRole('textbox',{name:'Номер LAYOUT',exact:true}).fill(publicationNumber);
    await createPane.getByRole('textbox',{name:'Статус правового акта LAYOUT',exact:true}).fill('Действует');
    await createPane.getByLabel('Оригинал LAYOUT',{exact:true}).setInputFiles({name:'publication.pdf',mimeType:'application/pdf',buffer:syntheticPdf()});
    assert.equal(await createPane.getByRole('heading',{name:'Основные сведения',exact:true}).locator('..').getByRole('combobox',{name:'Состояние карточки',exact:true}).count(),1);
    await detail.evaluate(el=>el.scrollTop=0);await ep.screenshot({path:path.join(outputDir,'publication-inline-creation-main.png'),fullPage:true,mask:[ep.locator('.account')]});
    await createPane.getByRole('heading',{name:'Публикация',exact:true}).scrollIntoViewIfNeeded();await ep.screenshot({path:path.join(outputDir,'publication-inline-creation-targets.png'),fullPage:true,mask:[ep.locator('.account')]});
    const creation=ep.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/documents'&&response.request().method()==='POST');
    await createPane.getByRole('button',{name:'Создать документ',exact:true}).click();const createdResponse=await creation;assert.equal(createdResponse.status(),201);
    let privateCard=await createdResponse.json();assert.equal(privateCard.card_state,'draft');assert.equal(privateCard.status,'Действует');assert.deepEqual(privateCard.publication_targets,['internal']);assert.equal(privateCard.files.length,1);
    await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();await indexed(ep,privateCard.id);
    pass('All-registry creation sits beside layout; saved filters follow search; new card defaults to draft/internal with shared unchecked');

    stage='reader and user publication boundary';
    // Readers now also have download rights so publication checks, rather than a
    // missing file grant, decide all original and derived-content probes.
    await json(ap,`/admin/access/${registry.id}`,'PUT',{entries:[
      {user_id:me.id,permissions:['read','create','update','upload','download']},
      {user_id:roles.editor.account.id,permissions:['read','create','update','upload','download']},
      {user_id:roles.viewer.account.id,permissions:['read','download']},
      {user_id:roles.user.account.id,permissions:['read','create','update','upload','download']},
    ]});
    async function extractionFor(card) {
      for(let attempt=0;attempt<60;attempt++) {
        const content=await json(ep,`/documents/${card.id}/content?mode=editor`);
        const item=content.items.find(item=>item.file_id===card.files[0].id);
        if(item?.state==='completed'){assert(item.blocks.length>0,'Synthetic PDF must produce content');return item;}
        if(item && ['failed','unsupported','encrypted','no_text'].includes(item.state))assert.fail('Synthetic PDF extraction did not complete: '+item.state);
        await delay(500);
      }
      assert.fail('Synthetic PDF extraction did not complete');
    }
    const privateExtraction=await extractionFor(privateCard);
    async function matching(page,card,mode='editor',cardState=null) {
      return json(page,'/search','POST',{query:'',registry_ids:[registry.id],reg_number:card.reg_number,mode,card_state:cardState,page_size:100});
    }
    async function exportText(page,card,mode='editor') {
      const parameters={query:'',registry_ids:[registry.id],reg_number:card.reg_number,mode,page_size:100};
      const result=await page.evaluate(async parameters=>{const response=await fetch('/api/v1/search/export?parameters='+encodeURIComponent(JSON.stringify(parameters))+'&format=csv');return {status:response.status,text:await response.text()};},parameters);
      assert.equal(result.status,200);return result.text;
    }
    async function hidden(card,extraction) {
      for(const page of [vp,up])for(const requestedMode of ['viewer','editor']) {
        for(const endpoint of [`/documents/${card.id}`,`/documents/${card.id}/history`,`/documents/${card.id}/content`,`/files/${card.files[0].id}/download`,`/content/extractions/${extraction.id}/blocks`])
          await json(page,endpoint+'?mode='+requestedMode,'GET',undefined,404);
        await json(page,`/documents/${card.id}/search-index`,'GET',undefined,404);
        for(const cardState of [null,'draft','published']){const result=await matching(page,card,requestedMode,cardState);assert.equal(result.total,0);assert.equal(result.items.length,0);}
        const legacyList=await json(page,`/documents?registry_id=${registry.id}&reg_number=${encodeURIComponent(card.reg_number)}&mode=${requestedMode}`);assert.equal(legacyList.total,0);
        assert(!(await exportText(page,card,requestedMode)).includes(card.title));
      }
    }
    async function assertReadable(card,extraction) {
      for(const page of [vp,up]) {
        const result=await json(page,`/documents/${card.id}?mode=editor`);assert.equal(result.card_state,'published');assert(result.publication_targets.includes('shared'));assert(!Object.hasOwn(result.values,'custom_kind'));
        assert((await json(page,`/documents/${card.id}/history?mode=editor`)).length>0);
        await json(page,`/files/${card.files[0].id}/download?mode=editor`);
        assert((await json(page,`/documents/${card.id}/content?mode=editor`)).items.length>0);
        assert((await json(page,`/content/extractions/${extraction.id}/blocks?mode=editor`)).items.length>0);
        assert.equal((await matching(page,card)).total,1);assert.equal((await matching(page,card,'editor','draft')).total,0);
        assert((await exportText(page,card)).includes(card.title));
        await json(page,`/documents/${card.id}`,'PUT',{revision:card.revision,values:{},card_state:'draft'},403);
      }
    }
    async function editPublication(card,change) {
      await ep.goto(`${origin}/work?registry_id=${registry.id}&document=${card.id}`);await dismissNav(ep);
      await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).click();
      const publication=detail.getByRole('heading',{name:'Публикация',exact:true}).locator('..');
      const main=detail.getByRole('heading',{name:'Основные сведения',exact:true}).locator('..');
      assert.equal(await publication.count(),1);assert.equal(await publication.getByRole('combobox',{name:'Состояние карточки',exact:true}).count(),0);await change(publication,main);
      const saving=ep.waitForResponse(response=>new URL(response.url()).pathname===`/api/v1/documents/${card.id}`&&response.request().method()==='PUT');
      await ep.getByRole('button',{name:'Сохранить карточку',exact:true}).click();const response=await saving;assert.equal(response.status(),200);
      await ep.getByRole('button',{name:'Редактировать карточку',exact:true}).waitFor();return response.json();
    }
    await hidden(privateCard,privateExtraction);
    privateCard=await editPublication(privateCard,async publication=>{await publication.getByLabel('Общая база',{exact:true}).check();});
    assert.equal(privateCard.card_state,'draft');assert(privateCard.publication_targets.includes('shared'));await hidden(privateCard,privateExtraction);
    privateCard=await editPublication(privateCard,async (_publication,main)=>{await main.getByRole('combobox',{name:'Состояние карточки',exact:true}).selectOption('published');});
    assert.equal(privateCard.status,'Действует');
    await indexed(ep,privateCard.id);await assertReadable(privateCard,privateExtraction);
    await ep.getByRole('radio',{name:'Вместе',exact:true}).click();await detail.evaluate(el=>el.scrollTop=0);
    await ep.getByRole('textbox',{name:'Регистрационный номер',exact:true}).fill(publicationNumber);await ep.getByRole('button',{name:'Найти',exact:true}).click();
    await ep.locator(`tr[data-document-id="${privateCard.id}"]`).getByRole('gridcell',{name:'Состояние карточки: Опубликован',exact:true}).waitFor();
    await ep.screenshot({path:path.join(outputDir,'publication-main-card-and-list-state.png'),fullPage:true,mask:[ep.locator('.account')]});
    await json(up,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,values:{caption:'Запрещённое создание '+code}},403);
    pass('Draft/internal and draft/shared remain hidden from viewer and user across direct/history/original/content/blocks/search/list/export; published/shared becomes readable');

    stage='official internal sharing and revocation';
    let official=await json(ep,'/documents','POST',{registry_id:registry.id,document_type_id:type.id,schema_version_id:version.id,card_state:'published',values:{caption:'Официальная внутренняя карточка '+code,number:'OFFICIAL-'+code,custom_kind:'Закрытая классификация'},files:[{field_key:'original',name:'official.pdf',mime:'application/pdf',content_base64:syntheticPdf().toString('base64')}]},201);
    assert.deepEqual(official.publication_targets,['internal']);await indexed(ep,official.id);const officialExtraction=await extractionFor(official);
    documentAcls.push(official.id);
    await json(ap,`/documents/${official.id}/acl`,'PUT',{entries:[{user_id:me.id,permissions:['read','update','download']},{user_id:roles.editor.account.id,permissions:['read','update','download']},...['viewer','user'].map(role=>({user_id:roles[role].account.id,permissions:['read','download']}))]});
    await hidden(official,officialExtraction);
    official=await editPublication(official,async (publication,main)=>{
      assert.equal(await main.getByRole('option',{name:'Черновик',exact:true}).count(),0);
      await publication.getByLabel('Общая база',{exact:true}).check();
    });
    await indexed(ep,official.id);await assertReadable(official,officialExtraction);
    await json(ep,`/documents/${official.id}`,'PUT',{revision:official.revision,values:{},card_state:'draft'},409);
    // A shared card must still obey its independent per-document ACL.
    await json(ap,`/documents/${official.id}/acl`,'PUT',{entries:[{user_id:me.id,permissions:['read','update','download']},{user_id:roles.editor.account.id,permissions:['read','update','download']}]});
    await hidden(official,officialExtraction);
    await json(ap,`/documents/${official.id}/acl`,'PUT',{entries:[{user_id:me.id,permissions:['read','update','download']},{user_id:roles.editor.account.id,permissions:['read','update','download']},...['viewer','user'].map(role=>({user_id:roles[role].account.id,permissions:['read','download']}))]});
    await assertReadable(official,officialExtraction);
    official=await editPublication(official,async publication=>{await publication.getByLabel('Общая база',{exact:true}).uncheck();});
    assert.equal(official.card_state,'published');assert.deepEqual(official.publication_targets,['internal']);await hidden(official,officialExtraction);
    assert((await json(ep,`/documents/${official.id}/history`)).some(revision=>revision.card_state==='published'&&revision.publication_targets?.includes('shared')));
    pass('Published/internal remains private despite ACL grants; explicit sharing allows reads, ACL revocation still blocks them, and unsharing revokes old history/files/content immediately');

    stage='non-editor user browser workspace';
    await up.goto(`${origin}/work?registry_id=${registry.id}&document=${privateCard.id}`);await dismissNav(up);
    await up.waitForURL(url=>url.pathname==='/view');await up.getByRole('button',{name:'История изменений',exact:true}).waitFor();
    assert.equal(await up.getByRole('button',{name:'Создать документ',exact:true}).count(),0);assert.equal(await up.getByRole('button',{name:'Редактировать карточку',exact:true}).count(),0);
    assert.equal(await up.getByRole('combobox',{name:'Состояние карточки',exact:true}).getByRole('option',{name:'Черновик',exact:true}).count(),0);
    await up.getByRole('button',{name:'Открыть навигацию',exact:true}).click();
    const spacePicker=up.getByRole('combobox',{name:'Рабочее пространство',exact:true});await spacePicker.waitFor();
    const spaces=await spacePicker.locator('option').allTextContents();assert.deepEqual(spaces,['Читатель']);
    await dismissNav(up);
    await up.screenshot({path:path.join(outputDir,'publication-user-reader.png'),fullPage:true,mask:[up.locator('.account')]});
    await ep.screenshot({path:path.join(outputDir,'publication-official-internal.png'),fullPage:true,mask:[ep.locator('.account')]});
    assert.deepEqual(errors,[]);pass('User role remains a reader on a typed /work URL despite write grants; no mutation controls or draft filter; zero runtime errors');
    return {status:'PASS',source_sha:expectedSha,checks,errors};
  } catch(error) {
    await fs.writeFile(path.join(outputDir,'failure.txt'),stage+'\n'+String(error.stack||error));throw error;
  } finally {
    if(ap){for(const id of documentAcls)await json(ap,`/documents/${id}/acl`,'PUT',{entries:[]});for(const owned of registries)await json(ap,`/admin/access/${owned.id}`,'PUT',{entries:[]});for(const id of accounts)await json(ap,`/admin/users/${id}`,'PATCH',{active:false});}
    for(const context of contexts){for(const page of context.pages())await page.screenshot({path:path.join(outputDir,`last-${contexts.indexOf(context)}.png`),fullPage:true,mask:[page.locator('.account')]}).catch(()=>{});await context.close();}
    await fs.writeFile(path.join(outputDir,'checks.json'),JSON.stringify({checks,stage,errors},null,2));
  }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){let browser;try{const config=JSON.parse(await fs.readFile(process.argv[2],'utf8')),admin=JSON.parse(await fs.readFile(config.adminCredentialsFile,'utf8'));const {chromium}=await import(config.playwrightModule?pathToFileURL(config.playwrightModule).href:'playwright');browser=await chromium.launch({headless:true,...(config.executablePath?{executablePath:config.executablePath}:{})});const receipt=await runDocumentWorkspace({...config,browser,admin});await fs.writeFile(path.join(config.outputDir,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(`Document workspace HTTPS PASS: ${receipt.checks.length} scenarios.`);}catch(error){console.error(`Document workspace FAIL: ${error.name}: ${error.message.split('\n')[0]}`);process.exitCode=1;}finally{await browser?.close();}}
