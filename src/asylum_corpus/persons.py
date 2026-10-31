"""The persons the head of a BVwG decision names (the judge, the representatives), and a function that replaces them
by their roles wherever the data repeats them: in the package's section titles and printed values (stage 10) and in
the texts of the agent corpus (stage 09).
"""

from __future__ import annotations

import re


def names_of(head: dict) -> dict[str, str]:
    """The persons the head names, each with its role, longest first: the judge, and the representatives as printed.
    A name that is only the court's anonymisation ("XXXX") is no person."""
    named = [(head["judge"]["name"], "[judge]")]
    named += [(rep["printed"], f"[{rep['kind']}]") for rep in head["representation"]["representatives"]]
    real = {n: role for n, role in reversed(named) if n and len(n.strip()) > 3 and re.search(r"[^X\W]", n)}
    return dict(sorted(real.items(), key=lambda kv: -len(kv[0])))


def masker(head: dict | None):
    """A function that replaces the persons the head names by their roles, in any case and spacing."""
    patterns = [(re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, name.split())) + r"(?!\w)", re.IGNORECASE), role)
                for name, role in (names_of(head) if head else {}).items()]

    def mask(text: str) -> str:
        for pattern, role in patterns:
            text = pattern.sub(role, text)
        return text
    return mask
