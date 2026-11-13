"""Status and tag colours. The board puts them into CSS, so only a hex colour (`#rgb`, `#rgba`,
`#rrggbb`, `#rrggbbaa`) or a plain colour name (`red`, `SlateGray`) is accepted."""

from __future__ import annotations

import re

from slate.service.errors import Invalid

_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|[a-zA-Z]{3,20}")


def clean_color(value: str | None) -> str | None:
    """The colour as given, None for none (or ""), or Invalid for anything else."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise Invalid("a colour is a hex colour like #3b82f6 or a colour name like red")
    return value
