"""Build the V&V site from tiny fixtures and check the page rules."""

import importlib.util
import json
import shutil
import subprocess
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


def build(gen, paths, content, *, results=True):
    argv = [
        "--manifest",
        str(paths["coverage"]),
        "--definitions",
        str(paths["definitions"]),
        "--hugo-content",
        str(content),
    ]
    if results:
        argv += ["--results", str(paths["results"])]
    assert gen.main(argv) == 0


@pytest.fixture
def content(tmp_path):
    return tmp_path / "site" / "content"


def page(content, slug):
    return (content / "standards" / "std" / slug / "index.md").read_text()


def test_every_row_gets_a_page_bundle(gen, inputs, content):
    build(gen, inputs, content)
    pages = sorted(
        p.parent.name for p in (content / "standards" / "std").glob("*/index.md")
    )
    assert len(pages) == len(ROWS)
    assert "std-t1" in pages
    assert "std-t5-up" in pages and "std-t5-down" in pages
    index = (content / "standards" / "std" / "_index.md").read_text()
    assert "Other tests" in index and "Group G" in index
    assert "type" in index and "docs" in index


def test_sidebar_weights_follow_test_order(gen, inputs, content):
    build(gen, inputs, content)
    slugs = ["std-t1", "std-t2", "std-t3", "std-t4", "std-t5-up", "std-t5-down"]
    weights = [int(page(content, s).split("weight: ")[1].split("\n")[0]) for s in slugs]
    assert weights == sorted(weights) and len(set(weights)) == len(slugs)


def test_status_rendering(gen, inputs, content):
    build(gen, inputs, content)
    assert '{{< status "PASS" >}}' in page(content, "std-t1")
    limit = page(content, "std-t2")
    assert '{{< status "FAIL" >}}' in limit and "Model has no cohesion." in limit
    assert '{{< status "PENDING" >}}' in page(content, "std-t3")
    assert '{{< status "TEST-LOCAL" >}}' in page(content, "std-t4")
    assert '{{< status "FAIL" >}}' in page(content, "std-t5-down")
    assert "1.5" in page(content, "std-t1")


def test_page_sections(gen, inputs, content):
    build(gen, inputs, content)
    text = page(content, "std-t1")
    for n, name in enumerate(
        [
            "Definition",
            "Scenario",
            "What to expect",
            "Our result",
            "Our implementation",
            "Our notebook",
        ],
        start=1,
    ):
        assert f"## {n}. {name}" in text


def test_no_forbidden_wording(gen, inputs, content):
    build(gen, inputs, content)
    for f in content.rglob("*.md"):
        text = f.read_text().lower()
        for word in ("xfail", "expected failure", "known"):
            assert word not in text, (f, word)


def test_missing_results_and_notebooks(gen, inputs, content):
    build(gen, inputs, content, results=False)
    index = (content / "_index.md").read_text()
    assert "not available in this run" in index
    run = json.loads((content.parent / "data" / "run.json").read_text())
    assert run["run"]["local"] is True
    assert "No notebook for this test yet" in page(content, "std-t1")
    assert "Scenario picture not available" in page(content, "std-t1")
    assert "not available" in (content / "diagnostics.md").read_text()


def notebook_inputs(tmp_path, inputs, status):
    nb_dir = tmp_path / "nb"
    (nb_dir / "html").mkdir(parents=True)
    (nb_dir / "html" / "a.html").write_text("<html></html>")
    (nb_dir / "figs").mkdir()
    (nb_dir / "figs" / "r.png").write_bytes(b"png")
    manifest = {
        "notebooks": [
            {
                "path": "nb/a.ipynb",
                "status": status,
                "error_summary": "ValueError: boom",
                "html": "html/a.html",
                "figures": {"result": ["figs/r.png"]},
            }
        ]
    }
    if status is not None:
        (nb_dir / "manifest.json").write_text(json.dumps(manifest))
    cov = json.loads(inputs["coverage"].read_text())
    cov["standards"][0]["tests"][0]["notebook"] = "nb/a.ipynb"
    inputs["coverage"].write_text(json.dumps(cov))
    return nb_dir


