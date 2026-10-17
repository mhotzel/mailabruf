# SPDX-License-Identifier: GPL-2.0-only
"""Decoding of IMAP folder names in modified UTF-7 (RFC 3501, section 5.1.3)."""

import base64
import binascii


def decode(name: str) -> str:
    """Decode a modified UTF-7 folder name, e.g. ``Entw&APw-rfe`` -> ``Entwürfe``.

    Raises ValueError if the name is not valid modified UTF-7.
    """
    out = []
    i = 0
    while i < len(name):
        char = name[i]
        if char != "&":
            out.append(char)
            i += 1
            continue
        end = name.find("-", i)
        if end == -1:
            raise ValueError(f"unterminated shift sequence in {name!r}")
        if end == i + 1:
            out.append("&")
        else:
            chunk = name[i + 1 : end].replace(",", "/")
            chunk += "=" * (-len(chunk) % 4)
            try:
                out.append(base64.b64decode(chunk, validate=True).decode("utf-16-be"))
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError(f"invalid modified UTF-7 in {name!r}") from exc
        i = end + 1
    return "".join(out)
