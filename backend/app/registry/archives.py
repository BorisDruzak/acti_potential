"""Reversible visibility metadata; published configuration and history stay intact."""

from sqlalchemy import and_, exists

from app.registry.models import ConfigurationArchive


def configuration_active_clause(model, kind, identity=None):
    identity = model.id if identity is None else identity
    clauses = [~exists().where(
        ConfigurationArchive.object_type == kind, ConfigurationArchive.object_id == identity,
    )]
    if kind in {"reference_set", "number_series"}:
        clauses.append(~exists().where(
            ConfigurationArchive.object_type == "registry",
            ConfigurationArchive.object_id == model.registry_id,
        ))
    return and_(*clauses)


def is_archived(session, kind, identity):
    if session.get(ConfigurationArchive, (kind, identity)) is not None:
        return True
    if kind in {"reference_set", "number_series"}:
        if kind == "reference_set":
            from app.registry.models import ReferenceSet

            model = ReferenceSet
        else:
            from app.numbering.models import NumberSeries

            model = NumberSeries
        book = session.get(model, identity)
        return bool(book is not None and book.registry_id is not None and is_archived(session, "registry", book.registry_id))
    return False