@pytest.mark.parametrize("status", ["error", None])
def test_unexecuted_notebook_is_not_run(gen, inputs, content, tmp_path, status):
    nb_dir = notebook_inputs(tmp_path, inputs, status)
    argv = [
        "--manifest",
        str(inputs["coverage"]),
        "--definitions",
        str(inputs["definitions"]),
        "--results",
        str(inputs["results"]),
        "--hugo-content",
        str(content),
        "--notebooks",
        str(nb_dir),
    ]
    if status is None:
        (nb_dir / "manifest.json").write_text(json.dumps({"notebooks": []}))
    assert gen.main(argv) == 0
    text = page(content, "std-t1")
    section = text.split("## 6. Our notebook")[1]
    assert "NOT RUN" in section
    assert "did not execute in this run" in section
    assert "FAIL" not in section
    assert "static-link" not in section
    assert "No notebook for this test yet" not in section
    assert "figures/" not in text
    assert not list((content.parent / "static").rglob("*.html"))


def test_footer_data_and_diagnostics(gen, inputs, content):
    build(gen, inputs, content)
    run = json.loads((content.parent / "data" / "run.json").read_text())
    assert run["run"]["commit_sha"] == "abc123def456"
    assert run["run"]["run_id"] == "42"
    assert run["run"]["time"]["iso"] == "2026-01-01T00:00:00.000+00:00"
    assert run["run"]["time"]["text"] == "2026-01-01 00:00 UTC"
    assert run["versions"][0] == {"name": "jupedsim", "version": "1.0"}
    diag = (content / "diagnostics.md").read_text()
    assert "TestOrphan" in diag
    assert "Join diagnostics" not in (content / "_index.md").read_text()


