"""Validate existing inbox integrations on a private database copy, never send mail."""
import argparse
import asyncio
import json
from pathlib import Path

from jobagent.db import Database
from jobagent.mail import MailService
from jobagent.security import SecretStore


async def run(args):
    if args.database.resolve().is_relative_to(Path.home() / 'Library/Application Support/Meridian'):
        raise ValueError('Inbox validation requires a separate database copy')
    if not args.read_inbox:
        raise ValueError('Reading configured inboxes requires explicit --read-inbox')
    db = Database(args.database)
    providers = db.query("SELECT provider,status FROM integrations WHERE provider IN ('gmail','outlook','imap')")
    report = {'providers': providers, 'private_copy': True, 'messages_sent': 0, 'cloud_model_calls': 0}
    try:
        async with asyncio.timeout(180):
            result = await MailService(db, SecretStore()).sync()
        report.update(imported=result.get('imported', 0), errors=result.get('errors', []))
    except TimeoutError:
        report.update(imported=None, errors=[{'error': 'Inbox validation exceeded 180 seconds; completion is unverified'}])
    report['sources'] = [dict(row) for row in db.query("SELECT name,last_checked,jobs_found,last_error FROM sources WHERE json_extract(config,'$.delivery')='email'")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    args.output.chmod(0o600)
    print(json.dumps(report), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--read-inbox', action='store_true')
    asyncio.run(run(parser.parse_args(argv)))
