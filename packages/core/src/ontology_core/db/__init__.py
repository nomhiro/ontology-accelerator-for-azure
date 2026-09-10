"""正本(PostgreSQL)へのアクセス層。"""

from ontology_core.db.engine import create_engine_and_factory, session_scope
from ontology_core.db.tables import (
    AccessEventRow,
    AuditEventRow,
    Base,
    NamespaceRoleRow,
    NamespaceRow,
    OntologyVersionRow,
    TermAccessRow,
    TermOwnerRow,
)

__all__ = [
    "AccessEventRow",
    "AuditEventRow",
    "Base",
    "NamespaceRoleRow",
    "NamespaceRow",
    "OntologyVersionRow",
    "TermAccessRow",
    "TermOwnerRow",
    "create_engine_and_factory",
    "session_scope",
]
