#!/usr/bin/env python3
"""Write the Hugo content of the public V&V site.

Inputs:
  --manifest     standards/coverage.json (one row per standard test)
  --definitions  standards/definitions.json (per-test page content)
  --results      vv-results.json from a full pytest run (optional)
  --notebooks    directory holding the executed-notebook manifest.json (optional)

Output (--hugo-content, for example site/content; the Hugo project in site/
holds the theme, layouts and styles):
  content/_index.md                          summary, cards, references
  content/standards/<std>/_index.md          grouped test table
  content/standards/<std>/<slug>/index.md    one page bundle per test
  data/run.json                              versions and run provenance
  static/notebooks/<std>/<slug>/             executed notebook HTML
All of these are generated and git-ignored; the generator replaces
content/standards and static/notebooks on every run.

Build the site with: cd site && hugo --minify --panicOnWarning

Slugs: the definitions.json slug when present. Otherwise
"<standard-id>-<id>" where <id> is the coverage id lower-cased, stripped of
a leading "Test"/"Verif."/"IMO" word, with runs of non-alphanumerics turned
into "-" (so "Verif.2.4" in nist gives "nist-2-4"). When a coverage id
repeats within a standard, the slugified row name is appended. Remaining
clashes get a numeric suffix.

Page status (plan section 3): PENDING and TEST-LOCAL come from the coverage
row; otherwise from the results rows joined to the row (fail/error/xfail/
xpass -> FAIL, all pass -> PASS). With no usable results rows a covered row
shows NOT RUN, and an xfail row shows FAIL (declared limitation).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely import wkt

REPO = "https://github.com/PedestrianDynamics/jupedsim-web-community"
BANNER = "Verification only; no validation comparisons have been run (#165)."
STATUS_ORDER = ["PASS", "FAIL", "TEST-LOCAL", "PENDING", "NOT RUN"]
FAIL_ROWS = {"fail", "error", "xfail"}
CASE_LABEL = {
    "pass": "PASS",
    "fail": "FAIL",
    "error": "FAIL",
    "xfail": "FAIL",
    "xpass": "FAIL",
    "skip": "SKIP",
}
MAX_PANELS = 6


# ----------------------------------------------------------------- loading


def load_json(path: Path | None):
    if path is None or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_inputs(args):
    manifest = load_json(args.manifest)
    if manifest is None:
        sys.exit(f"manifest not found: {args.manifest}")
    definitions = load_json(args.definitions) or {"standards": {}, "tests": []}
    results = load_json(args.results)
    nb_dir = Path(args.notebooks) if args.notebooks else None
    nb_manifest = load_json(nb_dir / "manifest.json") if nb_dir else None
    return manifest, definitions, results, nb_manifest, nb_dir


# ----------------------------------------------------------------- slugs


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def short_id(row_id: str) -> str:
    stripped = re.sub(
        r"^(test|verif\.?|imo)\s*", "", row_id.strip(), flags=re.IGNORECASE
    )
    return slugify(stripped)


def derive_slugs(manifest, def_by_key) -> dict:
    """Return {(std, index): slug} for every coverage row."""
    slugs, used = {}, set()
    for std in manifest["standards"]:
        id_counts = {}
        for row in std["tests"]:
            id_counts[row["id"]] = id_counts.get(row["id"], 0) + 1
        for i, row in enumerate(std["tests"]):
            definition = find_definition(def_by_key, std["id"], row)
            slug = definition.get("slug") if definition else None
            if not slug:
                slug = f"{std['id']}-{short_id(row['id'])}"
                if id_counts[row["id"]] > 1:
                    slug += "-" + slugify(row["name"])
            base, n = slug, 2
            while (std["id"], slug) in used:
                slug, n = f"{base}-{n}", n + 1
            used.add((std["id"], slug))
            slugs[(std["id"], i)] = slug
    return slugs


def find_definition(def_by_key, std_id, row):
    candidates = def_by_key.get((std_id, row["id"]), [])
    if len(candidates) == 1:
        return candidates[0]
    for cand in candidates:
        if cand.get("coverage", {}).get("name") == row["name"]:
            return cand
    return None


# ----------------------------------------------------------------- joins


def split_test_id(test_id: str):
    parts = test_id.split("::")
    if len(parts) < 3:
        return None, parts[-1].split("[")[0]
    return parts[-2], parts[-1].split("[")[0]


def result_matches(test_id: str, ref: str | None) -> bool:
    if not ref:
        return False
    prefix, _, method = ref.partition("::")
    cls, meth = split_test_id(test_id)
    if cls is None or not cls.startswith(prefix):
        return False
    return not method or meth == method


def assign_results(manifest, results) -> dict:
    """Return {(std, row index): [results rows]}.

    A results row goes to the coverage rows whose reference matches with the
    longest class prefix, so "TestNist21" does not capture "TestNist210...".
    """
    assigned = {}
    for r in results or []:
        hits = []
        for std in manifest["standards"]:
            for i, row in enumerate(std["tests"]):
                ref = row.get("test")
                if result_matches(r["test_id"], ref):
                    hits.append(((std["id"], i), len(ref.partition("::")[0])))
        best = max((n for _, n in hits), default=0)
        for key, n in hits:
            if n == best:
                assigned.setdefault(key, []).append(r)
    return assigned


def derive_status(row, res_rows):
    """Return (status label, notes list)."""
    note = row.get("note")
    cov = row["status"]
    if cov == "pending":
        return "PENDING", []
    if cov == "test-local":
        return "TEST-LOCAL", [note] if note else []
    if cov in ("skip", "notebook"):
        return "PENDING", [note] if note else []
    states = {r["status"] for r in res_rows}
    notes = []
    if states & FAIL_ROWS:
        if "xfail" in states and note:
            notes.append(f"Limitation: {note}")
        return "FAIL", notes
    if "xpass" in states:
        return "FAIL", ["Unexpected pass of a test declared as a limitation."]
    if "pass" in states:
        skipped = sum(1 for r in res_rows if r["status"] == "skip")
        if skipped:
            notes.append(f"{skipped} parametrised case(s) skipped.")
        return "PASS", notes
    if cov == "xfail":
        return "FAIL", [f"Limitation: {note}" if note else "Declared limitation."]
    return "NOT RUN", ["Not run in this build."]


def blocking_documented(row, definition=None) -> bool:
    """True when the row note or a deviation of its definition documents blocking."""
    texts = [row.get("note") or ""] + list((definition or {}).get("deviations", []))
    return any("blocking" in t.lower() for t in texts)


def notebook_ran(nb) -> bool:
    """True when the notebook executed to the end in this run."""
    return bool(nb) and nb["status"] not in ("error", "not_run")


def notebook_for(row, std_id, nb_manifest):
    """Manifest entry of the row's notebook; a stub when a covered notebook has none."""
    nb = find_notebook(row, std_id, nb_manifest)
    if nb or not row.get("notebook"):
        return nb
    return {"path": row["notebook"], "status": "not_run", "error_summary": None}


