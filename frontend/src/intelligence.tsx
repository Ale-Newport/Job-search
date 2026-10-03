import { useState } from 'react';
import { api, useResource } from './api';
import { Button, Editor, Panel, Resource, Tag } from './components';
import type { AppContext, Data } from './types';

export function CandidateIntelligence({ctx, coverageOnly=false}:{ctx:AppContext;coverageOnly?:boolean}) {
  const resource=useResource('/intelligence',ctx.refresh);
  const [search,setSearch]=useState('');
  const [edit,setEdit]=useState<Data|null>(null);
  const [question,setQuestion]=useState('');
  const [answer,setAnswer]=useState<Data|null>(null);
  const [asking,setAsking]=useState(false);
  const data=resource.data || {};
  const entities:Data[]=data.entities || [];
  const facts:Data[]=data.facts || [];
  const query=search.toLowerCase();
  const educationSearch=/graduat|graduaci|degree|university|education/.test(query);
  const matching=entities.filter(e=>!query || (educationSearch && e.kind==='education') || `${e.name} ${e.kind}`.toLowerCase().includes(query) || facts.some(f=>f.entity_id===e.id && `${f.concept} ${JSON.stringify(f.value)}`.toLowerCase().includes(query)));
  const linkedIds=new Set((data.relationships || []).filter((r:Data)=>matching.some(e=>e.id===r.subject_id)).map((r:Data)=>r.object_id));
  const visible=entities.filter(e=>matching.includes(e)||linkedIds.has(e.id));
  return <Panel title={coverageOnly?'Candidate Intelligence · Coverage':'Knowledge'} description={coverageOnly?'See which concepts have evidence and which still need information.':'Explore facts, relationships and derived answers. Changes remain editable and retain their history.'}>
    <Resource {...resource} retry={resource.reload}>
      {coverageOnly ? <><p className="panel-copy">{data.coverage_note}</p><div className="application-profile-grid">{(data.coverage || []).map((row:Data)=><article className="application-profile-field" key={row.category}><h4>{row.category} · {row.percentage}%</h4><progress max="100" value={row.percentage} aria-label={`${row.category} coverage`}/><p>{row.supported} / {row.total} diagnostic concepts supported</p>{row.missing.map((q:string)=><p className="muted" key={q}>{q}</p>)}</article>)}</div></> : <>
        <div className="application-profile-intro"><input aria-label="Search knowledge" placeholder="Search Python, graduation, projects…" value={search} onChange={e=>setSearch(e.target.value)}/><span>As of {data.as_of}</span></div>
        <div className="application-profile-grid">{visible.map(e=><article className="application-profile-field" key={e.id}><h4>{e.name}</h4><small>{e.kind}</small>{facts.filter(f=>f.entity_id===e.id).map(f=><div key={f.id} className="knowledge-fact"><strong>{String(f.concept).replaceAll('_',' ')}</strong> <Tag tone={f.verification_status==='verified'?'green':'amber'}>{f.verification_status==='verified'?'VERIFIED':'INFERRED'}</Tag><p>{Array.isArray(f.value)?f.value.join(', '):String(f.value)}</p><details><summary>Evidence</summary><p>{f.source}</p><p>{f.evidence?.excerpt}</p></details><Button secondary onClick={()=>setEdit(f)}>Edit fact</Button></div>)}</article>)}</div>
        <div className="simple-list">{(data.derived || []).map((a:Data)=><div key={a.question}><div><strong>{a.question}</strong><p>{a.answer || 'Not enough evidence'}</p><Tag tone="amber">DERIVED</Tag><details><summary>Why?</summary><p>{a.reasoning_summary}</p></details></div></div>)}</div>
        {(data.policies || []).map((p:Data)=><p key={p.id}><Tag>POLICY</Tag> {p.subject.replaceAll('_',' ')}: {JSON.stringify(p.value)} <Button secondary onClick={()=>setEdit({...p,policy:true,concept:p.subject,value_type:'text',verification_status:p.confirmed?'verified':'unverified',value:typeof p.value==='object'?p.value.target:p.value})}>Edit policy</Button></p>)}
        <form onSubmit={async e=>{e.preventDefault();setAsking(true);try{setAnswer(await api('/intelligence/answer','POST',{question}));}catch(error){setAnswer({reason:String(error)});}finally{setAsking(false);}}}><label>Try a question<input value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Highest completed degree?"/></label><Button type="submit" loading={asking}>Explain answer</Button></form>
        {answer && <article className="application-profile-field"><Tag>{answer.confidence_level || 'UNKNOWN'}</Tag><p>{answer.answer || 'No supported answer'}</p><small>{answer.canonical_intent} · {answer.reason}</small></article>}
      </>}
      {!!data.conflicts?.length && <details><summary>{data.conflicts.length} evidence conflicts</summary>{data.conflicts.map((c:Data,i:number)=><p key={i}>{c.entity} · {c.concept}: {c.reason}</p>)}</details>}
      {edit && <Editor title={edit.concept.replaceAll('_',' ')} initial={{value:Array.isArray(edit.value)?edit.value.join('\n'):typeof edit.value==='boolean'?(edit.value?'Yes':'No'):String(edit.value),confirmed:edit.verification_status==='verified'}} fields={[{key:'value',label:'Value',type:edit.value_type==='date'?'date':edit.value_type==='list'?'textarea':edit.value_type==='number' || edit.value_type==='integer'?'number':'text'},{key:'confirmed',label:'Confirmed — allow relevant application use',type:'checkbox'}]} onClose={()=>setEdit(null)} onSave={async values=>{await api(`/intelligence/${edit.policy?'policies':'facts'}/${edit.id}`,'PUT',values);setEdit(null);await ctx.act('knowledge-refresh',async()=>({}),'Knowledge updated.');}}/>}
    </Resource>
  </Panel>;
}
