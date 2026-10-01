(data => {
  globalThis.__meridianPrompt?.remove();
  const host = document.createElement('div');
  host.id = 'meridian-browser-guide';
  host.style.cssText = 'position:fixed!important;right:20px!important;top:24px!important;width:min(410px,calc(100vw - 40px))!important;z-index:2147483647!important;display:block!important;';
  const root = host.attachShadow({mode:'closed'});
  const style = document.createElement('style');
  style.textContent = `
    :host{all:initial;color-scheme:dark} *{box-sizing:border-box}
    section{font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#edf2f5;background:#17212b;border:1px solid #6dd5ba;border-radius:16px;box-shadow:0 18px 70px #0006;padding:22px;max-height:calc(100vh - 48px);overflow:auto}
    header{display:flex;justify-content:space-between;align-items:center;color:#8de0cc;font-size:12px;letter-spacing:.12em;font-weight:700} h2{font-size:18px;line-height:1.4;margin:14px 0 10px}p{color:#b8c5d0;margin:8px 0;font-size:13px}small{display:block;color:#b8c5d0;font-size:12px}
    label{display:block;margin:14px 0 7px} textarea,input,select{width:100%;font:inherit;line-height:1.5;color:#fff;background:#101820;border:1px solid #627384;border-radius:8px;padding:10px}textarea{min-height:100px;resize:vertical}input[type=checkbox]{width:auto;margin:0 8px 0 0;accent-color:#88d9c3}
    button{font:600 13px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;cursor:pointer;border:1px solid #536674;background:#253441;color:#eaf3f7;border-radius:8px;padding:10px 12px}button:disabled{opacity:.5;cursor:wait}button.primary{background:#89d8c2;color:#0c2620;border-color:#89d8c2;width:100%}.row{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}.status{white-space:pre-wrap;color:#ffd796}.privacy{font-size:11px;border-top:1px solid #354552;padding-top:12px;margin-top:15px} :focus-visible{outline:3px solid #96decf;outline-offset:2px}
  `;
  root.append(style);
  const section = document.createElement('section');
  section.setAttribute('role','dialog'); section.setAttribute('aria-label','Meridian application assistant');
  root.append(section);
  function el(tag, text, parent=section) {const n=document.createElement(tag); if(text)n.textContent=text;parent.append(n);return n;}
  el('header','✦ MERIDIAN · APPLICATION ASSISTANT');
  el('small',data.company+' · '+data.remaining+' question(s) remaining');
  el('h2',data.question);
  el('p',data.description || 'This answer is missing from your confirmed profile. Complete it here to continue.');
  el('small',data.manual ? 'Complete this step in the form, then choose Recheck form.' : data.required ? 'Required by this form' : 'Optional — you can leave it blank');
  const label=el('label','Your answer'); label.htmlFor='answer';
  let input;
  if(data.options?.length){input=el('select');el('option','Choose an answer',input).value=''; for(const option of data.options) el('option',option,input).value=option;}
  else input=el(data.multiline ? 'textarea' : 'input');
  input.id='answer'; input.setAttribute('aria-label','Your answer');input.maxLength=20000;
  if(input.tagName==='INPUT')input.type=data.type==='number'?'number':data.type==='date'?'date':'text';
  input.value=data.answer || '';
  if(data.manual){label.hidden=true;input.hidden=true;}
  let reuse;
  if(data.can_reuse){const row=el('label');reuse=el('input',null,row);reuse.type='checkbox';el('span',data.reuse_label || 'Remember for matching questions in future applications',row);}
  const status=el('p',data.message || '');status.className='status';status.setAttribute('role','status');
  const buttons=[];
  function send(action,e){
    if(!e.isTrusted)return;
    if(action==='answer' && !input.value.trim()){status.textContent='Enter an answer first, or leave this optional question blank.';return;}
    if(action==='answer' && !input.checkValidity()){input.reportValidity();return;}
    buttons.forEach(b=>b.disabled=true);status.textContent=action==='draft'?'Writing a draft from your verified experience…':'Saving…';
    globalThis[data.binding](JSON.stringify({token:data.token, action, answer:input.value, reusable:!!reuse?.checked}));
  }
  function button(text,action,parent=section,primary=false){const b=el('button',text,parent);b.type='button';if(primary)b.className='primary';b.addEventListener('click',e=>send(action,e));buttons.push(b);return b;}
  if(!data.manual)button('Save answer & continue','answer',section,true);
  const actions=el('div');actions.className='row';
  if(data.can_draft)button('Draft with AI','draft',actions);
  if(!data.required && !data.manual)button('Leave blank','skip',actions);
  const tools=el('div');tools.className='row';button('Recheck form','rescan',tools);button('Pause','pause',tools);
  el('p','Saved on this Mac. “Save answer & continue” fills this answer in '+data.company+'’s form. It does not submit your application.').className='privacy';
  root.addEventListener('keydown',e=>e.stopPropagation());root.addEventListener('input',e=>e.stopPropagation());
  root.addEventListener('click',e=>e.stopPropagation());
  document.documentElement.append(host);
  globalThis.__meridianPrompt = {
    root,
    remove(){host.remove();delete globalThis.__meridianPrompt;},
    update(update){if(update.answer!==undefined)input.value=update.answer;status.textContent=update.message || '';buttons.forEach(b=>b.disabled=false);}
  };
})(__DATA__)
