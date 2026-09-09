"""正本(PostgreSQL)へのアクセス層。"""

from ontology_core.db.engine import create_engine_and_factory, session_scope
from ontology_core.db.tables import (
    AuditEventRow,
    Base,
    NamespaceRoleRow,
    NamespaceRow,
    OntologyVersionRow,
    TermOwnerRow,
)

__all__ = [
    "AuditEventRow",
    "Base",
    "NamespaceRoleRow",
    "NamespaceRow",
    "OntologyVersionRow",
    "TermOwnerRow",
    "create_engine_and_factory",
    "session_scope",
]
