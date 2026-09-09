"""Incremental snapshots of consumed inputs without retaining every full prefix.

Each delta is against the preceding input snapshot. Values and instrument orders
are still exactly replayable; data-source approval is not inferred from a hash.
"""
from copy import deepcopy
from collections.abc import Iterator
import json


MAX_JOURNAL_BYTES = 256 * 1024 * 1024


def _changes(old, new, path=()):
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() - new.keys()):
            yield ["remove", [*path, key]]
        for key in sorted(new):
            if key not in old:
                yield ["set", [*path, key], deepcopy(new[key])]
            else:
                yield from _changes(old[key], new[key], (*path, key))
    elif isinstance(old, list) and isinstance(new, list):
        for index in range(min(len(old), len(new))):
            yield from _changes(old[index], new[index], (*path, index))
        if len(new) > len(old):
            yield ["append", list(path), deepcopy(new[len(old):])]
        elif len(new) < len(old):
            yield ["truncate", list(path), len(new)]
    elif type(old) is not type(new) or old != new:
        yield ["set", list(path), deepcopy(new)]


class InputJournal:
    """One current panel plus sparse changes, not a list of expanding panels."""
    def __init__(self):
        self.previous = {}
        self.deltas = []
        self.encoded_bytes = 0

    def append(self, snapshot):
        delta = list(_changes(self.previous, snapshot))
        self.encoded_bytes += len(json.dumps(delta, allow_nan=False, separators=(",", ":")).encode())
        if self.encoded_bytes > MAX_JOURNAL_BYTES:
            raise ValueError("research input journal exceeds bounded evidence budget")
        self.deltas.append(delta)
        self.previous = snapshot

    def finish(self):
        self.previous = {}
        return self.deltas


def iter_snapshots(deltas) -> Iterator[dict]:
    """Replay one independent snapshot at a time; callers need not retain them."""
    current = {}
    for delta in deltas:
        if not isinstance(delta, list):
            raise ValueError("invalid input delta")
        for change in delta:
            if not isinstance(change, list) or len(change) not in (2, 3):
                raise ValueError("invalid input change")
            operation, path = change[:2]
            if not isinstance(path, list) or any(type(key) not in (str, int) for key in path):
                raise ValueError("invalid input change path")
            if operation == "set" and not path:
                if len(change) != 3 or not isinstance(change[2], dict):
                    raise ValueError("invalid root snapshot")
                current = deepcopy(change[2])
                continue
            target = current
            for key in path[:-1]:
                target = target[key]
            if operation == "set" and len(change) == 3 and path:
                target[path[-1]] = deepcopy(change[2])
            elif operation == "remove" and len(change) == 2 and path:
                del target[path[-1]]
            elif operation in {"append", "truncate"} and len(change) == 3:
                target = target[path[-1]] if path else target
                if not isinstance(target, list):
                    raise ValueError("input change requires a list")
                if operation == "append" and isinstance(change[2], list):
                    target.extend(deepcopy(change[2]))
                elif operation == "truncate" and type(change[2]) is int and 0 <= change[2] <= len(target):
                    del target[change[2]:]
                else:
                    raise ValueError("invalid list change")
            else:
                raise ValueError("unknown input change")
        yield deepcopy(current)
