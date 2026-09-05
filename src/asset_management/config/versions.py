import hashlib
import json
from typing import Mapping


def content_hash(values: Mapping[str, object]) -> str:
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
