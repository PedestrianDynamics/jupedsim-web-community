"""Check the pull request V&V test selection on synthetic changes."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select_vv_tests.py"

FULL = ["tests/vv"]
RIMEA = "tests/vv/test_rimea.py"
IMO = "tests/vv/test_imo.py"
NIST = "tests/vv/test_nist.py"
ISO = "tests/vv/test_iso.py"
REGISTRY = "tests/vv/test_registry.py"
IMO_FILES = [
    IMO,
    "tests/vv/test_imo_behavior.py",
    "tests/vv/test_imo_room_evacuation.py",
    "tests/vv/test_imo_single_agent.py",
]
ALL_STANDARD_FILES = {RIMEA, NIST, ISO, *IMO_FILES}

# Which test file defines each class, as git grep would report it.
CLASSES = {
    "TestIMO11Ring": [IMO],
    "TestRiMEA13": [RIMEA],
    "TestNist21": [NIST],
    "TestIso01": [ISO],
}

MANIFEST = {
    "_comment": "rows",
    "standards": [
        {
            "id": "imo",
            "name": "IMO",
            "full_name": "IMO MSC.1/Circ.1533",
            "verification": "v",
            "tests": [
                {"id": "IMO 11", "status": "covered", "test": "TestIMO11Ring"},
                {"id": "IMO 12", "status": "pending"},
            ],
        },
        {
            "id": "rimea",
            "name": "RiMEA",
            "full_name": "RiMEA 4.1.1",
            "verification": "v",
            "tests": [{"id": "Test 13", "status": "covered", "test": "TestRiMEA13"}],
        },
        {
            "id": "nist",
            "name": "NIST",
            "full_name": "NIST TN 1822",
            "verification": "v",
            "tests": [{"id": "2.1", "status": "covered", "test": "TestNist21"}],
        },
        {
            "id": "iso",
            "name": "ISO",
            "full_name": "ISO 20414",
            "verification": "v",
            "tests": [
                {"id": "Test 1", "status": "covered", "test": "TestIso01::test_a"}
            ],
        },
    ],
}


@pytest.fixture(scope="module")
def sel():
    spec = importlib.util.spec_from_file_location("select_vv_tests", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["select_vv_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


def run(sel, paths, new=None, new_text=None):
    """Select for ``paths``; coverage.json goes from MANIFEST to ``new``."""
    old_text = json.dumps(MANIFEST)
    if new_text is None:
        new_text = json.dumps(MANIFEST if new is None else new)

    def coverage():
        return sel.manifest_files(old_text, new_text, lambda c: CLASSES.get(c, []))

    return sel.select(paths, coverage, exists=lambda p: "gone" not in p)


def edited(change):
    new = copy.deepcopy(MANIFEST)
    change(new)
    return new


def std(manifest, std_id):
    return next(s for s in manifest["standards"] if s["id"] == std_id)


@pytest.mark.parametrize(
    "paths, expected",
    [
        pytest.param(
            [
                "site/layouts/partials/header.html",
                "scripts/gen_vv_site.py",
                "tests/test_gen_vv_site.py",
            ],
            [],
            id="site-only",
        ),
        pytest.param(
            ["README.md", "CONTRIBUTING.md", "standards/nist/README.md"],
            [],
            id="docs-only",
        ),
        pytest.param(["standards/definitions.json"], [], id="definitions"),
        pytest.param(
            ["scripts/select_vv_tests.py"], FULL, id="selector-runs-full-suite"
        ),
        pytest.param(["tests/test_select_vv_tests.py"], [], id="selector-test"),
        pytest.param(
            ["standards/general/scenario_files/bottleneck-zone/config.json"],
            [],
            id="general-scenarios",
        ),
        pytest.param([".github/workflows/notebooks.yml"], [], id="other-workflow"),
        pytest.param(
            ["standards/iso/scenario_files/Iso-04-corner.zip"], [ISO], id="iso"
        ),
        pytest.param(
            ["standards/nist/nist2_8_counterflow.ipynb"], [NIST], id="nist-notebook"
        ),
        pytest.param(
            ["standards/imo/scenario_files/x.zip"], IMO_FILES, id="imo-scenario"
        ),
        pytest.param(
            ["standards/rimea/scenario_files/Rimea-01-corridor.zip"],
            [IMO, RIMEA],
            id="rimea-also-runs-imo",
        ),
        pytest.param(["geometries/dxf/room.dxf"], [RIMEA], id="geometries"),
        pytest.param(
            ["tests/vv/test_imo_behavior.py"],
            ["tests/vv/test_imo_behavior.py"],
            id="one-test-file",
        ),
        pytest.param(["tests/vv/conftest.py"], FULL, id="conftest"),
        pytest.param(["tests/vv/vv_helpers.py"], FULL, id="helpers"),
        pytest.param(["standards/utils/movement_disability.py"], FULL, id="utils"),
        pytest.param(["pyproject.toml"], FULL, id="pyproject"),
        pytest.param(["uv.lock"], FULL, id="uv-lock"),
        pytest.param([".github/workflows/vv.yml"], FULL, id="vv-yml"),
        pytest.param(["foo/bar.py"], FULL, id="unknown-path"),
        pytest.param(["tests/vv/test_gone.py"], FULL, id="deleted-test-file"),
        pytest.param(["README.md", "tests/vv/conftest.py"], FULL, id="docs-and-shared"),
    ],
)
def test_paths(sel, paths, expected):
    assert run(sel, paths) == expected


def test_one_standard_selects_no_other(sel):
    got = set(run(sel, ["standards/nist/scenario_files/Nist-2-3-corner.zip"]))
    assert got == {NIST}
    assert not got & (ALL_STANDARD_FILES - {NIST})


def set_row(std_id, i, key, value):
    def change(m):
        std(m, std_id)["tests"][i][key] = value

    return change


@pytest.mark.parametrize(
    "change, expected",
    [
        pytest.param(lambda m: None, [REGISTRY], id="unchanged"),
        pytest.param(
            set_row("imo", 0, "status", "pending"),
            [IMO, REGISTRY],
            id="row-change-selects-its-class-file",
        ),
        pytest.param(
            set_row("iso", 0, "note", "x"),
            [ISO, REGISTRY],
            id="row-with-method-reference",
        ),
        pytest.param(
            lambda m: std(m, "nist").update(full_name="NIST TN 1822 r1"),
            [NIST, REGISTRY],
            id="standard-field-change",
        ),
        pytest.param(
            lambda m: std(m, "imo")["tests"].append({"id": "IMO 99"}),
            [REGISTRY],
            id="row-without-test",
        ),
        pytest.param(
            set_row("rimea", 0, "test", "TestDoesNotExist"),
            sorted({IMO, RIMEA, REGISTRY}),
            id="unknown-class-selects-standard",
        ),
        pytest.param(
            lambda m: m.update(_comment="changed"), FULL, id="top-level-change"
        ),
    ],
)
def test_coverage_json(sel, change, expected):
    new = edited(change)
    assert run(sel, ["standards/coverage.json"], new=new) == expected


def test_coverage_row_change_skips_other_standards(sel):
    new = edited(set_row("imo", 0, "status", "pending"))
    got = set(run(sel, ["standards/coverage.json"], new=new))
    assert not got & {RIMEA, NIST, ISO}


@pytest.mark.parametrize("new_text", ["{bad", None, '{"standards": 1}'])
def test_coverage_json_unreadable_runs_full_suite(sel, new_text):
    def coverage():
        return sel.manifest_files(json.dumps(MANIFEST), new_text, lambda c: [])

    assert sel.select(["standards/coverage.json"], coverage) == FULL
