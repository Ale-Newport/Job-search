"""Validate immutable attachments before a bounded, independently checked upload."""
import mimetypes
from pathlib import Path

from .types import InvalidDecision


def validate_upload(path, filename, mime, accept='', max_bytes=50*1024**2):
    path = Path(path)
    size = path.stat().st_size
    if size <= 0 or size > max_bytes:
        raise InvalidDecision('Attachment is empty or exceeds the upload size limit')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.txt', '.doc', '.rtf', '.jpg', '.jpeg', '.png'}:
        raise InvalidDecision('Unsupported application attachment format')
    mime = mime or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    tokens = [x.strip().lower() for x in accept.split(',') if x.strip()]
    if tokens and not any(x == suffix or x == mime.lower() or (x.endswith('/*') and mime.startswith(x[:-1])) for x in tokens):
        raise InvalidDecision('Attachment format is not accepted by this field')
    return size


async def verify_upload(node, filename):
    # Selection is evidence for native multipart forms. Asynchronous widgets must
    # finish and expose a successful attachment before the worker advances.
    state = await node.evaluate('''(node, filename) => {
        const region = node.closest('[data-field-path],.ashby-application-form-field-entry,.form-group,fieldset') || node.parentElement;
        const text = region?.innerText || '';
        const selected = [...node.files || []].some(f => f.name === filename && f.size > 0);
        const busy = !!region?.querySelector('[aria-busy="true"],[role="progressbar"]') || /uploading|processing|parsing/i.test(text);
        const errors = [...region?.querySelectorAll('[role="alert"],.error,.field-error') || []].map(e => e.innerText).filter(Boolean);
        return {selected, busy, errors, acknowledged: text.includes(filename)};
    }''', filename)
    if state['errors']:
        raise InvalidDecision('The website rejected the attachment: ' + '; '.join(state['errors'])[:160])
    return state
