(() => {
  'use strict';
  const root = document.getElementById('botainer-study');
  let demo = globalThis.BotainerDemo.createDemo();
  const ui = {projectId:'p-dashboard', sessionId:'s-local', tab:'terminal', hostFilter:'all', installationFilter:'all', query:'', sort:'recent', flow:null, transcriptCount:8, inputs:{}, configMode:'quick', configScope:'project', filePaths:{}, rawDrafts:{}};
  const design = {showPaths:true, groupBy:'Project'};
  const slot = name => root.querySelector('[data-slot="' + name + '"]');
  const panel = name => root.querySelector('#bt-panel-' + name);
  const label = value => value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Unknown';
  const project = () => demo.getProject(ui.projectId);
  const session = () => ui.sessionId ? demo.getSession(ui.sessionId) : null;
  const host = () => demo.getHost(project().hostId);
  const activeStatuses = new Set(['starting','queued','running']);
  const isActive = s => Boolean(s && activeStatuses.has(s.status));
  const displayState = s => !s ? 'idle' : !demo.getHost(demo.getProject(s.projectId).hostId).reachable && isActive(s) ? 'unknown' : s.status;
  const describeConfig = c => label(c.agent) + ' · ' + c.authMode + ' credentials · ' + (c.network === 'none' ? 'no network' : 'internet access');
  const el = (tag,text,cls) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  const button = (text,handler,primary=false) => {const b=el('button',text,(primary?'bt-primary ':'')+'cursor-interaction');b.type='button';b.addEventListener('click',handler);return b;};
  function notice(text='',error=false) {slot('notice').textContent=text;slot('notice').dataset.error=String(error);slot('notice').setAttribute('role',error?'alert':'status');}
  function remember() {
    if(window.openai && window.openai.setWidgetState) window.openai.setWidgetState({modelContent:{selectedProject:project().name,view:ui.tab},privateContent:{projectId:ui.projectId,sessionId:ui.sessionId,tab:ui.tab,hostFilter:ui.hostFilter}}).catch(()=>{});
  }
  function restore(state) {
    const saved=state && state.privateContent;
    if(!saved)return;
    if(demo.getProject(saved.projectId))ui.projectId=saved.projectId;
    const s=demo.getSession(saved.sessionId);
    if(s && s.projectId===ui.projectId)ui.sessionId=s.id;
    else ui.sessionId=demo.getLiveSession(ui.projectId)?.id || null;
    if(['terminal','settings','details','files'].includes(saved.tab))ui.tab=saved.tab;
    if(saved.hostFilter==='all'||demo.getHost(saved.hostFilter))ui.hostFilter=saved.hostFilter;
  }
  function selectProject(id,sessionId) {
    ui.projectId=id;ui.sessionId=sessionId===undefined?(demo.getLiveSession(id)?.id || null):sessionId;
    ui.flow=null;ui.transcriptCount=8;ui.configScope='project';if(window.matchMedia('(max-width:760px)').matches)root.dataset.navOpen='false';notice();render();remember();
  }
  function perform(result,success) {
    if(!result.ok){notice(result.error.message,true);return false;}
    render();notice(success);remember();return true;
  }
  function renderSidebar() {
    const select=slot('host-filter');select.replaceChildren();
    const all=el('option','All machines');all.value='all';select.append(all);
    demo.state.hosts.forEach(h=>{const o=el('option',h.name+(h.reachable?'':' · offline'));o.value=h.id;select.append(o);});select.value=ui.hostFilter;
    const instances=slot('installation-filter');instances.replaceChildren();const any=el('option','All instances');any.value='all';instances.append(any);
    demo.state.installations.filter(i=>ui.hostFilter==='all'||i.hostId===ui.hostFilter).forEach(i=>{const o=el('option',i.name+' · '+demo.getHost(i.hostId).name);o.value=i.id;instances.append(o);});instances.value=ui.installationFilter;
    slot('projects').replaceChildren();
    const visible=demo.listProjects({query:ui.query,hostId:ui.hostFilter==='all'?undefined:ui.hostFilter,installationId:ui.installationFilter==='all'?undefined:ui.installationFilter,sort:ui.sort});
    const groups=design.groupBy==='Machine'?[...new Set(visible.map(p=>p.hostId))]:[null];
    groups.forEach(groupId=>{
      if(groupId)slot('projects').append(el('p',demo.getHost(groupId).name,'bt-eyebrow'));
      let previousPinned=null;
      visible.filter(p=>groupId===null||p.hostId===groupId).forEach(p=>{
        if(previousPinned!==p.pinned){slot('projects').append(el('p',p.pinned?'Pinned':ui.sort==='name'?'Projects':ui.sort==='active'?'Active first':'Recent activity','bt-list-heading'));previousPinned=p.pinned;}
        const group=el('div',undefined,'bt-group');const heading=el('div',undefined,'bt-group-name');
        const projectButton=button(p.name,()=>selectProject(p.id));projectButton.classList.add('bt-quiet');projectButton.setAttribute('aria-pressed',String(ui.projectId===p.id));
        const pin=button(p.pinned?'Unpin':'Pin',()=>{demo.setProjectPinned(p.id,!p.pinned);renderSidebar();notice(p.name+(p.pinned?' pinned above recent projects.':' unpinned.'));});pin.classList.add('bt-pin');pin.setAttribute('aria-label',(p.pinned?'Unpin ':'Pin ')+p.name);pin.setAttribute('aria-pressed',String(p.pinned));heading.append(projectButton,pin);group.append(heading);
        const runs=demo.state.sessions.filter(s=>s.projectId===p.id).slice().reverse();
        const current=demo.getLiveSession(p.id)||runs[0];
        const instance=demo.getInstallation(p.installationId);
        const context=el('div',demo.getHost(p.hostId).name+' / '+instance.name,'bt-session-meta bt-project-context');group.append(context);
        function runRow(s){const row=button('',()=>{ui.tab='terminal';selectProject(p.id,s.id);});row.className='bt-session cursor-interaction';row.setAttribute('aria-pressed',String(ui.sessionId===s.id));row.append(el('span',s.title));const meta=el('span',undefined,'bt-session-meta');const state=el('span',label(displayState(s)),'bt-badge');state.dataset.state=displayState(s);const agent=el('span',label(s.launchConfig.agent),'bt-agent-tag');agent.dataset.agent=s.launchConfig.agent;meta.append(state,agent);row.append(meta);return row;}
        if(current)group.append(runRow(current));else{const idle=button('Idle · '+label(p.savedConfig.agent)+' · Start session',()=>{selectProject(p.id,null);openNewSession(p.id);});idle.className='bt-session cursor-interaction';group.append(idle);}
        if((ui.projectId===p.id||ui.query.trim())&&runs.some(s=>s!==current)){const history=el('details');history.append(el('summary','History · '+runs.filter(s=>s!==current).length,'cursor-interaction'));runs.filter(s=>s!==current).forEach(s=>history.append(runRow(s)));if(ui.query.trim()||(ui.sessionId&&ui.sessionId!==current?.id))history.open=true;group.append(history);}
        slot('projects').append(group);
      });
    });
    if(!visible.length)slot('projects').append(el('p','No matching projects. Try another search or filter.','bt-caption'));
  }
  function renderHeader() {
    const p=project(),s=session(),h=host(),state=displayState(s);
    slot('breadcrumb').textContent=p.name+' / '+h.name+' / '+demo.getInstallation(p.installationId).name;
    slot('title').textContent=s?s.title:p.name;
    slot('path').textContent=p.path;slot('path').hidden=!design.showPaths;
    slot('badges').replaceChildren();const badge=el('span',s?label(state):'Ready for a session','bt-badge');badge.dataset.state=state;slot('badges').append(badge);
    slot('badges').append(el('span',label(s?s.launchConfig.agent:p.savedConfig.agent)),el('span',h.reachable?'Host connected':'Host unreachable · cached state'));
    if(s)slot('badges').append(el('span','Observed '+s.observedAt.slice(11,19)+' UTC'));
    if(s&&s.status==='running'&&h.reachable)slot('badges').append(el('span',s.terminalConnected?'Terminal connected':'View detached'));
    if(s&&demo.sessionView(s.id).installationConfigChanged)slot('badges').append(el('span','Instance policy changed'));
    if(s&&JSON.stringify(s.launchConfig)!==JSON.stringify(p.savedConfig))slot('badges').append(el('span','Saved defaults changed'));
    slot('header-action').replaceChildren();
    if(s&&s.status==='running'&&h.reachable)slot('header-action').append(button(s.terminalConnected?'Detach view':'Reconnect',()=>{const result=s.terminalConnected?demo.disconnectTerminal(s.id):demo.connectTerminal(s.id);perform(result,s.terminalConnected?'Reconnected to the same demo session.':'View detached. The demo session is still running.');}));
    else if(!s||!isActive(s))slot('header-action').append(button('New session',()=>openNewSession(p.id),true));
    root.querySelectorAll('[data-tab]').forEach(t=>{const selected=t.dataset.tab===ui.tab;t.setAttribute('aria-selected',String(selected));panel(t.dataset.tab).hidden=!selected;});
    root.querySelector('[data-tab="details"]').textContent=s?'Run details':'Project details';
  }
  function renderTerminal() {
    const target=panel('terminal'),p=project(),s=session(),h=host();target.replaceChildren();
    if(!s){const empty=el('div',undefined,'bt-callout');empty.append(el('h3','A place for your next task'),el('p','Start a session in '+p.name+'. Its terminal and history will appear here.'),el('p',describeConfig(p.savedConfig),'bt-caption'));empty.append(button('Start first session',()=>openNewSession(p.id),true));target.append(empty);return;}
    if(s.status==='queued'||s.status==='starting'){
      const pending=el('div',undefined,'bt-callout');pending.append(el('h3',s.status==='queued'?'Waiting for an allocation':'Starting the session'),el('p',h.reachable?(s.status==='queued'?'The cluster has accepted this request. The terminal becomes available when an allocation starts.':'The project is prepared. The terminal opens after the agent is ready.'):'The host is unreachable. The last known state is retained.'));
      const facts=el('dl',undefined,'bt-facts');const rows=[['Machine',h.name],['Resources',s.launchConfig.cpus+' CPUs · '+s.launchConfig.memoryGb+' GB'],['Time limit',(s.launchConfig.timeLimitMinutes||120)+' minutes'],['State',h.reachable?label(s.status):'Unknown · host offline']];if(s.jobId)rows.push(['Job ID',s.jobId],['Pending reason',s.queueReason||'Preparing terminal']);rows.forEach(([k,v])=>facts.append(el('dt',k),el('dd',v)));pending.append(facts);
      if(h.reachable){const actions=el('div',undefined,'bt-actions');actions.append(button(s.status==='queued'?'Simulate allocation granted':'Finish demo startup',()=>{const result=demo.advanceSession(s.id);if(!result.ok){perform(result);return;}perform(demo.connectTerminal(s.id),'The demo agent is ready. Type a sample prompt below.');},true),button('Cancel session…',()=>openStop(s.id)));pending.append(actions);}else pending.append(button('View machines',openMachines));target.append(pending);return;
    }
    const terminal=el('div',undefined,'bt-terminal');const top=el('div',undefined,'bt-terminal-top');top.append(el('span',label(s.launchConfig.agent)+' · '+(h.reachable?'session terminal':'cached terminal output')),el('span','Simulated output · no commands execute'));terminal.append(top);
    const transcript=el('div',undefined,'bt-transcript');transcript.setAttribute('role','log');transcript.setAttribute('aria-label','Simulated session transcript');
    const entries=s.transcript || [];if(entries.length>ui.transcriptCount){const earlier=button('Show earlier output',()=>{ui.transcriptCount+=12;renderTerminal();});transcript.append(earlier);}
    entries.slice(-ui.transcriptCount).forEach(entry=>{const line=el('div',(entry.kind==='input'?'› ':'')+entry.text,'bt-entry');line.dataset.kind=entry.kind;transcript.append(line);});terminal.append(transcript);
    const canWrite=h.reachable&&s.status==='running'&&s.terminalConnected;
    if(canWrite){const form=el('form',undefined,'bt-input-row');const inputLabel=el('label','Prompt');const input=el('textarea');input.rows=1;input.name='prompt';input.placeholder='For example: summarize the changes';input.maxLength=2000;input.required=true;input.value=ui.inputs[s.id]||'';input.addEventListener('input',()=>{ui.inputs[s.id]=input.value;});input.setAttribute('aria-label','Demo terminal input');inputLabel.append(input);const send=button('Send',()=>{} ,true);send.setAttribute('aria-label','Send in demo');send.type='submit';form.append(inputLabel,send);form.addEventListener('submit',event=>{event.preventDefault();const result=demo.sendInput(s.id,input.value);if(result.ok)ui.inputs[s.id]='';if(perform(result,'Sample response added to this session.')){panel('terminal').querySelector('textarea')?.focus();}});input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});terminal.append(form);}
    else {const message=!h.reachable?'Host unreachable. Output is cached; input is disabled.':s.status!=='running'?'This run has ended. Its transcript stays in history.':'View detached. The run continues; reconnect to type.';terminal.append(el('p',message,'bt-caption'));}
    target.append(terminal);
    const footer=el('div',undefined,'bt-footer');footer.append(el('span',s.status==='running'?'Closing a view leaves the session running.':'Start a new session to continue working.','bt-caption'));
    if(isActive(s)&&h.reachable)footer.append(button('Stop session…',()=>openStop(s.id)));else if(!h.reachable)footer.append(button('View machines',openMachines));else footer.append(button('New session',()=>openNewSession(p.id),true));target.append(footer);
  }
  function makeField(container,text,name,choices,value,type='text') {
    const wrapper=el('label',text);const control=el(choices?'select':'input');control.name=name;
    if(choices)choices.forEach(choice=>{const [v,t]=Array.isArray(choice)?choice:[choice,choice];const option=el('option',t);option.value=v;control.append(option);});else control.type=type;
    control.value=value??'';wrapper.append(control);container.append(wrapper);return control;
  }
  function renderSettings() {
    const target=panel('settings'),p=project(),s=session(),revision=p.draftBaseRevision;target.replaceChildren();
    const scope=makeField(target,'Configuration scope','configScope',[['project','Project · .botainer/config.yaml'],['installation','Instance policy · policy.yaml']],ui.configScope);
    scope.addEventListener('change',()=>{ui.configScope=scope.value;renderSettings();notice();});
    if(ui.configScope==='installation'){renderTextConfig(target,'installation',demo.getInstallation(p.installationId));return;}
    const modes=el('div',undefined,'bt-actions');['quick','text'].forEach(mode=>{const b=button(mode==='quick'?'Quick settings':'Text config',()=>{ui.configMode=mode;renderSettings();notice();});b.setAttribute('aria-pressed',String(ui.configMode===mode));modes.append(b);});target.append(modes);
    if(ui.configMode==='text'){renderTextConfig(target,'project',p);return;}
    target.append(el('h3','Defaults for the next session'),el('p','Save changes to this project. Each running session keeps its own launch configuration.','bt-caption'));
    if(p.draftError){target.append(el('p','The text draft has errors. Open Text config to repair it, or discard the draft before using quick settings.','bt-validation'),button('Open text draft',()=>{ui.configMode='text';renderSettings();}),button('Discard draft',()=>perform(demo.resetDraft(p.id),'Draft discarded.')));return;}
    const summary=el('div',undefined,'bt-summary');summary.append(el('p','Saved: '+describeConfig(p.savedConfig)));if(s)summary.append(el('p','This run: '+describeConfig(s.launchConfig),'bt-caption'));target.append(summary);
    const form=el('form');const fields=el('div',undefined,'bt-fields');
    const controls=[makeField(fields,'Agent','agent',[['claude','Claude'],['codex','Codex']],p.draftConfig.agent),makeField(fields,'Network','network',[['internet','Internet access'],['none','No network']],p.draftConfig.network),makeField(fields,'Credentials','authMode',[['isolated','Isolated · per project'],['shared','Shared · concurrency limited'],['broker','Broker · Claude on Docker only']],p.draftConfig.authMode),makeField(fields,'CPUs','cpus',null,p.draftConfig.cpus,'number'),makeField(fields,'Memory (GB)','memoryGb',null,p.draftConfig.memoryGb,'number')];
    if(host().id==='cluster')controls.push(makeField(fields,'Time limit (minutes)','timeLimitMinutes',null,p.draftConfig.timeLimitMinutes||120,'number'));
    controls.filter(c=>c.type==='number').forEach(c=>{c.min='1';c.max=String({cpus:256,memoryGb:1024,timeLimitMinutes:10080}[c.name]);c.step='1';c.required=true;});
    form.append(fields);const draftSummary=el('p',undefined,'bt-caption');form.append(draftSummary);
    const review=el('details');review.append(el('summary','Review draft changes','cursor-interaction'));const diff=el('div',undefined,'bt-summary');review.append(diff);form.append(review);
    function updateSummary(){const changed=Object.keys(p.savedConfig).filter(k=>p.savedConfig[k]!==p.draftConfig[k]);draftSummary.textContent=changed.length?'Unsaved changes for '+p.name+'.':'Draft matches saved defaults.';diff.replaceChildren();changed.forEach(key=>diff.append(el('p',key+': '+p.savedConfig[key]+' → '+p.draftConfig[key])));if(!changed.length)diff.append(el('p','No changes.'));}
    controls.forEach(control=>control.addEventListener('input',()=>{demo.updateDraft(p.id,{[control.name]:control.type==='number'?Number(control.value):control.value});notice();updateSummary();}));updateSummary();
    const actions=el('div',undefined,'bt-actions');const save=button('Save defaults in demo',()=>{},true);save.type='submit';const discard=button('Discard draft',()=>perform(demo.resetDraft(p.id),'Draft discarded.'));actions.append(save,discard);form.append(actions);form.addEventListener('submit',event=>{event.preventDefault();perform(demo.saveConfig(p.id,revision),'Defaults saved in this demo. Existing runs keep their launch settings.');});target.append(form);
  }
  function renderTextConfig(target,kind,record) {
    const isProject=kind==='project',instance=isProject?demo.getInstallation(record.installationId):record;
    const revision=record.draftBaseRevision;
    target.append(el('h3',isProject?'Project configuration':'Instance policy'),el('p',(isProject?record.path+'/.botainer/config.yaml':instance.stateRoot+'/policy.yaml')+' · '+demo.getHost(instance.hostId).name+' / '+instance.name,'bt-path'));
    target.append(el('p','Demo editor: JSON representation of a small config subset. The connected app will edit the original YAML and validate it with this Botainer instance.','bt-caption'));
    if(!isProject)target.append(el('p','Default credentials affect newly added projects. Network policy limits future launches on this instance; existing sessions keep their launch settings.','bt-caption'));
    const wrapper=el('label',isProject?'Project config text':'Instance policy text');const editor=el('textarea',undefined,'bt-code-editor');editor.rows=16;editor.spellcheck=false;editor.value=record.draftText;editor.setAttribute('aria-label',isProject?'Project config text':'Instance policy text');wrapper.append(editor);target.append(wrapper);
    const result=el('div',undefined,'bt-validation');result.setAttribute('aria-live','polite');target.append(result);
    const diff=el('details');diff.append(el('summary','Review file changes','cursor-interaction'));const preview=el('pre',undefined,'bt-config-diff');diff.append(preview);target.append(diff);
    function updateDiff(){preview.textContent=JSON.stringify(record.savedConfig)===JSON.stringify(record.draftConfig)&&!record.draftError?'No changes.':'Saved:\n'+JSON.stringify(record.savedConfig,null,2)+'\n\nDraft:\n'+editor.value;}
    function show(validation){result.replaceChildren();result.dataset.error=String(!validation.ok);result.setAttribute('role',validation.ok?'status':'alert');if(validation.ok)result.append(el('p','Valid for the demo schema. Real file syntax, policy, permissions and runtime checks remain an integration step.'));else (validation.error.issues||[validation.error.message]).forEach(issue=>result.append(el('p',issue)));}
    editor.addEventListener('input',()=>{const updated=isProject?demo.updateProjectConfigText(record.id,editor.value):demo.updateInstallationConfigText(record.id,editor.value);result.replaceChildren();notice();updateDiff();if(!updated.ok)show(updated);});updateDiff();if(record.draftError)show({ok:false,error:record.draftError});
    const actions=el('div',undefined,'bt-actions');actions.append(button('Validate config',()=>show(isProject?demo.validateProjectConfigText(record.id,editor.value):demo.validateInstallationConfigText(record.id,editor.value))),button('Save file in demo',()=>{const saved=isProject?demo.saveProjectConfigText(record.id,editor.value,revision):demo.saveInstallationConfigText(record.id,editor.value,revision);if(!saved.ok){show(saved);return;}if(ui.flow==='instance'){openInstance(instance.id);notice('Instance policy saved in the demo. Running sessions retain their original policy.');}else perform(saved,'Config saved in the demo. Running sessions retain their original settings.');},true),button('Discard text draft',()=>{if(isProject)demo.resetDraft(record.id);else demo.resetInstallationDraft(record.id);if(ui.flow==='instance')openInstance(instance.id);else renderSettings();notice('Draft restored to saved config.');}));target.append(actions);
  }
  function sampleEntries(path) {
    if(path==='')return [{name:'.botainer',kind:'folder'},{name:'src',kind:'folder'},{name:'results',kind:'folder'},{name:'README.md',kind:'file',text:'# Sample project\n\nProject notes and run instructions would appear here.\nThis is a synthetic file preview.'}];
    if(path==='.botainer')return [{name:'config.yaml',kind:'config'},{name:'project-id',kind:'file',text:'demo-project-id (synthetic)'}];
    if(path==='src')return [{name:'analysis.py',kind:'file',text:'# Synthetic file preview\nprint("Project analysis")\n'}];
    return [{name:'summary.txt',kind:'file',text:'Sample results from the previous run.\nNo real project files have been read.'}];
  }
  function renderFiles() {
    const target=panel('files'),p=project(),h=host();target.replaceChildren(el('h3','Project files'));
    target.append(el('p',h.name+' / '+demo.getInstallation(p.installationId).name+' · '+p.path,'bt-path'));
    target.append(el('p','Sample file browser · '+(h.kind==='local'?'local project':'remote project over the future host connection'),'bt-caption'));
    if(!h.reachable){target.append(el('p','Host unreachable. Reconnect to browse files; the project path remains available.'),button('View machines',openMachines));return;}
    const path=ui.filePaths[p.id]||'';const bar=el('div',undefined,'bt-actions');if(path)bar.append(button('Project root',()=>{ui.filePaths[p.id]='';renderFiles();}));bar.append(el('span','/ '+path,'bt-path'));target.append(bar);
    const preview=el('div',undefined,'bt-file-preview');
    sampleEntries(path).forEach(entry=>{const row=button((entry.kind==='folder'?'Folder · ':'File · ')+entry.name,()=>{if(entry.kind==='folder'){ui.filePaths[p.id]=entry.name;renderFiles();}else if(entry.kind==='config'){ui.tab='settings';ui.configMode='text';ui.configScope='project';render();}else{preview.replaceChildren(el('h3',entry.name),el('pre',entry.text,'bt-code-preview'));}});row.className='bt-file-row cursor-interaction';target.append(row);});target.append(preview);
  }
  function folderPicker(container,hostId,onChoose,onCancel) {
    const h=demo.getHost(hostId);let path='~/Projects';
    function draw(){container.replaceChildren(el('h3','Choose a folder · '+h.name),el('p','Sample folders only · '+(h.kind==='local'?'local filesystem':'remote filesystem'),'bt-caption'));if(!h.reachable){container.append(el('p','Host unreachable. Folder selection is unavailable.'),button('Close browser',onCancel));return;}
      container.append(el('p',path,'bt-path'));const children=path==='~'?['Projects','scratch']:path==='~/Projects'?['demo-research','experiment-lab','new-analysis']:path==='~/scratch'?['scratch-project']:[];
      if(path!=='~')container.append(button('Up one folder',()=>{path=path.substring(0,path.lastIndexOf('/'));draw();}));children.forEach(name=>{const b=button('Folder · '+name,()=>{path+='/'+name;draw();});b.className='bt-file-row cursor-interaction';container.append(b);});if(!children.length)container.append(el('p','No subfolders in this sample.','bt-caption'));
      const actions=el('div',undefined,'bt-actions');actions.append(button('Use this folder',()=>onChoose(path),true),button('Cancel folder selection',onCancel));container.append(actions);
    }draw();
  }
  function renderDetails() {
    const target=panel('details'),p=project(),s=session(),h=host();target.replaceChildren(el('h3',s?'This run':'This project'));
    const instance=s?s.installationSnapshot:demo.getInstallation(p.installationId);
    const facts=el('dl',undefined,'bt-facts');const rows=[['Project',p.name],['Machine',h.name],['Botainer instance',instance.name],['Executable',instance.executable],['State folder',instance.stateRoot],['Botainer version',instance.version],['Folder',p.path],['Runtime',label((s?s.launchConfig:p.savedConfig).runtime)],['Agent',label((s?s.launchConfig:p.savedConfig).agent)]];
    if(s)rows.push(['Run ID',s.id],['Run state',label(displayState(s))],['Terminal',s.terminalConnected&&h.reachable?'Connected':'Disconnected'],['Agent activity','Unknown · no structured agent signal'],['Launch configuration',describeConfig(s.launchConfig)]);
    rows.push(['Host connection',h.reachable?'Connected':'Offline · last known data retained'],['Saved defaults',describeConfig(p.savedConfig)],['Configuration revision',String(p.configRevision)]);
    rows.forEach(([k,v])=>facts.append(el('dt',k),el('dd',v)));target.append(facts,el('p','All identifiers and observations in this prototype are sample data.','bt-caption'));
  }
  function render() {
    renderSidebar();slot('workspace').hidden=Boolean(ui.flow);slot('flow').hidden=!ui.flow;
    renderHeader();renderTerminal();renderSettings();renderFiles();renderDetails();syncLayout();
  }
  function beginFlow(kind,title,intro) {ui.flow=kind;if(window.matchMedia('(max-width:760px)').matches)root.dataset.navOpen='false';syncLayout();slot('workspace').hidden=true;slot('flow').hidden=false;slot('flow').replaceChildren(el('h2',title),el('p',intro,'bt-flow-intro'));notice();return slot('flow');}
  function closeFlow(){ui.flow=null;render();notice();}
  function openNewProject() {
    const target=beginFlow('project','Add a project','Choose where the project lives. This demo creates a record only.');const form=el('form');const fields=el('div',undefined,'bt-fields');
    const name=makeField(fields,'Project name','name',null,'new-project');name.required=true;name.maxLength=64;
    const machine=makeField(fields,'Machine','hostId',demo.state.hosts.map(h=>[h.id,h.name]),ui.hostFilter==='all'?'local':ui.hostFilter);
    const instance=makeField(fields,'Botainer instance','installationId',[],null);
    function updateInstances(){instance.replaceChildren();demo.state.installations.filter(i=>i.hostId===machine.value).forEach(i=>{const o=el('option',i.name+' · '+i.stateRoot);o.value=i.id;instance.append(o);});if(demo.getInstallation(ui.installationFilter)?.hostId===machine.value)instance.value=ui.installationFilter;}
    updateInstances();
    const path=makeField(fields,'Project folder','path',null,'~/Projects/new-project');path.required=true;path.setAttribute('aria-label','Project folder');path.parentElement.classList.add('bt-full');
    const picker=el('div',undefined,'bt-folder-picker');picker.hidden=true;picker.classList.add('bt-full');
    const browse=button('Browse folders…',()=>{picker.hidden=false;folderPicker(picker,machine.value,selected=>{path.value=selected;path.dataset.edited='true';picker.hidden=true;},()=>{picker.hidden=true;});});path.parentElement.append(browse);fields.append(picker);
    machine.addEventListener('change',()=>{updateInstances();picker.hidden=true;path.dataset.edited='';path.value='~/Projects/'+name.value.trim();});
    const kind=makeField(fields,'Set up from','kind',[['existing','An existing folder'],['empty','A new empty folder']],'existing');
    const agent=makeField(fields,'Default agent','agent',[['claude','Claude'],['codex','Codex']],'claude');form.append(fields);
    form.append(el('p','This assignment selects the executable and state folder used to manage the project. The real app will show the resolved paths before initialization.','bt-caption'));
    name.addEventListener('input',()=>{if(!path.dataset.edited)path.value='~/Projects/'+name.value.trim();});path.addEventListener('input',()=>{path.dataset.edited='true';});
    const actions=el('div',undefined,'bt-actions');const create=button('Add project in demo',()=>{},true);create.type='submit';actions.append(create,button('Back',closeFlow));form.append(actions);
    form.addEventListener('input',()=>notice());
    form.addEventListener('submit',event=>{event.preventDefault();const result=demo.createProject({name:name.value,path:path.value,hostId:machine.value,installationId:instance.value,kind:kind.value,config:{agent:agent.value}});if(!result.ok){notice(result.error.message,true);return;}const p=result.project;ui.hostFilter='all';ui.installationFilter='all';ui.query='';slot('project-search').value='';ui.tab='terminal';selectProject(p.id,null);notice('Project added to the demo. Start its first session when ready.');});target.append(form);
  }
  function openNewSession(projectId=ui.projectId) {
    const target=beginFlow('launch','New session','Choose a task name, review the project defaults, and start.');const form=el('form');const fields=el('div',undefined,'bt-fields');
    const selected=makeField(fields,'Project','projectId',demo.state.projects.map(p=>[p.id,p.name+' · '+demo.getHost(p.hostId).name+' / '+demo.getInstallation(p.installationId).name]),projectId);
    const title=makeField(fields,'Session name','title',null,'Review the next change');title.required=true;title.maxLength=100;form.append(fields);
    const summary=el('div',undefined,'bt-summary');form.append(summary);const actions=el('div',undefined,'bt-actions');const launch=button('Start session in demo',()=>{},true);launch.type='submit';actions.append(launch,button('Back',closeFlow));form.append(actions);
    const existing=el('div',undefined,'bt-actions');form.append(existing);
    function update(){const p=demo.getProject(selected.value),h=demo.getHost(p.hostId),live=demo.getLiveSession(p.id),conflict=!live&&demo.authConflict(p.id);notice();summary.replaceChildren(el('p',h.name+' · '+label(p.savedConfig.runtime)),el('p',describeConfig(p.savedConfig)),el('p',p.savedConfig.cpus+' CPUs · '+p.savedConfig.memoryGb+' GB'+(h.id==='cluster'?' · '+(p.savedConfig.timeLimitMinutes||120)+' min':''),'bt-caption'));existing.replaceChildren();launch.disabled=Boolean(live)||Boolean(conflict)||!h.reachable;
      if(live){summary.append(el('p','This checkout already has an active run. Open it, or use a separate project for parallel work.','bt-caption'));existing.append(button('Open active session',()=>{ui.tab='terminal';selectProject(p.id,live.id);}),button('Add a separate project',openNewProject));}
      if(conflict){const other=demo.getProject(conflict.projectId);summary.append(el('p','Shared login in use by “'+conflict.title+'” in '+other.name+'. End that run or save an independent project login before launching.','bt-caption'));existing.append(button('Open conflicting session',()=>{ui.tab='terminal';selectProject(other.id,conflict.id);}),button('Edit project settings',()=>{ui.tab='settings';selectProject(p.id,null);}));}
      if(!h.reachable){summary.append(el('p','Host unreachable. Reconnect the host before starting a session.','bt-caption'));existing.append(button('View machines',openMachines));}
      if(demo.hasDraftChanges(p.id))summary.append(el('p','Unsaved settings are excluded. This launch uses the saved defaults.','bt-caption'));
      const instance=demo.getInstallation(p.installationId);summary.append(el('p','Botainer: '+instance.name+' · '+instance.version,'bt-caption'),el('p',instance.executable+' · state '+instance.stateRoot,'bt-path'));
    }selected.addEventListener('change',update);update();
    title.addEventListener('input',()=>notice());
    form.addEventListener('submit',event=>{event.preventDefault();const result=demo.startSession(selected.value,{title:title.value});if(!result.ok){notice(result.error.message,true);return;}const s=result.session;ui.tab='terminal';selectProject(s.projectId,s.id);notice(s.status==='queued'?'Demo allocation request queued.':'Demo session created. Finish startup to open the terminal.');});target.append(form);
  }
  function openStop(sessionId) {
    const s=demo.getSession(sessionId),p=demo.getProject(s.projectId),h=demo.getHost(p.hostId);const pending=s.status==='queued'||s.status==='starting';
    const target=beginFlow('stop',pending?'Cancel this session?':'Stop this session?',s.title+' · '+h.name);target.append(el('p',pending?'The pending run will be cancelled.':'The run will end and its transcript will stay in history. Detach the view to leave the run active.'));
    const actions=el('div',undefined,'bt-actions');const stop=button(pending?'Cancel session in demo':'Stop session in demo',()=>{const result=demo.stopSession(s.id);if(!result.ok){notice(result.error.message,true);return;}ui.flow=null;render();notice('Demo run ended. Its history is still available.');},true);stop.classList.add('bt-danger');actions.append(stop,button('Keep session',closeFlow));target.append(actions);
  }
  function openMachines() {
    const target=beginFlow('machines','Machines & Botainer instances','A machine can run several installs. Each project is assigned to an explicit executable and state folder. These are sample registrations.');
    demo.state.hosts.forEach(h=>{const row=el('div',undefined,'bt-machine');const text=el('div');text.append(el('h3',h.name),el('p',(h.reachable?'Connected':'Offline')+' · '+(h.id==='cluster'?'Slurm / Apptainer':'Docker'),'bt-caption'));row.append(text,button(h.reachable?'Simulate disconnect':'Simulate reconnect',()=>{const result=demo.setHostReachable(h.id,!h.reachable);if(!result.ok){notice(result.error.message,true);return;}renderSidebar();openMachines();notice('Demo connection updated. Existing run identities are retained.');}));target.append(row);
      demo.state.installations.filter(i=>i.hostId===h.id).forEach(i=>{const entry=el('div',undefined,'bt-instance-row');entry.append(el('h3',i.name+' · '+i.version),el('p',i.executable,'bt-path'),el('p','State: '+i.stateRoot,'bt-path'));const actions=el('div',undefined,'bt-actions');actions.append(button('Open '+i.name+' config',()=>openInstance(i.id)),button('Show '+i.name+' projects',()=>{ui.hostFilter=h.id;ui.installationFilter=i.id;ui.query='';slot('project-search').value='';closeFlow();root.dataset.focusTerminal='false';root.dataset.navOpen='true';syncLayout();}));entry.append(actions);target.append(entry);});
    });
    const storage=el('details');storage.append(el('summary','Planned: storage across projects and machines','cursor-interaction'),el('p','Show project folders, Botainer project data, and shared images/caches separately. Include free space, quotas, scan time, and unavailable hosts. Never add shared data once per project.','bt-caption'),el('p','Storage inspection and cleanup are future work; this demo does not scan or delete files.','bt-caption'));target.append(storage);const actions=el('div',undefined,'bt-actions');actions.append(button('Back to workspace',closeFlow));target.append(actions);
  }
  function openInstance(id){const instance=demo.getInstallation(id);const target=beginFlow('instance',instance.name+' config',demo.getHost(instance.hostId).name+' · '+instance.stateRoot);renderTextConfig(target,'installation',instance);if(instance.hostId==='cluster'){const extra=el('details');extra.append(el('summary','Cluster profile · cluster.yaml','cursor-interaction'),el('p','The connected app will expose this separate file with its own validation. Scheduler account, partition, paths and time limits belong here. This demo edits only instance policy.','bt-caption'));target.append(extra);}target.append(el('p','Administrator policy at /etc/botainer/policy.yaml is shown as inherited constraints in the connected app.','bt-caption'),button('Back to machines',openMachines));}
  function syncLayout(){const nav=root.querySelector('[data-action="toggle-nav"]'),focus=root.querySelector('[data-action="focus-terminal"]');const shown=root.dataset.focusTerminal!=='true'&&(root.dataset.navOpen===undefined?!window.matchMedia('(max-width:760px)').matches:root.dataset.navOpen==='true');root.querySelector('.bt-main').inert=shown&&window.matchMedia('(max-width:760px)').matches;nav.setAttribute('aria-expanded',String(shown));nav.textContent=shown?'Hide projects':'Projects';focus.setAttribute('aria-pressed',String(root.dataset.focusTerminal==='true'));focus.textContent=root.dataset.focusTerminal==='true'?'Exit terminal focus':'Terminal focus';}
  slot('host-filter').addEventListener('change',event=>{ui.hostFilter=event.target.value;ui.installationFilter='all';renderSidebar();remember();});
  slot('installation-filter').addEventListener('change',event=>{ui.installationFilter=event.target.value;renderSidebar();});
  slot('project-search').addEventListener('input',event=>{ui.query=event.target.value;renderSidebar();});
  slot('project-sort').addEventListener('change',event=>{ui.sort=event.target.value;renderSidebar();});
  slot('sidebar-width').addEventListener('input',event=>{root.style.setProperty('--bt-sidebar-width',event.target.value+'px');const out=slot('sidebar-width-value');if(out)out.textContent=event.target.value+' px';});
  root.querySelector('[data-action="toggle-nav"]').addEventListener('click',()=>{const shown=root.querySelector('[data-action="toggle-nav"]').getAttribute('aria-expanded')==='true';root.dataset.focusTerminal='false';root.dataset.navOpen=String(!shown);syncLayout();});
  root.querySelector('[data-action="focus-terminal"]').addEventListener('click',()=>{root.dataset.focusTerminal=String(root.dataset.focusTerminal!=='true');root.querySelector('.bt-view-options').open=false;ui.flow=null;ui.tab='terminal';render();});
  window.matchMedia('(max-width:760px)').addEventListener('change',()=>{delete root.dataset.navOpen;syncLayout();});
  root.querySelectorAll('[data-tab]').forEach(tab=>tab.addEventListener('click',()=>{ui.tab=tab.dataset.tab;if(ui.tab!=='terminal')root.dataset.focusTerminal='false';render();notice();remember();}));
  root.querySelectorAll('[data-tab]').forEach((tab,index,tabs)=>tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();}));
  root.querySelector('[data-action="new-session"]').addEventListener('click',()=>openNewSession());
  root.querySelector('[data-action="new-project"]').addEventListener('click',openNewProject);
  root.querySelector('[data-action="machines"]').addEventListener('click',openMachines);
  root.querySelector('[data-action="reset"]').addEventListener('click',()=>{demo=globalThis.BotainerDemo.createDemo();Object.assign(ui,{projectId:'p-dashboard',sessionId:'s-local',tab:'terminal',hostFilter:'all',installationFilter:'all',query:'',sort:'recent',flow:null,transcriptCount:8,inputs:{},configScope:'project',configMode:'quick',filePaths:{}});slot('project-search').value='';slot('project-sort').value='recent';delete root.dataset.focusTerminal;delete root.dataset.navOpen;render();notice('Demo reset to its initial sample data.');remember();});
  window.addEventListener('openai:set_globals',event=>{restore(event.detail?.globals?.widgetState);render();});
  restore(window.openai?.widgetState);render();
  if(globalThis.Tweak){const tweak=new Tweak({container:root,onChange:()=>{render();remember();}});tweak.addSelect(design,'groupBy',{label:'Group sidebar by',options:['Project','Machine']});tweak.addToggle(design,'showPaths',{label:'Show project paths'});}
})();
