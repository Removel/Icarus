from enum import StrEnum


class PersonKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    HUMAN = "human"
    VIRTUAL = "virtual"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class Source(StrEnum):
    USER = "user"
    AGENT = "agent"
    IMPORT = "import"
    OBSERVER = "observer"


class ProfileStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class RelationDirection(StrEnum):
    ONE_WAY = "one_way"
    TWO_WAY = "two_way"


class RelationStatus(StrEnum):
    ACTIVE = "active"
    INVALID = "invalid"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    LEFT = "left"
