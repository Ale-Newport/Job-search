"""Keep prior answers as history, separate from the current application draft."""
from .db import now


def supersede_answers(connection, application_id, question, question_id=None):
    connection.execute(
        'INSERT OR IGNORE INTO superseded_application_answers(answer_id,superseded_at) '
        'SELECT id,? FROM application_answers WHERE application_id=? AND question=?' +
        (' AND (NOT EXISTS(SELECT 1 FROM application_answer_targets WHERE answer_id=application_answers.id) OR EXISTS(SELECT 1 FROM application_answer_targets WHERE answer_id=application_answers.id AND question_id=?))' if question_id else ''),
        (now(), application_id, question, question_id) if question_id else (now(), application_id, question),
    )
