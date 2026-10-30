"""Compare two paper_numbers.json files (e.g. committed vs. freshly computed on another machine).

usage: python case_studies/compare_numbers.py <reference.json> <new.json> [--tol 0.15]
Compares the temperatures and sizes of case studies B and C, the pixel-provenance shares and the element counts.
Values are rounded to 0.1 in the files, so the default tolerance allows one rounding step. Run times and file sizes
(section "cost") are machine dependent and are not compared. Exit code 1 on any difference.
"""
import json
import sys


def walk(a, b, path, tol, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'reference' if k in a else 'new'}")
            else:
                walk(a[k], b[k], f"{path}/{k}", tol, out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", tol, out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(a - b) > tol:
            out.append(f"{path}: {a} -> {b}")
    elif a != b:
        out.append(f"{path}: {a!r} -> {b!r}")


def main():
    ref, new = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
    tol = float(sys.argv[sys.argv.index("--tol") + 1]) if "--tol" in sys.argv else 0.15
    for d in (ref, new):                                       # machine- and run-dependent fields
        for c in d.get("counts", {}).values():
            c.pop("energyplus", None)
    out = []
    for key in ("B", "C", "provenance_share_percent", "counts"):
        walk(ref.get(key), new.get(key), key, tol, out)
    print("\n".join(out) if out else f"paper numbers agree (tolerance {tol})")
    sys.exit(1 if out else 0)


if __name__ == "__main__":
    main()
