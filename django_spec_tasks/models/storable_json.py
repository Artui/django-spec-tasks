from __future__ import annotations

import json
import re
from typing import Any

# An odd run of backslashes: ``\u0000`` is a NUL, and ``\\u0000`` is a backslash and some
# letters, which every database stores.
_NUL = re.compile(r"(?<!\\)(?:\\\\)*\\u0000")


def storable_json(value: Any, *, encoder: type[json.JSONEncoder] | None = None) -> str:
    """``value`` as a JSON column writes it, or a refusal of what the column cannot hold.

    ``NaN``, ``inf``, a NUL character and half a surrogate pair each fail the write on
    Postgres, and the last two pass on SQLite, so a value that passes in a test suite can fail
    only in production.

    Raises:
        TypeError: The encoder cannot encode ``value``.
        ValueError: ``value`` holds something no JSON column can.
    """
    encoded = json.dumps(value, cls=encoder, allow_nan=False)
    if _NUL.search(encoded):
        raise ValueError("JSON holding a NUL character cannot be stored on every database.")
    # A surrogate pair is common in text, as an emoji, and half of one is not, and only an
    # unescaped encode can tell them apart. Encoding twice is paid only by text that needs it.
    if "\\ud" in encoded:
        try:
            json.dumps(value, cls=encoder, ensure_ascii=False).encode()
        except UnicodeEncodeError as error:
            raise ValueError("JSON holding half a surrogate pair cannot be stored.") from error
    return encoded
