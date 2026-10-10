"""Reviewed public project evidence, kept separate from candidate identity and employment.

This module never fetches URLs or executes page scripts. Callers provide saved
source snapshots and explicitly reviewed facts; website labels cannot verify a
candidate fact. Every imported fact retains its source hash, URL and quotation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlparse

from .store import entity, ingest_fact, link, typed

PROJECT_CONCEPTS = {
    'description', 'contribution', 'technologies', 'repository', 'year',
    'public_implementation_details', 'public_architecture_tradeoffs',
    'public_documentation_notes', 'public_observation',
}


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)


def _page_payloads(content):
    parser = _Scripts()
    parser.feed(content)
    values = []
    decoder = json.JSONDecoder()
    for script in parser.parts:
        prefix = 'self.__next_f.push('
        if script.startswith(prefix):
            try:
                payload, _ = decoder.raw_decode(script[len(prefix):])
            except (ValueError, TypeError):
                continue
            values.extend(item for item in payload if isinstance(item, str))
        else:
            try:
                values.append(json.dumps(json.loads(script), ensure_ascii=False))
            except (ValueError, TypeError):
                continue
    return values


def portfolio_projects(content):
    """Extract published project records from a saved structured portfolio page."""
    for payload in _page_payloads(content):
        marker = '"projects":['
        if marker not in payload:
            continue
        projects, _ = json.JSONDecoder().raw_decode(payload[payload.index(marker) + len('"projects":'):])
        if not isinstance(projects, list) or len(projects) > 1000:
            raise ValueError('Invalid portfolio project collection')
        return [row for row in projects if isinstance(row, dict) and row.get('title')
                and row.get('status') == 'published' and not row.get('hidden')]
    return []


def snapshot(url, content, retrieved_at):
    _source_url(url)
    return {'url': url, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'retrieved_at': retrieved_at}


def _source_url(value):
    if not isinstance(value, str):
        raise ValueError('Public source URL must be a string')
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Public evidence requires an HTTPS source URL without embedded credentials')


def _normalized(value):
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()


class _TransactionDB:
    """Use established ingestion helpers inside one outer SQLite transaction."""
    def __init__(self, connection):
        self.connection = connection

    def one(self, sql, params=()):
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def query(self, sql, params=()):
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        return self.connection.execute(sql, params).lastrowid

    @contextmanager
    def transaction(self):
        yield self.connection


def _project_id(db, row):
    names = {str(value).casefold() for value in [row['name'], *row.get('aliases', [])]}
    candidates = [existing for existing in db.query("SELECT * FROM knowledge_entities WHERE kind='project'")
                  if names & {existing['name'].casefold(), *(str(alias).casefold() for alias in json.loads(existing['aliases']))}]
    if len(candidates) > 1:
        raise ValueError('Public evidence aliases match more than one existing project')
    return candidates[0]['id'] if candidates else entity(db, 'project', row['name'], row.get('aliases', []))


def import_public_evidence(db, bundle, *, source_priority=60):
    """Add bounded, reviewed project facts without rewriting CV or historical answers.

    bundle contains snapshots keyed by caller IDs and projects with fact specs:
    concept, value, source_id, excerpt, reviewed (defaults False), optional type.
    Higher-priority verified data remains authoritative. Unsupported identity,
    contact, employment, policy or fabricated not-on-resume flags are rejected.
    """
    if isinstance(source_priority, bool) or not isinstance(source_priority, int) or not 1 <= source_priority <= 60:
        raise ValueError('Public evidence source priority must be between 1 and 60')
    if not isinstance(bundle, dict) or bundle.get('schema_version', 1) != 1:
        raise ValueError('Unsupported public evidence bundle schema')
    sources = bundle.get('snapshots', {})
    projects = bundle.get('projects', [])
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 100 or not isinstance(projects, list) or len(projects) > 100:
        raise ValueError('Invalid public evidence bundle size')
    searchable = {}
    for key, source in sources.items():
        if not isinstance(key, str) or not isinstance(source, dict):
            raise ValueError('Public source snapshots require named objects')
        content = source.get('content')
        if not isinstance(content, str) or not content or len(content.encode()) > 2_000_000:
            raise ValueError('Public source snapshot is empty or too large')
        _source_url(source['url'])
        if hashlib.sha256(content.encode()).hexdigest() != source.get('sha256'):
            raise ValueError('Public source snapshot hash does not match its saved content')
        if not isinstance(source.get('retrieved_at'), str):
            raise ValueError('Public source retrieval time is required')
        captured = datetime.fromisoformat(source['retrieved_at'])
        if captured.tzinfo is None:
            raise ValueError('Public source retrieval time must include a timezone')
        searchable[key] = _normalized('\n'.join([content, *_page_payloads(content)]))
    prepared = []
    for row in projects:
        if not isinstance(row, dict):
            raise ValueError('Public project records must be objects')
        if not isinstance(row.get('name'), str) or not row['name'].strip() or len(row['name']) > 300:
            raise ValueError('Public project name is required')
        aliases = row.get('aliases', [])
        if not isinstance(aliases, list) or len(aliases) > 30 or any(not isinstance(alias, str) or len(alias) > 300 for alias in aliases):
            raise ValueError('Public project aliases must be bounded strings')
        specs = row.get('facts', [])
        if not isinstance(specs, list) or not 1 <= len(specs) <= 30:
            raise ValueError('Public project requires a bounded list of facts')
        for spec in specs:
            if not isinstance(spec, dict):
                raise ValueError('Public project facts must be objects')
            concept = spec.get('concept')
            if concept not in PROJECT_CONCEPTS:
                raise ValueError('Public import accepts project evidence only')
            if spec.get('source_id') not in sources:
                raise ValueError('Every public fact needs a saved source snapshot')
            excerpt = spec.get('excerpt')
            if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 12000:
                raise ValueError('Every public fact needs a bounded source excerpt')
            if _normalized(excerpt) not in searchable[spec['source_id']]:
                raise ValueError('Public fact excerpt was not found in the saved source')
            if not isinstance(spec.get('reviewed', False), bool):
                raise ValueError('Public fact review must be explicit')
            if 'value' not in spec or spec['value'] is None:
                raise ValueError('Public facts require an explicit supported value')
            value = spec['value']
            kind = spec.get('type') or ('list' if isinstance(value, list) else 'text')
            value = typed(value, kind)
            if len(json.dumps(value)) > 12000:
                raise ValueError('Public fact value is too large')
            prepared.append((row, spec, value, kind))
    report = {'projects': 0, 'verified_facts': 0, 'unverified_facts': 0, 'facts_created': 0, 'fact_ids': [], 'entity_ids': {}}
    with db.transaction() as connection:
        transaction_db = _TransactionDB(connection)
        for row, spec, value, kind in prepared:
            identifier = report['entity_ids'].get(row['name'])
            if not identifier:
                identifier = _project_id(transaction_db, row)
                report['entity_ids'][row['name']] = identifier
            source = sources[spec['source_id']]
            source_name = 'public:' + source['url'] + '#sha256=' + source['sha256'] + '&reviewed=' + str(int(spec.get('reviewed', False)))
            previous = transaction_db.one('SELECT COUNT(*) AS n FROM facts')
            fact_id = ingest_fact(transaction_db, identifier, spec['concept'], value, value_type=kind,
                source=source_name, evidence={'source': source['url'], 'url': source['url'], 'snapshot_sha256': source['sha256'],
                    'retrieved_at': source['retrieved_at'], 'excerpt': spec['excerpt'], 'reviewed': spec.get('reviewed', False),
                    'source_status': spec.get('source_status'), 'verification_basis': 'Reviewed public project documentation' if spec.get('reviewed') else 'Unreviewed public observation'},
                verified=spec.get('reviewed', False), priority=source_priority, sensitivity='professional')
            report['fact_ids'].append(fact_id)
            report['verified_facts' if spec.get('reviewed') else 'unverified_facts'] += 1
            report['facts_created'] += transaction_db.one('SELECT COUNT(*) AS n FROM facts')['n'] - previous['n']
            if spec['concept'] == 'technologies' and isinstance(value, list) and spec.get('reviewed'):
                for technology in value:
                    skill_id = entity(transaction_db, 'skill', technology)
                    link(transaction_db, skill_id, 'used_in', identifier, [fact_id])
        report['projects'] = len(report['entity_ids'])
    return report