def test_pages_commit_shown_only_when_it_differs(gen, inputs, content, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    build(gen, inputs, content)
    run = json.loads((content.parent / "data" / "run.json").read_text())
    assert run["run"]["pages_commit_sha"] == "f" * 40
    monkeypatch.setenv("GITHUB_SHA", "abc123def456")
    build(gen, inputs, content)
    run = json.loads((content.parent / "data" / "run.json").read_text())
    assert run["run"]["pages_commit_sha"] is None


def test_pages_commit_falls_back_to_git_then_none(gen, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert gen.pages_commit(Path(SCRIPT).parent)
    assert gen.pages_commit(tmp_path) is None


def test_footer_without_run_ids(gen):
    run = gen.pick_run([{"commit_sha": "a" * 40}], None)
    assert run["time"] is None and not run["local"]
    assert gen.format_time("2026-10-15T10:38:07.421947+00:00") == {
        "iso": "2026-10-15T10:38:07.421+00:00",
        "text": "2026-10-15 10:38 UTC",
    }


def test_summary_cards_and_blocking_count(gen, inputs, content):
    rows = json.loads(inputs["coverage"].read_text())
    rows["standards"][0]["tests"][0]["note"] = (
        "Counterflow 50 blocks; blocking documented."
    )
    inputs["coverage"].write_text(json.dumps(rows))
    build(gen, inputs, content)
    index = (content / "_index.md").read_text()
    assert 'blocking="1"' in index
    assert 'pass="2"' in index and 'fail="2"' in index
    assert 'test_local="1"' in index and 'pending="1"' in index
    assert 'not_run="0"' in index
    assert "blocking documented" in page(content, "std-t1")


def test_blocking_from_definition_deviation(gen):
    row = {"id": "X", "note": "Seed 42: 0 -> 46 s."}
    assert not gen.blocking_documented(row)
    definition = {"deviations": ["Level N is reported as blocking documented."]}
    assert gen.blocking_documented(row, definition)


def test_summary_intro_and_references(gen, inputs, content):
    defs = json.loads(inputs["definitions"].read_text())
    defs["standards"]["std"].update(citation="Cite A (2020).", doi="10.1/x")
    inputs["definitions"].write_text(json.dumps(defs))
    build(gen, inputs, content)
    index = (content / "_index.md").read_text()
    assert "Verification only" not in index
    assert 'title: "JuPedSim verification"' in index
    assert "(https://www.jupedsim.org)" in index
    assert "not validation" in index
    assert "live verification" not in index and "expected result" not in index
    assert "(https://github.com/PedestrianDynamics/jupedsim-web-community)" in index
    assert "Cite A (2020)." in index
    assert "(https://doi.org/10.1/x)" in index


@pytest.mark.skipif(shutil.which("hugo") is None, reason="hugo not installed")
def test_hugo_builds_fixture_site(gen, inputs, tmp_path):
    site_src = Path(__file__).resolve().parents[1] / "site"
    site = tmp_path / "site"
    shutil.copytree(
        site_src,
        site,
        ignore=shutil.ignore_patterns("content", "data", "public", "resources"),
    )
    build(gen, inputs, site / "content")
    proc = subprocess.run(
        ["hugo", "--minify", "--panicOnWarning"],
        cwd=site,
        capture_output=True,
        check=False,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (site / "public" / "standards" / "std" / "std-t1" / "index.html").is_file()


def test_number_format_keeps_pass_fail_reading(gen):
    assert gen.fmt_number(1.2230104463633147) == "1.223"
    assert gen.fmt_number(20.0) == "20"
    assert gen.fmt_number(13.7004, (13.0, 13.7)) == "13.7004"
    assert gen.fmt_number(13.7, (13.0, 13.7)) == "13.7"
    assert gen.fmt_number(0.9999999, (1.0,)) == "0.9999999"


def view(gen, **fields):
    row = {"test_id": "t.py::TestA::test_x", "status": "pass"} | fields
    return gen.result_view(row)


def test_result_cell_range_forms(gen):
    both = view(gen, quantity="q", measured=13.15, unit="s", lower=13.0, upper=13.7)
    cell = gen.result_cell(both)
    assert cell.startswith("q = 13.15 s<br>")
    assert "accepted 13 – 13.7 s" in cell
    low = gen.result_cell(view(gen, measured=2.0, unit="s", lower=1.5))
    assert "accepted ≥ 1.5 s" in low
    up = gen.result_cell(view(gen, measured=2.0, unit="s", upper=3.0))
    assert "accepted ≤ 3 s" in up


def test_result_cell_missing_values(gen):
    assert gen.result_cell(view(gen)) == "&mdash;"
    assert gen.result_cell(view(gen, quantity="q")) == "q: not reported"
    assert gen.run_cell(view(gen)) == "&mdash;"
    assert (
        gen.run_cell(view(gen, n=1.0, seed=42.0))
        == '<span class="vv-nw">n = 1</span> &middot; <span class="vv-nw">seed 42</span>'
    )
    assert gen.run_cell(view(gen, seed=7.0)) == '<span class="vv-nw">seed 7</span>'


def test_result_cell_dict_and_list_measured(gen):
    cell = gen.result_cell(
        view(gen, quantity="d", measured={"a": 1.0, "b": 0.93687862718091, "c": None})
    )
    assert "a: 1<br>b: 0.9369<br>c: \u2014 (not reached)" in cell
    assert "0.936878" not in cell
    listed = gen.result_cell(view(gen, quantity="l", measured=[1.5, 2.123456]))
    assert listed == "l = 1.5, 2.123"


def test_unreached_entries_get_a_note(gen):
    views = [view(gen, quantity="d", measured={"a": 1.0, "b": None})]
    text = "\n".join(gen.case_table(views))
    assert "b: \u2014 (not reached)" in text
    assert "recorded no value" in text
    plain = "\n".join(gen.case_table([view(gen, quantity="d", measured={"a": 1.0})]))
    assert "not reached" not in plain


def test_case_table_has_four_columns_and_rule_notes(gen):
    views = [view(gen, rule="Needs <b> & more", quantity="q", measured=1.0)]
    text = "\n".join(gen.case_table(views))
    assert "<th>Case</th><th>Status</th><th>Result</th><th>Run</th>" in text
    assert 'title="t.py::TestA::test_x">test_<wbr>x</code>' in text
    assert "Needs &lt;b&gt; &amp; more" in text
    assert "\n\n<" not in text.split("</table>")[0].split("<table")[1]


def test_dimensionless_unit_is_not_printed(gen):
    cell = gen.result_cell(view(gen, quantity="p", measured=0.5, unit="1", lower=0.05))
    assert cell.startswith("p = 0.5<br>")
    assert "accepted \u2265 0.05<" in cell


def test_rule_note_omitted_only_when_it_restates_bounds(gen):
    same = view(gen, lower=9.5, upper=10.5, rule="bound: 9.50 <= measured <= 10.5")
    one = view(gen, lower=0.05, rule="bound: measured > 0.05")
    more = view(gen, lower=0.05, rule="bound: measured > 0.05 (KS test)")
    other = view(gen, lower=1.0, rule="bound: 0.9 <= measured")
    assert gen.restates_bounds(same)
    assert gen.restates_bounds(one)
    assert not gen.restates_bounds(more)
    assert not gen.restates_bounds(other)
    text = "\n".join(gen.case_table([same]))
    assert "vv-rules" not in text
    assert "vv-rules" in "\n".join(gen.case_table([more]))


def test_case_names_break_after_underscores(gen):
    assert gen.wrap_name("test_down_faster") == "test_<wbr>down_<wbr>faster"
