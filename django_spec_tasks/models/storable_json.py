from __future__ import annotations

import json
import re
from typing import Any

# An odd run of backslashes: ``\u0000`` is a NUL, and ``\\u0000`` is a backslash and some
# letters, which every database stores.
_NUL = re.compile(r"(?<!\\)(?:\\\\)*\\u0000")


def storable_json(value: Any, *, encoder: type[json.JSONEncoder] | None = None) -> str:
    """``value`` as a JSON column writes it, or a refusal of what the column cannot hold."""
    encoded = json.dumps(value, cls=encoder, allow_nan=False)
    if _NUL.search(encoded):
        raise ValueError("JSON holding a NUL character cannot be stored on every database.")
    if "\\ud" in encoded:
        try:
            json.dumps(value, cls=encoder, ensure_ascii=False).encode()
        except UnicodeEncodeError as error:
            raise ValueError("JSON holding half a surrogate pair cannot be stored.") from error
    return encoded
