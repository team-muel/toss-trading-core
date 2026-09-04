from enum import StrEnum


class DataStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    UNRECONCILED = "UNRECONCILED"


class DecisionAction(StrEnum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"


class ExecutionMode(StrEnum):
    READ_ONLY = "READ_ONLY"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"
