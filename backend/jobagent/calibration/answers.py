"""Real, read-only form question calibration against an authorised copied profile.

Runs genuine configured provider calls; never opens, fills or submits an ATS.
Personal answers stay in the local ignored report directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone

from jobagent.application_answer_llm import ApplicationAnswerLLM, provider_config
from jobagent.automation.company_research import CompanyResearch
from jobagent.core import get_settings
from jobagent.db import Database
from jobagent.intelligence.semantics import interpret
from jobagent.security import SecretStore


def calibration_usage(db):
    row = db.one('SELECT value FROM settings WHERE key=?', ('ai_usage:' + datetime.now(timezone.utc).strftime('%Y-%m'),))
    return json.loads(row['value']) if row else {'cost': 0, 'calls': 0, 'input_tokens': 0, 'output_tokens': 0}


def configure_from_live(db, live_db):
    """Copy provider settings and numeric prices, never credentials or profile data."""
    settings = get_settings(live_db)
    for key, value in settings.items():
        if key.startswith('text_') or key == 'monthly_budget':
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(value)))
    provider = settings.get('text_provider')
    row = live_db.one('SELECT config FROM integrations WHERE provider=?', (provider,))
    config = json.loads(row['config']) if row else {}
    prices = {key: config[key] for key in ('input_cost_per_million', 'output_cost_per_million') if key in config}
    rate_keys = ('input_cost_per_million', 'output_cost_per_million',
                 'cached_input_cost_per_million', 'cache_write_cost_per_million')
    required_keys = rate_keys[:2]
    bound_prices = config.get('answer_model_prices', {})
    if isinstance(bound_prices, dict):
        prices['answer_model_prices'] = {
            model: {key: rates[key] for key in rate_keys if key in rates
                    and not isinstance(rates[key], bool) and isinstance(rates[key], (int, float))
                    and math.isfinite(rates[key]) and rates[key] >= 0} for model, rates in bound_prices.items()
            if isinstance(rates, dict) and all(not isinstance(rates.get(key), bool) and isinstance(rates.get(key), (int, float))
                                              and math.isfinite(rates[key]) and rates[key] >= 0 for key in required_keys)
        }
    if provider and provider != 'none':
        from jobagent.db import uid
        db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?) ON CONFLICT(provider) DO UPDATE SET config=excluded.config',
                   (uid(), provider, json.dumps(prices), 'configured'))
    return get_settings(db)

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
    usage_db = db
    if args.usage_database:
        usage_path = Path(args.usage_database).resolve()
        if not usage_path.is_file() or usage_path == (output / 'llm-calibration.sqlite3').resolve():
            raise ValueError('--usage-database must point at the existing shared live database, not this calibration copy')
        usage_db = Database(usage_path)
        configure_from_live(db, usage_db)
    settings = get_settings(db)
    _, _, local = provider_config(settings)
    if not local and not args.allow_remote:
        raise ValueError('Remote calibration requires explicit --allow-remote authorization')
    if not local and not args.usage_database:
        raise ValueError('Remote calibration requires --usage-database pointing at the shared live database so QA copies cannot reset aggregate spending')
    llm = ApplicationAnswerLLM(db, SecretStore(), usage_db=usage_db)
    job = db.one('SELECT * FROM jobs WHERE id=?', (args.job,))
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
    report_path = output / 'real-llm-regression.json'
    selected_questions = getattr(args, 'question', None)
    report = {'genuine_provider_calls': False, 'provider': settings['text_provider'], 'model': settings['text_model'],
              'selected_question_numbers': selected_questions or list(range(1, len(QUESTIONS) + 1)),
              'job': {key: job.get(key) for key in ('id', 'title', 'company', 'location', 'url', 'application_url')},
              'usage_ledger': str(Path(args.usage_database).resolve()) if args.usage_database else str(output / 'llm-calibration.sqlite3'),
              'usage_before': calibration_usage(usage_db),
              'research_sources': [s['url'] for s in job['company_context']], 'unavailable_research': [url for url, s in zip(urls, sources) if not s],
              'answers': [], 'errors': [], 'no_form_fill_or_submission': True}
    print(json.dumps({'stage': 'connecting', 'provider': settings['text_provider'], 'model': settings['text_model']}), flush=True)
    try:
        report['connection'] = await llm.test(settings)
    except ValueError as exc:
        report['connection_error'] = {'code': getattr(exc, 'code', 'UNKNOWN'), 'error': str(exc)}
        report['usage_after'] = calibration_usage(usage_db)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        raise
    report['genuine_provider_calls'] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'stage': 'connected', 'latency_ms': report['connection']['latency_ms']}), flush=True)
    previous = []
    for index, question in enumerate(QUESTIONS):
        if selected_questions and index + 1 not in selected_questions:
            continue
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
                     'requires_human_input': 'MISSING_CANDIDATE_FACT:' in str(exc),
                     'diagnostics': getattr(exc, 'details', {})}
            report['errors'].append(error)
            print(json.dumps({key: value for key, value in error.items() if key != 'diagnostics'}), flush=True)
        (output / 'real-llm-regression.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    report['application_review'] = llm.consistency.review(report['answers'])
    report['successful_questions'] = len(report['answers'])
    report['failed_questions'] = len(report['errors'])
    report['missing_input_questions'] = sum(error['requires_human_input'] for error in report['errors'])
    report['rejected_or_provider_failed_questions'] = report['failed_questions'] - report['missing_input_questions']
    report['usage_after'] = calibration_usage(usage_db)
    (output / 'real-llm-regression.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'successful': len(report['answers']), 'failed': len(report['errors']), 'report': str(output / 'real-llm-regression.json')}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--job', required=True, help='Exact stored target job ID; matching titles never substitute another vacancy')
    parser.add_argument('--question', action='append', type=int, choices=range(1, len(QUESTIONS) + 1),
                        help='Replay a specific original question number; repeat to select several. Default runs all ten.')
    parser.add_argument('--allow-remote', action='store_true')
    parser.add_argument('--usage-database', help='Existing live database whose nonsecret provider settings and shared monthly usage ledger are used')
    asyncio.run(run(parser.parse_args(argv)))
