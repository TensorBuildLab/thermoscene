"""Minimal, dependency-free IDF text handling: list, remove and append objects.

Used to prepare a user's model for simulation (output requests, run periods, injected faults)
while leaving everything else untouched. Works on IDFs that still contain HVACTemplate objects.
"""
from __future__ import annotations

import re


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("!", 1)[0] for line in text.splitlines())


class IDFText:
    def __init__(self, text: str) -> None:
        self.objects: list[list[str]] = []
        for chunk in _strip_comments(text).split(";"):
            fields = [f.strip() for f in chunk.split(",")]
            if fields and fields[0]:
                self.objects.append(fields)

    # EnergyPlus requires UTF-8 names. Reading UTF-8 with surrogateescape keeps any other byte of the user's file
    # unchanged on write, while text appended here (fault objects named after model objects) is written as UTF-8.
    ENCODING = dict(encoding="utf-8", errors="surrogateescape")

    @classmethod
    def read(cls, path) -> "IDFText":
        with open(path, **cls.ENCODING) as f:
            return cls(f.read())

    def classes(self) -> set[str]:
        return {o[0].upper() for o in self.objects}

    def find(self, cls: str) -> list[list[str]]:
        return [o for o in self.objects if o[0].upper() == cls.upper()]

    def find_named(self, cls: str, name: str) -> list[str] | None:
        return next((o for o in self.find(cls) if len(o) > 1 and o[1].upper() == name.upper()), None)

    def remove(self, cls: str, name: str | None = None) -> int:
        before = len(self.objects)
        self.objects = [o for o in self.objects
                        if not (o[0].upper() == cls.upper() and (name is None or (len(o) > 1 and o[1].upper() == name.upper())))]
        return before - len(self.objects)

    def append(self, raw: str) -> None:
        """Append one or more objects given as IDF text."""
        self.objects.extend(IDFText(raw).objects)

    def version(self) -> str:
        v = self.find("Version")
        return v[0][1] if v else ""

    def text(self) -> str:
        out = []
        for o in self.objects:
            out.append(o[0] + ",\n" + ",\n".join("    " + f for f in o[1:]) + ";\n" if len(o) > 1 else o[0] + ";\n")
        return "\n".join(out)

    def write(self, path) -> None:
        with open(path, "w", **self.ENCODING) as f:
            f.write(self.text())


def run_period(name: str, start: str, end: str, start_dow: str = "") -> str:
    """RunPeriod object; an empty start weekday lets EnergyPlus use its default (Sunday when no year is given)."""
    m0, d0 = (int(x) for x in re.split(r"[-/]", start))
    m1, d1 = (int(x) for x in re.split(r"[-/]", end))
    return f"RunPeriod, {name}, {m0}, {d0}, , {m1}, {d1}, , {start_dow}, No, No, No, No, No, No;"
