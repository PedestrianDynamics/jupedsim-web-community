"""V&V test configuration — makes vv_helpers importable and exports results.

Tests that request the ``vv_row`` fixture write one row each to
``results/vv-results.json`` at the end of the session. The row carries the
manifest metadata (standard, clause, criterion) and whatever the test reports
through ``vv_row.set(...)``. Fields a test does not report are null.
"""

import datetime
import importlib.metadata
import json
import os
import pathlib
import sys

import pytest

VV_DIR = str(pathlib.Path(__file__).resolve().parent)
if VV_DIR not in sys.path:
    sys.path.insert(0, VV_DIR)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "standards" / "coverage.json"
RESULTS_PATH = REPO_ROOT / "results" / "vv-results.json"
RESULTS_PARTIAL_PATH = REPO_ROOT / "results" / "vv-results-partial.json"
# RiMEA items a full run collects (includes the 3 skipped placeholder classes).
FULL_RIMEA_ITEMS = 22

ROW_FIELDS = (
    "quantity",
    "unit",
    "lower",
    "upper",
    "measured",
    "n",
    "seed",
    "premovement_seed",
    "model",
    "scenario_file",
    "rule",
)

_ROWS: list[dict] = []
_STATUS_KEY = pytest.StashKey[dict]()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _provenance() -> dict:
    """Commit and run identity from GitHub Actions; null outside CI."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    return {
        "commit_sha": os.environ.get("GITHUB_SHA") if run_id else None,
        "run_id": run_id,
        "timestamp": (
            datetime.datetime.now(datetime.UTC).isoformat() if run_id else None
        ),
        "jupedsim_version": _package_version("jupedsim"),
        "jupedsim_scenarios_version": _package_version("jupedsim-scenarios"),
        "pedpy_version": _package_version("pedpy"),
    }


def _manifest_entry(class_name: str | None) -> dict:
    """Standard, clause and criterion for a test class (longest manifest prefix)."""
    empty = {"standard": None, "clause": None, "criterion": None}
    if not class_name:
        return empty
    best, best_len = None, 0
    for std in json.loads(MANIFEST.read_text())["standards"]:
        for test in std["tests"]:
            ref = test.get("test")
            if ref and class_name.startswith(ref) and len(ref) > best_len:
                best, best_len = (std, test), len(ref)
    if best is None:
        return empty
    std, test = best
    return {
        "standard": std["name"],
        "clause": test["id"],
        "criterion": test.get("criterion"),
    }


def _to_json(value):
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, list | tuple):
        return [_to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    return float(value) if hasattr(value, "__float__") else value


class VVRow:
    """Fields a test reports for its results row."""

    def __init__(self, base: dict):
        self._values = {k: None for k in ROW_FIELDS}
        self._base = base

    def set(self, **fields) -> None:
        unknown = set(fields) - set(ROW_FIELDS)
        if unknown:
            raise KeyError(f"Unknown results field(s): {sorted(unknown)}")
        self._values.update(fields)

    def as_dict(self, status: str) -> dict:
        row = {**self._base, "kind": "verification", "status": status}
        row.update({k: _to_json(v) for k, v in self._values.items()})
        return row


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    item.stash.setdefault(_STATUS_KEY, {})[report.when] = report.outcome


@pytest.fixture
def vv_row(request):
    """Yield a VVRow and append its finished row after the test ends."""
    cls = request.cls.__name__ if request.cls else None
    base = {
        "test_id": request.node.nodeid,
        **_manifest_entry(cls),
        "test_source": f"{pathlib.Path(request.node.fspath).relative_to(REPO_ROOT)}"
        f"::{request.node.name}",
        **_provenance(),
    }
    row = VVRow(base)
    yield row
    phases = request.node.stash.get(_STATUS_KEY, {})
    _ROWS.append(row.as_dict(_status(phases)))


def _status(phases: dict) -> str:
    if phases.get("setup") == "failed":
        return "error"
    if phases.get("setup") == "skipped":
        return "skip"
    return {"passed": "pass", "failed": "fail", "skipped": "skip"}.get(
        phases.get("call", ""), "error"
    )


def pytest_sessionfinish(session):
    if not _ROWS:
        return
    rimea_items = sum(1 for item in session.items if item.path.name == "test_rimea.py")
    full_run = rimea_items == FULL_RIMEA_ITEMS
    path = RESULTS_PATH if full_run else RESULTS_PARTIAL_PATH
    if not full_run:
        print(
            f"WARNING: subset run ({rimea_items} of {FULL_RIMEA_ITEMS} RiMEA tests "
            f"collected); writing {path.name}, {RESULTS_PATH.name} is not touched",
            file=sys.stderr,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(_ROWS, key=lambda r: r["test_id"])
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
