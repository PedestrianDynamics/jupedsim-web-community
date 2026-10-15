"""Select the V&V tests a pull request has to run.

Usage: ``select_vv_tests.py BASE HEAD`` with the changed paths (one per
line) on stdin. Prints the pytest targets, space separated: ``tests/vv``
for the full suite, a list of test files, or nothing when no V&V test is
affected. Pull requests only; pushes to main, the schedule and dispatch
always run the full suite.

A path that matches no rule runs the full suite. A change to
standards/coverage.json is compared row by row between BASE and HEAD and
runs test_registry.py plus the test files of the changed rows. Stdlib
only.
"""

import json
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

FULL = "tests/vv"

# Shared code and environment: any change can move every result.
FULL_SUITE = (
    "tests/vv/conftest.py",
    "tests/vv/vv_helpers.py",
    "standards/utils/*",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/vv.yml",
    "scripts/select_vv_tests.py",
)

IMO = (
    "tests/vv/test_imo.py",
    "tests/vv/test_imo_behavior.py",
    "tests/vv/test_imo_room_evacuation.py",
    "tests/vv/test_imo_single_agent.py",
)

MANIFEST = "standards/coverage.json"
REGISTRY = "tests/vv/test_registry.py"

# Per standard: scenario files, builders and notebooks map to the test
# files that load them. test_imo.py loads Rimea-10.zip and the rimea16
# builder, so RiMEA changes also run it.
STANDARD = (
    ("standards/rimea/*", ("tests/vv/test_rimea.py", "tests/vv/test_imo.py")),
    ("standards/nist/*", ("tests/vv/test_nist.py",)),
    ("standards/iso/*", ("tests/vv/test_iso.py",)),
    ("standards/imo/*", IMO),
    ("geometries/*", ("tests/vv/test_rimea.py",)),
)

# No V&V test reads these. The site generator test runs on every pull
# request; standards/general and standards/tests belong to
# scenario-examples.yml; notebooks do not run on pull requests.
NO_VV = (
    "*.md",
    "LICENSE",
    ".gitignore",
    "docker/*",
    "site/*",
    "scripts/gen_vv_site.py",
    "scripts/gen_standards_report.py",
    "scripts/run_notebooks.py",
    "scripts/assets/*",
    "tests/test_gen_vv_site.py",
    "standards/definitions.json",
    "standards/general/*",
    "standards/tests/*",
    ".github/*",
)


def git(*args) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def standard_files(std_id: str) -> tuple[str, ...]:
    return dict(STANDARD).get(f"standards/{std_id}/*", ())


def row_files(row: dict, std_id: str, head: str) -> set[str]:
    """Test files that define the row's test class; else the standard's."""
    cls = (row.get("test") or "").split("::")[0]
    if not cls:
        return set()
    try:
        out = git("grep", "-l", "-E", rf"^class {cls}[(:]", head, "--", "tests/vv")
    except subprocess.CalledProcessError:
        out = ""
    found = {line.split(":", 1)[1] for line in out.splitlines()}
    return found or set(standard_files(std_id))


def by_id(manifest: dict) -> dict:
    return {s["id"]: s for s in manifest["standards"]}


def manifest_files(base: str, head: str) -> set[str] | None:
    """Tests affected by a coverage.json change; None means the full suite."""
    try:
        old = json.loads(git("show", f"{base}:{MANIFEST}"))
        new = json.loads(git("show", f"{head}:{MANIFEST}"))
        old_std, new_std = by_id(old), by_id(new)
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
        return None
    if {k: v for k, v in old.items() if k != "standards"} != {
        k: v for k, v in new.items() if k != "standards"
    }:
        return None
    selected = {REGISTRY}
    for std_id in old_std.keys() | new_std.keys():
        selected |= standard_changes(old_std.get(std_id), new_std.get(std_id), head)
    return selected


def standard_changes(old: dict | None, new: dict | None, head: str) -> set[str]:
    std_id = (old or new)["id"]
    if old is None or new is None:
        return set(standard_files(std_id))
    meta = [k for k in old.keys() | new.keys() if k != "tests"]
    if any(old.get(k) != new.get(k) for k in meta):
        return set(standard_files(std_id))
    old_rows = [json.dumps(r, sort_keys=True) for r in old.get("tests", [])]
    new_rows = [json.dumps(r, sort_keys=True) for r in new.get("tests", [])]
    changed = set(old_rows) ^ set(new_rows)
    files = set()
    for row in changed:
        files |= row_files(json.loads(row), std_id, head)
    return files


def select(paths, base: str, head: str) -> list[str]:
    selected = set()
    for path in paths:
        if any(fnmatch(path, p) for p in FULL_SUITE):
            return [FULL]
        if path == MANIFEST:
            files = manifest_files(base, head)
            if files is None:
                return [FULL]
            selected |= files
            continue
        if fnmatch(path, "tests/vv/test_*.py"):
            selected.add(path)
            continue
        if any(fnmatch(path, p) for p in NO_VV):
            continue
        hit = next((t for p, t in STANDARD if fnmatch(path, p)), None)
        if not hit:
            return [FULL]
        selected.update(hit)
    # A deleted test file cannot be run on its own; run everything.
    if not all(Path(t).exists() for t in selected):
        return [FULL]
    return sorted(selected)


def main() -> int:
    base, head = sys.argv[1:3]
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(" ".join(select(paths, base, head)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
