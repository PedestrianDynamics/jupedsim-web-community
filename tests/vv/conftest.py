"""V&V test configuration — makes vv_helpers importable and exports results.

Every test in the standard test files (``STANDARD_FILES``) writes one row to
``results/vv-results.json`` at the end of the session. The row carries the
manifest metadata (standard, clause, criterion), the test status and the run
provenance, plus whatever the test reports through ``vv_row.set(...)``.
Fields a test does not report are null.
``scenario_sha256`` is derived from ``scenario_file``, not reported by tests.

A run that collects only part of the suite writes ``vv-results-partial.json``
instead, so a subset run never replaces the full results file.
"""

import datetime
import hashlib
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
# Test files whose tests write a results row; a full run collects all of them.
STANDARD_FILES = frozenset(
    {
        "test_imo.py",
        "test_imo_behavior.py",
        "test_imo_room_evacuation.py",
        "test_imo_single_agent.py",
        "test_iso.py",
        "test_nist.py",
        "test_rimea.py",
    }
)

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
_DESELECTED: list[int] = [0]


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


def _manifest_entry(class_name: str | None, method_name: str) -> dict:
    """Standard, clause and criterion for a test (longest manifest prefix).

    A manifest ``test`` reference is ``ClassPrefix`` or ``ClassPrefix::method``;
    the method form matches only that method (parametrised ids included).
    """
    empty = {"standard": None, "standard_id": None, "clause": None, "criterion": None}
    if not class_name:
        return empty
    best, best_len = None, 0
    for std in json.loads(MANIFEST.read_text())["standards"]:
        for test in std["tests"]:
            ref = test.get("test")
            if not ref:
                continue
            ref_class, _, ref_method = ref.partition("::")
            if not class_name.startswith(ref_class):
                continue
            if ref_method and ref_method != method_name.split("[")[0]:
                continue
            if len(ref) > best_len:
                best, best_len = (std, test), len(ref)
    if best is None:
        return empty
    std, test = best
    return {
        "standard": std["name"],
        "standard_id": std["id"],
        "clause": test["id"],
        "criterion": test.get("criterion"),
    }


def _scenario_sha256(scenario_file):
    """SHA-256 of the scenario zip(s) a test names; null for anything else.

    ``scenario_file`` is a repo-relative path or a list of them. A path that
    is not an existing ``.zip`` (a scenario builder script, or none) has no
    hash.
    """
    if isinstance(scenario_file, list | tuple):
        return [_scenario_sha256(f) for f in scenario_file]
    if not isinstance(scenario_file, str) or not scenario_file.endswith(".zip"):
        return None
    path = REPO_ROOT / scenario_file
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        row["scenario_sha256"] = _scenario_sha256(self._values["scenario_file"])
        return row


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    outcome_name = report.outcome
    if hasattr(report, "wasxfail"):
        # pytest reports an expected failure as "skipped" and an unexpected
        # pass of a non-strict xfail as "passed"; keep both visible.
        outcome_name = "xfailed" if report.skipped else "xpassed"
    item.stash.setdefault(_STATUS_KEY, {})[report.when] = outcome_name


@pytest.fixture(autouse=True)
def vv_row(request):
    """Yield a VVRow and append its finished row after the test ends.

    Autouse, so every test in ``STANDARD_FILES`` writes a row even if it does
    not report any field. Tests in other files get a row that is not recorded.
    """
    if request.node.path.name not in STANDARD_FILES:
        yield VVRow({})
        return
    cls = request.cls.__name__ if request.cls else None
    base = {
        "test_id": request.node.nodeid,
        **_manifest_entry(cls, request.node.name),
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
    return {
        "passed": "pass",
        "failed": "fail",
        "skipped": "skip",
        "xfailed": "xfail",
        "xpassed": "xpass",
    }.get(phases.get("call", ""), "error")


def pytest_deselected(items):
    _DESELECTED[0] += len(items)


def _is_full_run(session) -> bool:
    """All standard files collected, nothing deselected, no node-id selection."""
    collected = {item.path.name for item in session.items}
    node_selection = any("::" in arg for arg in session.config.args)
    return STANDARD_FILES <= collected and not _DESELECTED[0] and not node_selection


def pytest_sessionfinish(session):
    if not _ROWS:
        return
    full_run = _is_full_run(session)
    path = RESULTS_PATH if full_run else RESULTS_PARTIAL_PATH
    if not full_run:
        print(
            "WARNING: subset run (not every V&V test collected); writing "
            f"{path.name}, {RESULTS_PATH.name} is not touched",
            file=sys.stderr,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(_ROWS, key=lambda r: r["test_id"])
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
