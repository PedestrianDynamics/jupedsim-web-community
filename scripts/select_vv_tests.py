"""Select the V&V tests a pull request has to run.

Reads changed paths (one per line) on stdin and prints the pytest targets,
space separated: ``tests/vv`` for the full suite, a list of test files, or
nothing when no V&V test is affected. Pull requests only; pushes to main,
the schedule and dispatch always run the full suite.

A path that matches no rule runs the full suite. Stdlib only.
"""

import sys
from fnmatch import fnmatch
from pathlib import Path

FULL = "tests/vv"

# Shared code and environment: any change can move every result.
FULL_SUITE = (
    "tests/vv/conftest.py",
    "tests/vv/vv_helpers.py",
    "standards/utils/*",
    "standards/coverage.json",
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


def select(paths) -> list[str]:
    selected = set()
    for path in paths:
        if any(fnmatch(path, p) for p in FULL_SUITE):
            return [FULL]
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
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(" ".join(select(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
