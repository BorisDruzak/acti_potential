"""Registry-scoped reference maintenance; readers never acquire write access."""

from app.api.errors import fail
from app.registry.archives import is_archived
from app.registry.policy import permissions_for


def can_manage_reference(session, actor, registry_id):
    if actor.scoped_operations is not None:
        return False
    if 'admin' in actor.roles:
        return True
    return bool('editor' in actor.roles and registry_id is not None
                and not is_archived(session, 'registry', registry_id)
                and {'read', 'update'} <= permissions_for(session, actor, registry_id))


def require_reference_manager(session, actor, registry_id):
    if not can_manage_reference(session, actor, registry_id):
        fail(403, 'forbidden', 'Недостаточно прав для изменения справочника')


def editor_item_values(actor, book, data, *, previous=None):
    values = data.model_dump(exclude_unset=previous is not None)
    if 'admin' in actor.roles:
        return values
    if data.model_fields_set - {'label', 'description', 'active', 'metadata'}:
        fail(403, 'forbidden', 'Технические свойства изменяет администратор')
    if 'metadata' in values:
        from app.registry.references import BUSINESS_METADATA, PEOPLE_CODES

        metadata = values['metadata']
        if not isinstance(metadata, dict) or set(metadata) - set(BUSINESS_METADATA) or (metadata and book.code not in PEOPLE_CODES):
            fail(403, 'forbidden', 'Служебные сведения изменяет администратор')
        values['metadata'] = {**(previous.item_metadata if previous else {}), **metadata}
    return values
