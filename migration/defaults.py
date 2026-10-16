"""Target defaults for a future controlled import; never writes to a source database."""


def legacy_card_defaults(source_database):
    if source_database not in {"okrug", "raion"}:
        raise ValueError("Unapproved legacy source")
    return {"card_state": "published", "publication_targets": ["internal"]}
