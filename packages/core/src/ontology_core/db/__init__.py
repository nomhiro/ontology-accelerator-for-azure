"""正本(PostgreSQL)へのアクセス層。"""

from ontology_core.db.collation import IDENTIFIER_COLLATION, by_identifier
from ontology_core.db.engine import create_engine_and_factory, session_scope
from ontology_core.db.tables import (
    AccessEventRow,
    AuditEventRow,
    Base,
    CompetencyQuestionSetRow,
    NamespaceRoleRow,
    NamespaceRow,
    OntologyVersionRow,
    ScanColumnRow,
    ScanRunRow,
    ScanSourceRow,
    ScanTableRow,
    TermAccessRow,
    TermEmbeddingRow,
    TermMappingRow,
    TermOwnerRow,
    VkgMappingRow,
)

__all__ = [
    "IDENTIFIER_COLLATION",
    "AccessEventRow",
    "AuditEventRow",
    "Base",
    "CompetencyQuestionSetRow",
    "NamespaceRoleRow",
    "NamespaceRow",
    "OntologyVersionRow",
    "ScanColumnRow",
    "ScanRunRow",
    "ScanSourceRow",
    "ScanTableRow",
    "TermAccessRow",
    "TermEmbeddingRow",
    "TermMappingRow",
    "TermOwnerRow",
    "VkgMappingRow",
    "by_identifier",
    "create_engine_and_factory",
    "session_scope",
]
