"""Shared eligibility predicates for current extraction runs."""

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.content.models import ContentExtraction


def latest_extraction_clause():
    """Keep old runs addressable, but expose only the newest run in discovery."""
    newer = aliased(ContentExtraction)
    return (
        ~select(newer.id)
        .where(
            newer.file_id == ContentExtraction.file_id,
            or_(
                newer.created_at > ContentExtraction.created_at,
                (newer.created_at == ContentExtraction.created_at)
                & (newer.id > ContentExtraction.id),
            ),
        )
        .correlate(ContentExtraction)
        .exists()
    )
