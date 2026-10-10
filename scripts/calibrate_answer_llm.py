"""Real, read-only form question calibration against an authorised copied profile.

Runs genuine configured provider calls; never opens, fills or submits an ATS.
Personal answers stay in the local ignored report directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import time

from jobagent.application_answer_llm import ApplicationAnswerLLM
from jobagent.automation.company_research import CompanyResearch
from jobagent.core import get_settings
from jobagent.db import Database
from jobagent.intelligence.semantics import interpret
from jobagent.security import SecretStore

QUESTIONS = [
    "If Palantir didn't exist, what kind of company or work would you be most excited and interested in working at/on? (Approx. 200 words)",
    "What is the hardest technical challenge you've faced as part of work experience or a personal project? (Approx. 200 words)",
    'Tell us one thing that’s not on your resume that you’re proud of.',
    'At Palantir we have two main Software Engineering roles: Forward Deployed Software Engineer and Software Engineer. Which of these roles resonates the most with your job search and why? More details found here: https://blog.palantir.com/dev-versus-delta-demystifying-engineering-roles-at-palantir-ad44c2a6e87',
    'Why do Foundry or Apollo interest you?',
    'Describe a leadership experience.',
    'Describe a difficult bug you resolved.',
    'Describe a team conflict and how you resolved it.',
    'Describe a project you built from scratch.',
    'Explain why you want to work at this company.',
]


async def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f'file:{Path(args.database).resolve()}?mode=ro', uri=True)
    destination = sqlite3.connect(output / 'llm-calibration.sqlite3')
    source.backup(destination)
    source.close()
    destination.close()
    db = Database(output / 'llm-calibration.sqlite3')
    settings = get_settings(db)
    if settings.get('text_provider') != 'ollama' and not args.allow_remote:
        raise ValueError('This calibration requires local Ollama unless --allow-remote is explicitly supplied')
    llm = ApplicationAnswerLLM(db, SecretStore())
    jobs = db.query("SELECT * FROM jobs WHERE company LIKE '%Palantir%' AND title LIKE '%Software Engineer%' ORDER BY created_at DESC")
    job = next((j for j in jobs if 'new grad' in j['title'].lower() and 'london' in j['location'].lower()), jobs[0] if jobs else None)
    if not job:
        raise ValueError('The calibration database does not contain the exact target job')
    job = dict(job)
    application = db.one('SELECT id FROM applications WHERE job_id=?', (job['id'],))
    job['application_id'] = application['id'] if application else 'calibration-local'
    job['job_id'] = job['id']
    resume = db.one("SELECT id FROM document_versions WHERE approved=1 AND text!='' ORDER BY created_at DESC LIMIT 1")
    if resume:
        job['resume_version_id'] = resume['id']
    research = CompanyResearch(output / 'company-research')
    urls = ['https://blog.palantir.com/dev-versus-delta-demystifying-engineering-roles-at-palantir-ad44c2a6e87',
            'https://www.palantir.com/platforms/foundry/', 'https://www.palantir.com/platforms/apollo/']
    sources = await asyncio.gather(*(research.source(url) for url in urls))
    job['company_context'] = [source for source in sources if source]
    report = {'genuine_provider_calls': True, 'provider': settings['text_provider'], 'model': settings['text_model'],
              'research_sources': [s['url'] for s in job['company_context']], 'unavailable_research': [url for url, s in zip(urls, sources) if not s],
              'answers': [], 'errors': [], 'no_form_fill_or_submission': True}
    report['connection'] = await llm.test(settings)
    previous = []
    for index, question in enumerate(QUESTIONS):
        started = time.monotonic()
        field = {'id': f'real-question-{index + 1}', 'type': 'textarea', 'multiline': True, '_related_answers': list(previous)}
        try:
            answer = await llm.generate(settings, question, job, field=field, question_id=field['id'], semantic=interpret(question, field))
            previous.append(answer)
            report['answers'].append(answer)
            print(json.dumps({'question': index + 1, 'intent': answer['intent'], 'state': answer['state'], 'word_count': answer['word_count'],
                              'seconds': round(time.monotonic() - started, 2), 'facts_used': len(answer['facts_used'])}), flush=True)
        except ValueError as exc:
            error = {'question_id': field['id'], 'exact_question': question, 'state': 'ANSWER_FAILED', 'error': str(exc),
                     'code': getattr(exc, 'code', 'UNKNOWN'), 'seconds': round(time.monotonic() - started, 2),
                     'diagnostics': getattr(exc, 'details', {})}
            report['errors'].append(error)
            print(json.dumps({key: value for key, value in error.items() if key != 'diagnostics'}), flush=True)
        (output / 'real-llm-regression.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report['application_review'] = llm.consistency.review(report['answers'])
    report['successful_questions'] = len(report['answers'])
    report['failed_questions'] = len(report['errors'])
    (output / 'real-llm-regression.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'successful': len(report['answers']), 'failed': len(report['errors']), 'report': str(output / 'real-llm-regression.json')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--allow-remote', action='store_true')
    asyncio.run(run(parser.parse_args()))
