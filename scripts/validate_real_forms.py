"""Authorised real-form preparation; the final submission gate stays closed.

Run on a private database snapshot. Reports exclude answers, document contents,
credentials and browser storage. Every form has a bounded preparation deadline.
"""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import hashlib

from jobagent.automation import BrowserManager
from jobagent.application_profile import application_knowledge
from jobagent.automation.job_context import prepare_job_context
from jobagent.db import Database, decode_row, uid
from jobagent.intelligence.engine import CandidateEngine
from jobagent.core import get_settings


APPROVED_URLS = ['https://jobs.ashbyhq.com/trainline/2757aa18-5e60-45cf-ac9d-be11dee1afe2/application', 'https://job-boards.greenhouse.io/shifttechnology/jobs/8014397003', 'https://jobs.lever.co/mytos/bbb558c0-d769-4b5f-a3a7-025642c2626d/apply', 'https://kla.wd1.myworkdayjobs.com/en-US/search/job/Chennai-India/Associate-Software-Engineer_2639562', 'https://jobs.smartrecruiters.com/Endava/744000153660909-junior-software-engineer-c-c-linux-?oga=true', 'https://apply.workable.com/grayce/j/1789116104', 'https://www.digitalocean.com/careers/position/apply?gh_jid=8233113', 'https://jobs.ashbyhq.com/relay/16b8fb16-20a6-4153-b661-e018da9c5241/application', 'https://job-boards.greenhouse.io/shifttechnology/jobs/5910904003', 'https://jobs.lever.co/farfetch/6d86bd8d-8ae6-4e06-8156-59c13b1b4fd2']


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--only-url',action='append',default=[])
    args = parser.parse_args()
    if any(url not in APPROVED_URLS for url in args.only_url):
        raise ValueError('This destination has not been explicitly approved')
    db = Database(args.data_dir/'database/meridian.sqlite3')
    approved = db.query("SELECT v.*,d.kind FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.approved=1 ORDER BY v.created_at DESC LIMIT 1")
    documents = []
    for doc in approved:
        if hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest() != doc['content_hash']:
            raise ValueError('Approved document content hash does not match')
        target = args.data_dir/'documents'/doc['document_id']/Path(doc['path']).name
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            shutil.copy2(doc['path'],target)
        documents.append({**doc,'kind':'cv','path':str(target.resolve())})
    chosen=[]
    for url in APPROVED_URLS:
        job=db.one('SELECT * FROM jobs WHERE application_url=? OR url=? LIMIT 1',(url,url))
        if not job:
            raise ValueError('An explicitly approved form is no longer in the snapshot')
        chosen.append(decode_row(job))
    previous=json.loads(args.report.read_text()).get('forms',[]) if args.only_url and args.report.exists() else []
    by_url={record['url']:record for record in previous}
    results=[]
    if args.only_url:
        chosen=[job for job in chosen if (job['application_url'] or job['url']) in args.only_url]
    for job in chosen[:min(10,args.limit)]:
        browser=BrowserManager(args.data_dir)
        browser.profile_path=args.data_dir/'browser-profile'/'dry-run'/uid()
        browser._headless=True
        app={**job,'job_id':job['id'],'id':uid(),'mode':'review'}
        facts=db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
        facts,skipped=application_knowledge(db,app,facts)
        started=asyncio.get_running_loop().time()
        operations=[]
        browser.on_step=lambda step:operations.append(step)
        settings=get_settings(db) | {'browser_engine':'deterministic','browser_max_steps':100,'_skipped_questions':skipped,'skip_optional_unknown':True}
        try:
            async with asyncio.timeout(100):
                settings['_job_context']=await prepare_job_context(app)
                engine=CandidateEngine(db,settings=settings)
                if engine.enabled:
                    settings['_candidate_engine']=engine
                result=await browser.fill_application(app,facts,documents,'REVIEW',settings)
            record={'company':job['company'],'title':job['title'],'ats':job['ats'],'url':job['application_url'] or job['url'],
                    'status':result['status'],'form_state':result.get('form_state'),'answers_resolved':len(result.get('answers',[])),
                    'steps_completed':len(result.get('completed_steps',[])),'uploads':sum(s.get('operation')=='UPLOAD' for s in operations),
                    'upload_acknowledgements':sum(bool((s.get('upload_evidence') or {}).get('acknowledged')) for s in operations),
                    'question_labels':[q.get('question') for q in result.get('questions',[])],
                    'actions':len(operations),'attention_kinds':sorted({q['kind'] for q in result.get('questions',[])}),
                    'submitted':False,'duration_seconds':round(asyncio.get_running_loop().time()-started,2)}
        except Exception as exc:
            record={'company':job['company'],'ats':job['ats'],'url':job['application_url'] or job['url'],'status':'bounded_failure','failure_type':type(exc).__name__,'submitted':False,'duration_seconds':round(asyncio.get_running_loop().time()-started,2)}
        finally:
            await browser.close()
        results.append(record)
        by_url[record['url']]=record
        args.report.write_text(json.dumps({'scope':'Explicitly authorised preparation only; no final submission','forms':[by_url[url] for url in APPROVED_URLS if url in by_url]},indent=2)+'\n')
        print(json.dumps(record),flush=True)


if __name__=='__main__':
    asyncio.run(main())
