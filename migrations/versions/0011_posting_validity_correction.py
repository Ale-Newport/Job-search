"""Separate legacy publication-validity dates from actual application deadlines."""
import json
import re
from datetime import datetime,timezone

from alembic import op

revision = '0011_posting_validity_correction'
down_revision = '0010_discovery_observation_index'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    rows = bind.exec_driver_sql("SELECT id,url,canonical_url,description,metadata,application_deadline,deadline_source FROM jobs WHERE application_deadline IS NOT NULL").mappings().all()
    for row in rows:
        metadata = json.loads(row['metadata'] or '{}')
        validity = metadata.get('valid_through')
        if not validity or metadata.get('application_deadline') or row['deadline_source'] not in (None,row['url'],row['canonical_url']):
            continue
        if re.search(r'application deadline|closing date|applications close',row['description'] or '',re.I):
            continue
        try:
            published = datetime.fromisoformat(str(validity).replace('Z','+00:00'))
            stored = datetime.fromisoformat(row['application_deadline'].replace('Z','+00:00'))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            same = published == stored or len(str(validity))==10 and published.date()==stored.date()
        except (ValueError,TypeError):
            continue
        if same:
            metadata['legacy_posting_validity_deadline'] = row['application_deadline']
            state = 'EXPIRED' if published.date() < datetime.now(timezone.utc).date() else 'POSTING_VALIDITY'
            bind.exec_driver_sql('UPDATE jobs SET application_deadline=NULL,deadline_timezone=NULL,deadline_state=?,metadata=? WHERE id=?',
                                 (state,json.dumps(metadata,ensure_ascii=False),row['id']))


def downgrade():
    raise RuntimeError('Corrected deadline evidence and original metadata must be retained')
