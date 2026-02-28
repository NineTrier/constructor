#!/usr/bin/env python3
"""
Check UTF-8 BOM presence and detect common mojibake patterns.

Default scan roots:
  - taskmanager/templates
  - taskmanager/static/js
  - taskmanager/static/css

Exit code:
  0 - no problems
  1 - encoding/mojibake issues found (or fixed with --fix)
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


DEFAULT_ROOTS = (
    "taskmanager/templates",
    "taskmanager/static/js",
    "taskmanager/static/css",
)
DEFAULT_EXTS = (".html", ".js", ".css")
BOM = b"\xef\xbb\xbf"

# Typical mojibake (UTF-8 decoded as cp1251 or latin-1) patterns.
# cp1251 pattern targets non-Russian symbols that often appear in mojibake
# chains, e.g. "\u0420\u00B0", "\u0421\u201A", "\u0420\u040E".
_CP1251_NOISE = (
    "\u0403\u0409\u040A\u040B\u040F\u0452\u0453\u2026\u20AC\u2122"
    "\u0459\u045A\u045B\u045F\u040E\u045E\u0408\u00A4\u0490\u00A6"
    "\u00A7\u00A9\u0404\u00AB\u00AC\u00AE\u0407\u00B0\u00B1\u00B2"
    "\u00B3\u00B5\u00B6\u00B7\u2116\u0454\u00BB\u0458\u0405\u0455\u0457"
)
MOJIBAKE_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(rf"[\u0420\u0421][{re.escape(_CP1251_NOISE)}]"),
    re.compile(r"\u00D0[\u0080-\u00FF]"),
    re.compile(r"\u00D1[\u0080-\u00FF]"),
)


@dataclass
class Issue:
    path: pathlib.Path
    kind: str
    details: str


def _iter_files(roots: Iterable[pathlib.Path], extensions: Sequence[str]) -> Iterable[pathlib.Path]:
    ext_set = {ext.lower() for ext in extensions}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in ext_set:
                yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in ext_set:
                lower_name = path.name.lower()
                if ".min." in lower_name:
                    continue
                if any(part.lower() in {"admin", "vendor"} for part in path.parts):
                    continue
                yield path


def _mojibake_score(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in MOJIBAKE_PATTERNS)


def _try_fix(text: str) -> Optional[str]:
    base_score = _mojibake_score(text)
    if base_score == 0:
        return None

    candidates: List[str] = []
    for source_encoding in ("cp1251", "latin-1"):
        try:
            candidates.append(text.encode(source_encoding, errors="strict").decode("utf-8", errors="strict"))
        except UnicodeError:
            continue

    best_text: Optional[str] = None
    best_score = base_score
    for candidate in candidates:
        candidate_score = _mojibake_score(candidate)
        if candidate_score < best_score:
            best_score = candidate_score
            best_text = candidate
    return best_text


def check_file(path: pathlib.Path, check_bom: bool, fix: bool) -> Tuple[List[Issue], bool]:
    issues: List[Issue] = []
    changed = False
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM)

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(Issue(path, "decode_error", str(exc)))
        return issues, changed

    if check_bom and not has_bom:
        issues.append(Issue(path, "missing_bom", "File must be UTF-8 with BOM"))
        if fix:
            has_bom = True
            changed = True

    score = _mojibake_score(text)
    if score > 0:
        issues.append(Issue(path, "mojibake", f"suspicious_patterns={score}"))
        if fix:
            fixed = _try_fix(text)
            if fixed is not None and _mojibake_score(fixed) < score:
                text = fixed
                changed = True

    if fix and changed:
        path.write_bytes(text.encode("utf-8-sig"))

    return issues, changed


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check UTF-8 BOM and mojibake patterns.")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_ROOTS), help="Files/directories to scan.")
    parser.add_argument(
        "--ext",
        dest="extensions",
        action="append",
        default=[],
        help="Extension to scan (repeatable), e.g. --ext .html --ext .js",
    )
    parser.add_argument("--no-bom-check", action="store_true", help="Do not fail on missing UTF-8 BOM.")
    parser.add_argument("--fix", action="store_true", help="Auto-fix BOM and safe mojibake cases.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    extensions = args.extensions or list(DEFAULT_EXTS)
    roots = [pathlib.Path(path) for path in args.paths]

    all_issues: List[Issue] = []
    changed_files: List[pathlib.Path] = []

    for path in _iter_files(roots, extensions):
        issues, changed = check_file(path, check_bom=not args.no_bom_check, fix=args.fix)
        all_issues.extend(issues)
        if changed:
            changed_files.append(path)

    if changed_files:
        print(f"Fixed files: {len(changed_files)}")
        for path in changed_files:
            print(f"  FIXED: {path}")

    if all_issues:
        print(f"Issues found: {len(all_issues)}")
        for issue in all_issues:
            print(f"  {issue.kind}: {issue.path} ({issue.details})")
        return 1

    print("Encoding check passed: no BOM/mojibake issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
