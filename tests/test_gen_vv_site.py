"""Build the V&V site from tiny fixtures and check the page rules."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_vv_site.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_vv_site", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_vv_site"] = mod
    spec.loader.exec_module(mod)
    return mod


ROWS = [
    {
        "id": "T1",
        "name": "Passing",
        "criterion": "c1",
        "status": "covered",
        "test": "TestPass",
    },
    {
        "id": "T2",
        "name": "Limit",
        "criterion": "c2",
        "status": "xfail",
        "test": "TestLimit",
        "note": "Model has no cohesion.",
    },
    {"id": "T3", "name": "Later", "criterion": "c3", "status": "pending"},
    {
        "id": "T4",
        "name": "Local",
        "criterion": "c4",
        "status": "test-local",
        "test": "TestLocal",
    },
    {
        "id": "T5",
        "name": "Up",
        "criterion": "c5",
        "status": "covered",
        "test": "TestUp",
    },
    {
        "id": "T5",
        "name": "Down",
        "criterion": "c5",
        "status": "covered",
        "test": "TestDown",
    },
]


def result(test_id, status, **kw):
    return {
        "test_id": f"tests/vv/test_x.py::{test_id}",
        "status": status,
        "test_source": "tests/vv/test_x.py::f",
        "model": "CollisionFreeSpeedModel",
        "jupedsim_version": "1.0",
        "jupedsim_scenarios_version": "2.0",
        "pedpy_version": "3.0",
        "commit_sha": "abc123def456",
        "run_id": "42",
        "timestamp": "2026-01-01T00:00:00+00:00",
        **kw,
    }


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "standards").mkdir()
    manifest = {
        "standards": [
            {"id": "std", "name": "Std", "full_name": "Standard", "tests": ROWS}
        ]
    }
    definitions = {
        "standards": {
            "std": {"title": "T", "groups": [{"id": "g", "title": "Group G"}]}
        },
        "tests": [
            {
                "slug": "std-t1",
                "standard": "std",
                "coverage": {"id": "T1"},
                "group": "g",
                "title": "Pass title",
                "source": "S p. 1",
                "definition": "d",
                "expected": "e",
                "deviations": ["dev"],
                "scenario_files": [],
            }
        ],
    }
    results = [
        result("TestPass::test_a", "pass", measured=[1.5], unit="s", n=3.0, seed=7.0),
        result("TestLimit::test_b", "xfail"),
        result("TestUp::test_c", "pass"),
        result("TestDown::test_d", "fail"),
        result("TestOrphan::test_e", "pass"),
    ]
    paths = {}
    for name, data in [
        ("coverage", manifest),
        ("definitions", definitions),
        ("results", results),
    ]:
        paths[name] = tmp_path / "standards" / f"{name}.json"
        paths[name].write_text(json.dumps(data))
    return paths


def build(gen, paths, out, *, results=True):
    argv = [
        "--manifest",
        str(paths["coverage"]),
        "--definitions",
        str(paths["definitions"]),
        "--outdir",
        str(out),
    ]
    if results:
        argv += ["--results", str(paths["results"])]
    assert gen.main(argv) == 0


def page(out, slug):
    return (out / "standards" / "std" / slug / "index.html").read_text()


def test_every_row_gets_a_page(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out)
    pages = sorted(
        p.parent.name for p in (out / "standards" / "std").glob("*/index.html")
    )
    assert len(pages) == len(ROWS)
    assert "std-t1" in pages
    assert "std-t5-up" in pages and "std-t5-down" in pages
    assert "Other tests" in (out / "standards" / "std" / "index.html").read_text()


def test_status_rendering(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out)
    assert "badge-pass" in page(out, "std-t1")
    limit = page(out, "std-t2")
    assert ">FAIL<" in limit and "Model has no cohesion." in limit
    assert ">PENDING<" in page(out, "std-t3")
    assert ">TEST-LOCAL<" in page(out, "std-t4")
    assert ">FAIL<" in page(out, "std-t5-down")
    assert "1.5" in page(out, "std-t1")


def test_no_xfail_wording(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out)
    for f in out.rglob("*.html"):
        assert "xfail" not in f.read_text().lower()


def test_missing_results_and_notebooks(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out, results=False)
    index = (out / "index.html").read_text()
    assert "not available in this run" in index
    assert "local build" in index
    assert "No notebook for this test yet" in page(out, "std-t1")
    assert "Scenario picture not available" in page(out, "std-t1")


def test_footer_links_on_every_page(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out)
    pages = [out / "index.html", out / "standards" / "std" / "index.html"]
    pages += list((out / "standards" / "std").glob("*/index.html"))
    for f in pages:
        text = f.read_text()
        assert f"{gen.REPO}/commit/abc123def456" in text
        assert f'{gen.REPO}/actions/runs/42">42</a>' in text
        assert '<time datetime="2026-01-01T00:00:00.000+00:00">' in text
        assert "2026-01-01 00:00 UTC" in text
        assert "come from one run on the locked versions" in text
    assert "Join diagnostics" in (out / "index.html").read_text()
    assert "Join diagnostics" not in page(out, "std-t1")


def test_footer_without_run_ids(gen):
    run = gen.pick_run([{"commit_sha": "a" * 40}], None)
    assert run["time"] is None and not run["local"]
    assert gen.format_time("2026-10-15T10:38:07.421947+00:00") == {
        "iso": "2026-10-15T10:38:07.421+00:00",
        "text": "2026-10-15 10:38 UTC",
    }


def test_summary_cards_and_blocking_count(gen, inputs, tmp_path):
    rows = json.loads(inputs["coverage"].read_text())
    rows["standards"][0]["tests"][0]["note"] = (
        "Counterflow 50 blocks; blocking documented."
    )
    inputs["coverage"].write_text(json.dumps(rows))
    out = tmp_path / "site"
    build(gen, inputs, out)
    index = (out / "index.html").read_text()
    assert "Blocking documented: 1" in index
    order = [m for m in ("PASS", "FAIL", "TEST-LOCAL", "PENDING", "NOT RUN")]
    positions = [index.index(f"{n} <span>") for n in order]
    assert positions == sorted(positions)
    assert "blocking documented" in page(out, "std-t1")


def test_blocking_from_definition_deviation(gen):
    row = {"id": "X", "note": "Seed 42: 0 -> 46 s."}
    assert not gen.blocking_documented(row)
    definition = {"deviations": ["Level N is reported as blocking documented."]}
    assert gen.blocking_documented(row, definition)


def test_header_nav_on_every_page(gen, inputs, tmp_path):
    out = tmp_path / "site"
    build(gen, inputs, out)
    test_page = page(out, "std-t1")
    assert 'href="../../../index.html" aria-current' not in test_page
    assert 'href="../../../index.html">Summary' in test_page
    assert 'href="../../../standards/std/index.html"' in test_page
    assert 'aria-current="page">Std<' in test_page
    assert 'aria-current="page">Summary<' in (out / "index.html").read_text()
    assert "Versions used" not in test_page


def test_summary_intro_and_references(gen, inputs, tmp_path):
    defs = json.loads(inputs["definitions"].read_text())
    defs["standards"]["std"].update(citation="Cite A (2020).", doi="10.1/x")
    inputs["definitions"].write_text(json.dumps(defs))
    out = tmp_path / "site"
    build(gen, inputs, out)
    index = (out / "index.html").read_text()
    assert "Verification only" not in index
    assert "<h1>JuPedSim verification</h1>" in index
    assert "https://www.jupedsim.org" in index
    assert "Cite A (2020)." in index
    assert 'href="https://doi.org/10.1/x"' in index