def find_notebook(row, std_id, nb_manifest):
    for nb in (nb_manifest or {}).get("notebooks", []):
        if row.get("notebook") and nb["path"] == row["notebook"]:
            return nb
    for nb in (nb_manifest or {}).get("notebooks", []):
        for t in nb.get("tests", []):
            if (t["standard"], t["test"]) != (std_id, row["id"]):
                continue
            if t.get("name") in (None, row["name"]) or nb["path"] == row.get(
                "notebook"
            ):
                return nb
    return None


# ----------------------------------------------------------------- scenarios


def read_zip_member(zf: zipfile.ZipFile, name: str):
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1] == name:
            return zf.read(n)
    return None


def polygons_of(section) -> list:
    out = []
    for item in (section or {}).values():
        coords = item.get("coordinates") if isinstance(item, dict) else None
        if coords and len(coords) >= 2:
            out.append(coords)
    return out


def draw_polys(ax, polys, color, label, hatch=None):
    first = True
    for coords in polys:
        xs, ys = [p[0] for p in coords], [p[1] for p in coords]
        kw = {"label": label} if first else {}
        first = False
        if len(coords) < 3:
            ax.plot(xs, ys, color=color, lw=3, **kw)
            continue
        ax.fill(
            xs,
            ys,
            facecolor=color,
            alpha=0.35,
            edgecolor=color,
            hatch=hatch,
            lw=1.2,
            **kw,
        )


