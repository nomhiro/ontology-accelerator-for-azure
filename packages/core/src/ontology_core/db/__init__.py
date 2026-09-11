"""正本(PostgreSQL)へのアクセス層。"""

from ontology_core.db.engine import create_engine_and_factory, session_scope
from ontology_core.db.tables import (
    AccessEventRow,
    AuditEventRow,
    Base,
    CompetencyQuestionSetRow,
    NamespaceRoleRow,
    NamespaceRow,
    OntologyVersionRow,
    TermAccessRow,
    TermMappingRow,
    TermOwnerRow,
)

__all__ = [
    "AccessEventRow",
    "AuditEventRow",
    "Base",
    "CompetencyQuestionSetRow",
    "NamespaceRoleRow",
    "NamespaceRow",
    "OntologyVersionRow",
    "TermAccessRow",
    "TermMappingRow",
    "TermOwnerRow",
    "create_engine_and_factory",
    "session_scope",
]