def draw_scenario(ax, zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            geom = read_zip_member(zf, "geometry.wkt")
            cfg = read_zip_member(zf, "config.json")
        area = wkt.loads(geom.decode())
        config = json.loads(cfg) if cfg else {}
    except (OSError, zipfile.BadZipFile, AttributeError, ValueError, KeyError):
        return False
    for poly in getattr(area, "geoms", [area]):
        ax.fill(*poly.exterior.xy, facecolor="#e8e8e8", edgecolor="#333333", lw=1.5)
        for hole in poly.interiors:
            ax.fill(*hole.xy, facecolor="white", edgecolor="#333333", lw=1.5)
    draw_polys(ax, polygons_of(config.get("distributions")), "#0072B2", "Start area")
    draw_polys(ax, polygons_of(config.get("exits")), "#009E73", "Exit", hatch="//")
    draw_polys(ax, polygons_of(config.get("zones")), "#E69F00", "Zone")
    draw_polys(ax, polygons_of(config.get("checkpoints")), "#CC79A7", "Checkpoint")
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title(zip_path.stem, fontsize=9)
    return True


def render_scenarios(zip_paths: list[Path], out_file: Path) -> bool:
    paths = [p for p in zip_paths if p.is_file()][:MAX_PANELS]
    if not paths:
        return False
    cols = min(len(paths), 2)
    nrows = -(-len(paths) // cols)
    fig, axes = plt.subplots(
        nrows, cols, figsize=(5.2 * cols, 3.0 * nrows), squeeze=False
    )
    drawn = 0
    for ax, p in zip(axes.flat, paths):
        drawn += draw_scenario(ax, p)
    for ax in list(axes.flat)[len(paths) :]:
        ax.axis("off")
    handles = {}
    for ax in axes.flat:
        for h, lab in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(lab, h)
    if handles:
        fig.legend(
            handles.values(),
            handles.keys(),
            loc="lower center",
            ncol=len(handles),
            fontsize=8,
        )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(out_file, dpi=110)
    plt.close(fig)
    return drawn > 0


# ----------------------------------------------------------------- helpers


def pick_versions(results, nb_manifest) -> dict:
    keys = {
        "jupedsim": ("jupedsim_version", "jupedsim"),
        "jupedsim-scenarios": ("jupedsim_scenarios_version", "jupedsim-scenarios"),
        "pedpy": ("pedpy_version", "pedpy"),
    }
    nb_versions = ((nb_manifest or {}).get("provenance") or {}).get("versions") or {}
    out = {}
    for name, (rkey, nkey) in keys.items():
        val = next((r[rkey] for r in results or [] if r.get(rkey)), None)
        out[name] = val or nb_versions.get(nkey)
    return out


def format_time(ts) -> dict | None:
    """Return {"iso": ms-precision ISO string, "text": "YYYY-MM-DD HH:MM UTC"}."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return {"iso": str(ts), "text": str(ts)}
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return {
        "iso": dt.isoformat(timespec="milliseconds"),
        "text": dt.strftime("%Y-%m-%d %H:%M UTC"),
    }


def pick_run(results, nb_manifest) -> dict:
    nb_prov = (nb_manifest or {}).get("provenance") or {}
    run = {}
    for key in ("commit_sha", "run_id", "timestamp"):
        val = next((r[key] for r in results or [] if r.get(key)), None)
        run[key] = val or nb_prov.get(key)
    run["local"] = not any(run.values())
    run["time"] = format_time(run["timestamp"])
    return run


def pages_commit(repo_root) -> str | None:
    """Commit the site is generated from: GITHUB_SHA, else git HEAD, else None."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def fmt_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(fmt_value(x) for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


MIN_SIG_DIGITS = 4


def fmt_number(v, bounds=()) -> str:
    """Format a number with at least 4 significant digits.

    More digits are added until the printed value compares to every bound
    (<, ==, >) the way the exact value does, so rounding never changes a
    pass or fail reading.
    """
    if not isinstance(v, float):
        return fmt_value(v)
    for digits in range(MIN_SIG_DIGITS, 18):
        text = f"{v:.{digits}g}"
        shown = float(text)
        if all((shown > b) - (shown < b) == (v > b) - (v < b) for b in bounds):
            return text
    return repr(v)


def fmt_measured(v, bounds=()) -> str:
    if isinstance(v, list):
        return ", ".join(fmt_number(x) for x in v)
    return fmt_number(v, bounds)


def numeric_bounds(*values) -> tuple:
    return tuple(
        float(b)
        for b in values
        if isinstance(b, (int, float)) and not isinstance(b, bool)
    )


NOT_REACHED = "\u2014 (not reached)"


def fmt_item(v) -> str:
    return NOT_REACHED if v is None else fmt_measured(v)


def fmt_count(v) -> str:
    return fmt_value(int(v) if isinstance(v, float) else v)


def result_view(r) -> dict:
    measured = r.get("measured")
    bounds = numeric_bounds(r.get("lower"), r.get("upper"))
    items = []
    if isinstance(measured, dict):
        items = [(str(k), fmt_item(x)) for k, x in measured.items()]
        measured = None
    return {
        "test": r["test_id"].rsplit("::", 1)[-1],
        "test_id": r["test_id"],
        "status": CASE_LABEL.get(r["status"], r["status"].upper()),
        "quantity": r.get("quantity") or "",
        "measured": fmt_measured(measured, bounds),
        "items": items,
        "unit": r.get("unit") or "",
        "lower": fmt_value(r.get("lower")),
        "upper": fmt_value(r.get("upper")),
        "n": fmt_count(r.get("n")),
        "seed": fmt_count(r.get("seed")),
        "rule": r.get("rule") or "",
    }


def copy_figures(nb_dir, rel_paths, dest_dir: Path, rel_prefix: str) -> list[str]:
    out = []
    for rel in rel_paths or []:
        src = Path(nb_dir) / rel
        if not src.is_file():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / src.name)
        out.append(f"{rel_prefix}{src.name}")
    return out


# ----------------------------------------------------------------- build


def collect_standards(manifest, definitions, results, nb_manifest):
    """Join the inputs into one entry per coverage row, grouped by standard."""
    def_by_key = {}
    for d in definitions.get("tests", []):
        c = d.get("coverage", {})
        def_by_key.setdefault((d["standard"], c.get("id")), []).append(d)
    slugs = derive_slugs(manifest, def_by_key)
    assigned = assign_results(manifest, results)
    matched_ids, no_results, standards = set(), [], []

    for std in manifest["standards"]:
        std_def = definitions.get("standards", {}).get(std["id"], {})
        group_titles = {g["id"]: g["title"] for g in std_def.get("groups", [])}
        entries = []
        for i, row in enumerate(std["tests"]):
            definition = find_definition(def_by_key, std["id"], row) or {}
            res_rows = assigned.get((std["id"], i), [])
            matched_ids.update(r["test_id"] for r in res_rows)
            if (
                results is not None
                and not res_rows
                and row["status"] in ("covered", "xfail")
            ):
                no_results.append(f"{std['id']} {row['id']} ({row['name']})")
            status, notes = derive_status(row, res_rows)
            entries.append(
                {
                    "slug": slugs[(std["id"], i)],
                    "row": row,
                    "definition": definition,
                    "title": definition.get("title") or row["name"],
                    "group": definition.get("group"),
                    "status": status,
                    "notes": notes,
                    "blocking": blocking_documented(row, definition),
                    "res_rows": res_rows,
                    "notebook": notebook_for(row, std["id"], nb_manifest),
                }
            )
        standards.append(
            {
                "std": std,
                "std_def": std_def,
                "entries": entries,
                "group_titles": group_titles,
            }
        )
    orphans = sorted(
        r["test_id"] for r in results or [] if r["test_id"] not in matched_ids
    )
    return standards, orphans, no_results


def reference_of(s) -> dict:
    d = s["std_def"]
    doi = d.get("doi")
    link = f"https://doi.org/{doi}" if doi else d.get("url")
    return {
        "citation": d.get("citation") or s["std"]["full_name"],
        "link": link,
        "link_text": doi or link,
    }


def summarize(standards) -> list[dict]:
    out = []
    for s in standards:
        counts = {k: 0 for k in STATUS_ORDER}
        for e in s["entries"]:
            counts[e["status"]] += 1
        blocking = sum(1 for e in s["entries"] if e["blocking"])
        out.append(
            {
                "std": s["std"],
                "counts": counts,
                "total": len(s["entries"]),
                "blocking": blocking,
            }
        )
    return out


def group_entries(s) -> list[dict]:
    groups = [{"title": t, "entries": []} for t in s["group_titles"].values()]
    by_id = dict(zip(s["group_titles"], groups))
    other = {"title": "Other tests", "entries": []}
    for e in s["entries"]:
        by_id.get(e["group"], other)["entries"].append(e)
    return [g for g in groups + [other] if g["entries"]]


# ----------------------------------------------------------------- markdown


def md(text) -> str:
    """Escape free text so Markdown shows it literally (single line)."""
    s = " ".join(str(text if text is not None else "").split())
    s = re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", s)
    return s.replace("{", "&#123;").replace("}", "&#125;")


def md_link(text, url) -> str:
    return f"[{md(text)}]({url})"


def front_matter(**fields) -> str:
    """YAML front matter; JSON scalars and objects are valid YAML."""
    lines = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def shortcode_arg(value) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def status_chip(status: str) -> str:
    return f"{{{{< status {shortcode_arg(status)} >}}}}"


def reference_line(std, std_def) -> str:
    text = f"Reference: {md(std_def.get('citation') or std['full_name'])}"
    if std_def.get("url"):
        text += " " + md_link(std_def.get("doi") or std_def["url"], std_def["url"])
    if std_def.get("page_note"):
        text += f" ({md(std_def['page_note'])})"
    return text


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ----------------------------------------------------------------- summary


def card_markup(item) -> str:
    std, counts = item["std"], item["counts"]
    args = [
        f"name={shortcode_arg(std['name'])}",
        f"full={shortcode_arg(std['full_name'])}",
        f"url={shortcode_arg('standards/' + std['id'] + '/')}",
        f"total={shortcode_arg(item['total'])}",
        f"blocking={shortcode_arg(item['blocking'])}",
    ]
    for st in STATUS_ORDER:
        key = st.lower().replace("-", "_").replace(" ", "_")
        args.append(f"{key}={shortcode_arg(counts[st])}")
    return "{{< vv-card " + " ".join(args) + " >}}"


def summary_page(model) -> str:
    lines = [
        front_matter(title="JuPedSim verification"),
        INTRO,
        "",
    ]
    if not model["has_results"]:
        lines += ["Test results are not available in this run.", ""]
    if not model["has_notebooks"]:
        lines += ["Executed notebooks are not available in this run.", ""]
    cards = "\n".join(card_markup(i) for i in summarize(model["standards"]))
    lines += ["{{< vv-cards >}}", cards, "{{< /vv-cards >}}", ""]
    lines += ["{{< vv-legend >}}", "", "## References {#references}", ""]
    for s in model["standards"]:
        r = reference_of(s)
        item = f"- {md(r['citation'])}"
        if r["link"]:
            item += " " + md_link(r["link_text"], r["link"])
        lines.append(item)
    return "\n".join(lines) + "\n"


def join_diagnostics(orphans, no_results) -> str:
    """Maintainer check as a markdown section for the CI run summary."""
    lines = [
        (
            f"## Join diagnostics: {len(orphans)} unmapped result rows, "
            f"{len(no_results)} covered rows without results"
        ),
        "",
    ]
    for title, items in (
        ("Result rows mapped to no coverage row", orphans),
        ("Covered rows with no result rows", no_results),
    ):
        if items:
            lines += [f"### {title}", ""] + [f"- `{t}`" for t in items] + [""]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- standards


def standard_page(s, weight: int) -> str:
    std, std_def = s["std"], s["std_def"]
    title = std_def.get("title") or std["full_name"]
    if std_def.get("edition"):
        title += f" ({std_def['edition']})"
    lines = [
        front_matter(
            title=std["name"],
            weight=weight,
            cascade={"type": "docs"},
        ),
        md(title),
        "",
    ]
    if std_def.get("citation"):
        lines += [reference_line(std, std_def), ""]
    lines += [f"> {md(BANNER)}", ""]
    for g in group_entries(s):
        lines += [
            f"## {md(g['title'])}",
            "",
            "| Test | Title | Status | Page |",
            "|---|---|---|---|",
        ]
        for e in g["entries"]:
            status = status_chip(e["status"])
            if e["blocking"]:
                status += " blocking documented"
            page = md_link("Open test page", f"{e['slug']}/")
            lines.append(
                f"| {md(e['row']['id'])} | {md(e['title'])} | {status} | {page} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def figure(path: str, alt: str, caption: str) -> list[str]:
    return [f"![{md(alt)}]({path})", "", f"*{md(caption)}*", ""]


def copy_notebook(std_id, e, nb_dir, static_dir: Path, commit):
    """Copy the executed HTML under static/ and return the page's notebook info."""
    nb, row = e["notebook"], e["row"]
    if not nb:
        return None
    html_src = (
        Path(nb_dir) / nb["html"] if notebook_ran(nb) and nb.get("html") else None
    )
    html_path = None
    if html_src and html_src.is_file():
        html_path = f"notebooks/{std_id}/{e['slug']}/notebook.html"
        dest = static_dir / html_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(html_src, dest)
    return {
        "status": nb["status"],
        "error": nb.get("error_summary"),
        "html_path": html_path,
        "source_url": f"{REPO}/blob/{commit}/{row.get('notebook') or nb['path']}",
    }


def bundle_figures(nb, nb_dir, pdir: Path):
    if not notebook_ran(nb):
        return [], [], []
    figs = nb.get("figures", {})
    return tuple(
        copy_figures(nb_dir, figs.get(kind), pdir / "figures", "figures/")
        for kind in ("scenario", "result", "other")
    )


def first_test_source(e):
    return next((r["test_source"] for r in e["res_rows"] if r.get("test_source")), None)


def esc(text) -> str:
    """Escape free text for raw HTML in Markdown, including Hugo shortcode braces."""
    out = html.escape(" ".join(str(text if text is not None else "").split()))
    return out.replace("{", "&#123;").replace("}", "&#125;")


def with_unit(text: str, unit: str) -> str:
    """Append the unit; "1" marks a dimensionless quantity and is not printed."""
    return f"{text} {esc(unit)}" if unit and unit != "1" else text


def accepted_range(v) -> str:
    lower, upper = v["lower"], v["upper"]
    if lower and upper:
        return with_unit(f"{esc(lower)} \u2013 {esc(upper)}", v["unit"])
    if lower:
        return with_unit(f"\u2265 {esc(lower)}", v["unit"])
    if upper:
        return with_unit(f"\u2264 {esc(upper)}", v["unit"])
    return ""


def result_cell(v) -> str:
    quantity, unit = esc(v["quantity"]), v["unit"]
    parts = []
    if v["items"]:
        head = f"{quantity} ({esc(unit)})" if quantity and unit else quantity
        pairs = "<br>".join(f"{esc(k)}: {esc(x)}" for k, x in v["items"])
        parts.append(f"<strong>{head}</strong><br>{pairs}" if head else pairs)
    elif v["measured"]:
        value = with_unit(esc(v["measured"]), unit)
        parts.append(f"{quantity} = {value}" if quantity else value)
    elif quantity:
        parts.append(f"{quantity}: not reported")
    rng = accepted_range(v)
    if rng:
        parts.append(f'<span class="vv-range">accepted {rng}</span>')
    return "<br>".join(parts) or "&mdash;"


def run_cell(v) -> str:
    parts = []
    if v["n"]:
        parts.append(f'<span class="vv-nw">n = {esc(v["n"])}</span>')
    if v["seed"]:
        parts.append(f'<span class="vv-nw">seed {esc(v["seed"])}</span>')
    return " &middot; ".join(parts) or "&mdash;"


NUM = r"-?\d+(?:\.\d+)?"
BOUND_ONLY = re.compile(
    rf"bound: (?:(?P<lo>{NUM}) <= measured(?: <= (?P<hi>{NUM}))?"
    rf"|measured (?:<=|<) (?P<hi2>{NUM})|measured (?:>=|>) (?P<lo2>{NUM}))"
)


def same_number(text, shown) -> bool:
    if not text or not shown:
        return not text and not shown
    return float(text) == float(shown)


def restates_bounds(v) -> bool:
    """True when the rule only repeats the accepted range shown in the row."""
    m = BOUND_ONLY.fullmatch(v["rule"])
    if not m:
        return False
    lo = m["lo"] or m["lo2"]
    hi = m["hi"] or m["hi2"]
    return same_number(lo, v["lower"]) and same_number(hi, v["upper"])


def wrap_name(name: str) -> str:
    """Escape a test name and allow line breaks after underscores only."""
    return esc(name).replace("_", "_<wbr>")


def case_row(v) -> str:
    status = status_chip(v["status"]) if v["status"] != "SKIP" else "SKIP"
    return (
        "<tr>"
        f'<td data-label="Case"><code title="{esc(v["test_id"])}">{wrap_name(v["test"])}</code></td>'
        f'<td data-label="Status">{status}</td>'
        f'<td data-label="Result">{result_cell(v)}</td>'
        f'<td data-label="Run">{run_cell(v)}</td>'
        "</tr>"
    )


def case_table(res_views) -> list[str]:
    # No blank lines inside the HTML: Markdown ends a raw HTML block at one.
    lines = [
        "**Test cases of this row**",
        "",
        '<div class="vv-cases-wrap">',
        '<table class="vv-cases">',
        "<thead><tr><th>Case</th><th>Status</th><th>Result</th><th>Run</th></tr></thead>",
        "<tbody>",
    ]
    lines += [case_row(v) for v in res_views]
    lines += ["</tbody>", "</table>", "</div>", ""]
    if any(x == NOT_REACHED for v in res_views for _, x in v["items"]):
        lines += [
            (
                f"{NOT_REACHED} means the run recorded no value for that entry, "
                "for example because agents blocked before the end of the time budget."
            ),
            "",
        ]
    rules = [v for v in res_views if v["rule"] and not restates_bounds(v)]
    if rules:
        lines += ['<ul class="vv-rules">']
        lines += [
            f"<li><code>{wrap_name(v['test'])}</code>: {esc(v['rule'])}</li>"
            for v in rules
        ]
        lines += ["</ul>", ""]
    return lines


def result_section(e, res_views, has_results, imgs, row) -> list[str]:
    result_imgs, other_imgs = imgs
    lines = [
        "## 4. Our result {#result}",
        "",
        f"Status: {status_chip(e['status'])}",
        "",
    ]
    for n in e["notes"]:
        if n != row.get("note"):
            lines += [f"> {md(n)}", ""]
    has_measured = any(v["measured"] or v["items"] for v in res_views)
    if not has_results:
        lines += ["Test results are not available in this run.", ""]
    elif e["status"] in ("PENDING", "TEST-LOCAL") and not res_views:
        reason = (
            "this test is not covered yet"
            if e["status"] == "PENDING"
            else "this test does not measure simulator output"
        )
        lines += [f"No measured values: {reason}.", ""]
    elif not has_measured:
        lines += ["Measured values not reported yet.", ""]
    if res_views:
        lines += case_table(res_views)
    for img in result_imgs:
        lines += figure(
            img,
            f"Result figure for {row['id']}",
            "Result figure from the executed notebook.",
        )
    if not result_imgs:
        for img in other_imgs:
            lines += figure(img, f"Notebook figure for {row['id']}", "Notebook figure.")
    return lines


def implementation_section(e, model_name, test_src, commit) -> list[str]:
    row, definition = e["row"], e["definition"]
    lines = ["## 5. Our implementation {#implementation}", ""]
    if definition.get("deviations"):
        lines += ["Deviations from the standard:", ""]
        lines += [f"- {md(d)}" for d in definition["deviations"]]
    else:
        lines.append("No deviations listed yet.")
    lines += ["", f"Model: {md(model_name or 'not available in this run')}", ""]
    if test_src:
        url = f"{REPO}/blob/{commit}/{test_src.split('::')[0]}"
        lines.append(f"- Test function: {md_link(test_src, url)}")
    elif row.get("test"):
        lines.append(f"- Test: `{row['test']}` (source link not available in this run)")
    else:
        lines.append("- No test function for this row.")
    for p in definition.get("scenario_files", []):
        lines.append(
            f"- Scenario file: {md_link(Path(p).name, f'{REPO}/blob/{commit}/{p}')}"
        )
    return lines + [""]


def notebook_section(nb) -> list[str]:
    lines = ["## 6. Our notebook {#notebook}", ""]
    if not nb:
        return lines + ["No notebook for this test yet.", ""]
    if nb["status"] == "acceptance_failed":
        lines += [
            f"{status_chip('FAIL')} The acceptance check of this notebook failed in this run.",
            "",
        ]
    elif not notebook_ran(nb):
        detail = f": {md(nb['error'])}" if nb["error"] else ""
        lines += [
            f"{status_chip('NOT RUN')} The notebook did not execute in this run{detail}.",
            "",
        ]
    if nb["html_path"]:
        link = f'{{{{< static-link {shortcode_arg(nb["html_path"])} "Executed notebook (HTML)" >}}}}'
        lines.append(f"- {link}")
    lines.append(f"- {md_link('Notebook source (.ipynb) on GitHub', nb['source_url'])}")
    return lines + [""]


def test_page(ctx, s, e, weight, pdir: Path) -> str:
    row, definition, std, std_def = e["row"], e["definition"], s["std"], s["std_def"]
    commit, has_results = ctx["commit"], ctx["has_results"]
    nb = copy_notebook(std["id"], e, ctx["nb_dir"], ctx["static_dir"], commit)
    scenario_imgs, result_imgs, other_imgs = bundle_figures(
        e["notebook"], ctx["nb_dir"], pdir
    )
    scenario_note = None
    zip_paths = [ctx["repo_root"] / p for p in definition.get("scenario_files", [])]
    if not scenario_imgs and render_scenarios(zip_paths, pdir / "scenario.png"):
        scenario_imgs = ["scenario.png"]
        scenario_note = (
            "Rendered from the scenario files of this repository (top view)."
        )
    res_views = [result_view(r) for r in e["res_rows"]]
    model_name = next((r["model"] for r in e["res_rows"] if r.get("model")), None)

    title = f"{row['id']}: {e['title']}"
    lines = [front_matter(title=title, linkTitle=title, weight=weight)]
    blocking = " blocking documented (see note)" if e["blocking"] else ""
    lines += [f"{status_chip(e['status'])}{blocking}", ""]

    lines += ["## 1. Definition {#definition}", ""]
    if definition.get("definition"):
        lines += [md(definition["definition"]), ""]
        lines += [
            (
                f"Source: {md(definition.get('source'))}. "
                "This is a paraphrase, not the text of the standard."
            ),
            "",
        ]
    else:
        lines += ["Definition text not written yet for this test.", ""]
    lines += [reference_line(std, std_def), ""]

    lines += ["## 2. Scenario {#scenario}", ""]
    for img in scenario_imgs:
        lines += figure(
            img,
            f"Top view of the scenario for {row['id']}",
            scenario_note or "Scenario figure from the executed notebook.",
        )
    if not scenario_imgs:
        lines += ["Scenario picture not available.", ""]

    lines += ["## 3. What to expect {#expect}", ""]
    if definition.get("expected"):
        lines += [md(definition["expected"]), ""]
    lines += [f"**Our criterion:** {md(row['criterion'])}", ""]
    if row.get("note"):
        lines += [f"> {md(row['note'])}", ""]

    lines += result_section(e, res_views, has_results, (result_imgs, other_imgs), row)
    lines += implementation_section(e, model_name, first_test_source(e), commit)
    lines += notebook_section(nb)
    return "\n".join(lines) + "\n"


def write_standard(ctx, s, weight: int):
    sdir = ctx["content_dir"] / "standards" / s["std"]["id"]
    write(sdir / "_index.md", standard_page(s, weight))
    for i, e in enumerate(s["entries"], start=1):
        pdir = sdir / e["slug"]
        pdir.mkdir(parents=True, exist_ok=True)
        write(pdir / "index.md", test_page(ctx, s, e, i, pdir))


def standards_index(standards) -> str:
    lines = [
        front_matter(title="Standards", weight=2, cascade={"type": "docs"}),
        "Test pages of the four published test suites.",
        "",
    ]
    for s in standards:
        std = s["std"]
        lines.append(
            f"- {md_link(std['name'], std['id'] + '/')}: {md(std['full_name'])}"
        )
    return "\n".join(lines) + "\n"


def run_data(model) -> dict:
    return {
        "versions": [{"name": k, "version": v} for k, v in model["versions"].items()],
        "run": model["run"],
    }


def clean_generated(content_dir: Path, static_dir: Path):
    for path in (content_dir / "standards", static_dir / "notebooks"):
        shutil.rmtree(path, ignore_errors=True)


def build_site(
    manifest, definitions, results, nb_manifest, nb_dir, content_dir: Path, repo_root
):
    """Write Hugo content, data/run.json and static/notebooks next to it."""
    site_dir = content_dir.parent
    static_dir, data_dir = site_dir / "static", site_dir / "data"
    standards, orphans, no_results = collect_standards(
        manifest, definitions, results, nb_manifest
    )
    run = pick_run(results, nb_manifest)
    pages_sha = pages_commit(repo_root)
    run["pages_commit_sha"] = pages_sha if pages_sha != run["commit_sha"] else None
    model = {
        "standards": standards,
        "orphans": orphans,
        "no_results": no_results,
        "versions": pick_versions(results, nb_manifest),
        "run": run,
        "has_results": results is not None,
        "has_notebooks": nb_manifest is not None,
    }
    ctx = {
        "commit": run["commit_sha"] or "main",
        "has_results": model["has_results"],
        "nb_dir": nb_dir,
        "repo_root": repo_root,
        "content_dir": content_dir,
        "static_dir": static_dir,
    }
    clean_generated(content_dir, static_dir)
    write(content_dir / "_index.md", summary_page(model))
    write(content_dir / "standards" / "_index.md", standards_index(standards))
    for weight, s in enumerate(standards, start=1):
        write_standard(ctx, s, weight)
    write(data_dir / "run.json", json.dumps(run_data(model), indent=2) + "\n")
    return standards, orphans, no_results


INTRO = (
    "Verification tests of [JuPedSim](https://www.jupedsim.org) against the test cases of "
    "IMO MSC.1/Circ.1533, RiMEA 4.1.1, NIST TN 1822 and ISO 20414:2020. Each test page gives "
    "the definition, the scenario, our result and the notebook. It also states the pass rule "
    "and whether the standard gives that rule or the suite chose it. The results come from the "
    "run named in the footer, not from a live check. This is verification, not validation: no "
    "comparison with experimental data has been run. Code and scenarios: "
    "[jupedsim-web-community on GitHub](https://github.com/PedestrianDynamics/jupedsim-web-community)."
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--definitions", type=Path)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--notebooks", type=Path)
    ap.add_argument(
        "--hugo-content",
        type=Path,
        required=True,
        help="content directory of the Hugo site (for example site/content)",
    )
    args = ap.parse_args(argv)
    manifest, definitions, results, nb_manifest, nb_dir = load_inputs(args)
    args.hugo_content.mkdir(parents=True, exist_ok=True)
    repo_root = args.manifest.resolve().parent.parent
    _, orphans, no_results = build_site(
        manifest,
        definitions,
        results,
        nb_manifest,
        nb_dir,
        args.hugo_content,
        repo_root,
    )
    for t in orphans:
        print(f"join: results row maps to no coverage row: {t}")
    for t in no_results:
        print(f"join: covered row has no results rows: {t}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(join_diagnostics(orphans, no_results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
