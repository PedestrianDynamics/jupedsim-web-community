#!/usr/bin/env python3
"""Generate the static public V&V site.

Inputs:
  --manifest     standards/coverage.json (one row per standard test)
  --definitions  standards/definitions.json (per-test page content)
  --results      vv-results.json from a full pytest run (optional)
  --notebooks    directory holding the executed-notebook manifest.json (optional)

Output (under --outdir):
  index.html                          summary per standard
  standards/<std>/index.html          grouped test table
  standards/<std>/<slug>/index.html   one self-contained page per test
  assets/                             style.css and the JuPedSim logo

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
import json
import re
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import jinja2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely import wkt

REPO = "https://github.com/PedestrianDynamics/jupedsim-web-community"
LOGO = Path(__file__).resolve().parent / "assets" / "jupedsim_logo.svg"
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


def notebook_for(row, std_id, nb_manifest):
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


def fmt_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(fmt_value(x) for x in v)
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def result_view(r) -> dict:
    return {
        "test": r["test_id"].split("::", 1)[-1],
        "status": CASE_LABEL.get(r["status"], r["status"].upper()),
        "quantity": r.get("quantity") or "",
        "measured": fmt_value(r.get("measured")),
        "unit": r.get("unit") or "",
        "lower": fmt_value(r.get("lower")),
        "upper": fmt_value(r.get("upper")),
        "n": fmt_value(int(r["n"]) if isinstance(r.get("n"), float) else r.get("n")),
        "seed": fmt_value(
            int(r["seed"]) if isinstance(r.get("seed"), float) else r.get("seed")
        ),
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


def build_pages(
    manifest, definitions, results, nb_manifest, nb_dir, outdir: Path, repo_root: Path
):
    def_by_key = {}
    for d in definitions.get("tests", []):
        c = d.get("coverage", {})
        def_by_key.setdefault((d["standard"], c.get("id")), []).append(d)
    slugs = derive_slugs(manifest, def_by_key)
    assigned = assign_results(manifest, results)
    versions = pick_versions(results, nb_manifest)
    run = pick_run(results, nb_manifest)
    commit = run["commit_sha"] or "main"
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
    env = make_env()
    ctx = {
        "versions": versions,
        "run": run,
        "banner": BANNER,
        "repo_root": repo_root,
        "repo": REPO,
        "nav": [
            {
                "id": s["std"]["id"],
                "name": s["std"]["name"],
                "full_name": s["std"]["full_name"],
            }
            for s in standards
        ],
    }
    write_assets(outdir)
    for s in standards:
        write_standard_pages(env, ctx, s, outdir, commit, nb_dir, results is not None)
    summary = summarize(standards)
    write(
        outdir / "index.html",
        env.get_template("index.html").render(
            **ctx,
            root="",
            title="Verification and validation",
            summary=summary,
            has_results=results is not None,
            has_notebooks=nb_manifest is not None,
            orphans=orphans,
            no_results=no_results,
            order=STATUS_ORDER,
            refs=[reference_of(s) for s in standards],
        ),
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


def write_standard_pages(env, ctx, s, outdir, commit, nb_dir, has_results):
    std = s["std"]
    sdir = outdir / "standards" / std["id"]
    write(
        sdir / "index.html",
        env.get_template("standard.html").render(
            **ctx,
            root="../../",
            title=std["name"],
            std=std,
            std_def=s["std_def"],
            groups=group_entries(s),
        ),
    )
    for e in s["entries"]:
        write_test_page(env, ctx, s, e, sdir / e["slug"], commit, nb_dir, has_results)


def write_test_page(env, ctx, s, e, pdir: Path, commit, nb_dir, has_results):
    pdir.mkdir(parents=True, exist_ok=True)
    definition, row, std = e["definition"], e["row"], s["std"]
    repo_root = ctx["repo_root"]
    nb = e["notebook"]
    nb_info = None
    scenario_imgs, result_imgs, other_imgs = [], [], []
    if nb:
        figs = nb.get("figures", {})
        scenario_imgs = copy_figures(
            nb_dir, figs.get("scenario"), pdir / "figures", "figures/"
        )
        result_imgs = copy_figures(
            nb_dir, figs.get("result"), pdir / "figures", "figures/"
        )
        other_imgs = copy_figures(
            nb_dir, figs.get("other"), pdir / "figures", "figures/"
        )
        html_src = Path(nb_dir) / nb["html"] if nb.get("html") else None
        has_html = bool(html_src and html_src.is_file())
        if has_html:
            shutil.copy2(html_src, pdir / "notebook.html")
        nb_info = {
            "status": nb["status"],
            "error": nb.get("error_summary"),
            "has_html": has_html,
            "source_url": f"{REPO}/blob/{commit}/{row.get('notebook') or nb['path']}",
        }
    zip_paths = [repo_root / p for p in definition.get("scenario_files", [])]
    scenario_note = None
    if not scenario_imgs and render_scenarios(zip_paths, pdir / "scenario.png"):
        scenario_imgs = ["scenario.png"]
        scenario_note = (
            "Rendered from the scenario files of this repository (top view)."
        )
    test_src = None
    for r in e["res_rows"]:
        if r.get("test_source"):
            test_src = r["test_source"]
            break
    test_url = f"{REPO}/blob/{commit}/{test_src.split('::')[0]}" if test_src else None
    res_views = [result_view(r) for r in e["res_rows"]]
    has_measured = any(v["measured"] for v in res_views)
    write(
        pdir / "index.html",
        env.get_template("test.html").render(
            **ctx,
            root="../../../",
            title=f"{row['id']}: {e['title']}",
            std=std,
            std_def=s["std_def"],
            e=e,
            row=row,
            definition=definition,
            scenario_imgs=scenario_imgs,
            scenario_note=scenario_note,
            result_imgs=result_imgs,
            other_imgs=other_imgs,
            res_views=res_views,
            has_measured=has_measured,
            has_results=has_results,
            test_url=test_url,
            test_src=test_src,
            scenario_links=[
                {"name": Path(p).name, "url": f"{REPO}/blob/{commit}/{p}"}
                for p in definition.get("scenario_files", [])
            ],
            nb=nb_info,
            model=next((r["model"] for r in e["res_rows"] if r.get("model")), None),
        ),
    )


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_assets(outdir: Path):
    adir = outdir / "assets"
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "style.css").write_text(CSS, encoding="utf-8")
    if LOGO.is_file():
        shutil.copy2(LOGO, adir / "jupedsim_logo.svg")


def make_env():
    env = jinja2.Environment(
        loader=jinja2.DictLoader(TEMPLATES),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["badge_class"] = lambda s: slugify(s)
    return env


# ----------------------------------------------------------------- assets

CSS = """\
:root {
  --bg: #ffffff; --fg: #1b1f24; --muted: #4b5563; --line: #d0d7de;
  --panel: #f6f8fa; --link: #0b5cad;
  --pass-bg: #dff3e6; --pass-fg: #0b4d26;
  --fail-bg: #fbe3e3; --fail-fg: #8a1010;
  --local-bg: #e6e9fb; --local-fg: #25318f;
  --pending-bg: #fdf1d3; --pending-fg: #6b4700;
  --notrun-bg: #eceff2; --notrun-fg: #3b434c;
  --font-body: Arial, Helvetica, "Liberation Sans", sans-serif;
  --text-sm: 0.875rem;     /* 14px */
  --text-base: 1rem;       /* 16px */
  --text-lg: 1.333rem;     /* 16 x 4/3 */
  --text-xl: 1.778rem;     /* 16 x (4/3)^2 */
  --text-2xl: 2.370rem;    /* 16 x (4/3)^3 */
  --leading-body: 1.4;
  --leading-head: 1.2;
  --radius: 6px;
  --gap: 16px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14181d; --fg: #e6e9ed; --muted: #aab3bd; --line: #343c45;
    --panel: #1c2128; --link: #79b8ff;
    --pass-bg: #123522; --pass-fg: #9be3b4;
    --fail-bg: #471818; --fail-fg: #ffb3b3;
    --local-bg: #1f2650; --local-fg: #b9c3ff;
    --pending-bg: #3d3010; --pending-fg: #f3d58a;
    --notrun-bg: #272d34; --notrun-fg: #c3cad2;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font-family: var(--font-body); font-size: var(--text-base); line-height: var(--leading-body); }
a { color: var(--link); }
header.site, footer.site { background: var(--panel); border-color: var(--line);
  border-style: solid; border-width: 0 0 1px; padding: 12px 16px; }
footer.site { border-width: 1px 0 0; margin-top: 48px; color: var(--muted); font-size: var(--text-sm); }
footer.site .bar { align-items: baseline; }
footer.site time { white-space: nowrap; }
footer.site .diag { flex-basis: 100%; }
footer.site .diag summary { cursor: pointer; padding: 6px 0; }
footer.site .diag p { margin: 4px 0 0; }
.bar { max-width: 62rem; margin: 0 auto; display: flex; flex-wrap: wrap;
  gap: 8px 24px; align-items: center; justify-content: space-between; }
.logo { height: 34px; width: auto; display: block; }
.versions { color: var(--muted); font-size: var(--text-sm); line-height: var(--leading-body); margin: 2px 0 0; }
header.site { padding: 8px 16px; }
header.site .bar { align-items: flex-start; }
nav.main { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: var(--text-sm); padding-top: 6px; }
nav.main a { text-decoration: none; padding: 2px 0; border-bottom: 2px solid transparent; }
nav.main a:hover { text-decoration: underline; }
nav.main a[aria-current="page"] { color: var(--fg); font-weight: 700; border-bottom-color: currentColor; }
@media (prefers-color-scheme: dark) {
  header.site .logo { background: #fff; border-radius: 4px; padding: 2px 6px; }
}
main { max-width: 62rem; margin: 0 auto; padding: 8px 16px; }
nav.crumbs { font-size: 0.9rem; margin: 16px 0 0; }
h1 { font-size: var(--text-xl); line-height: var(--leading-head); margin: 0.6em 0 0.3em; }
h2 { font-size: 1.2rem; margin: 1.8em 0 0.4em; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
h3 { font-size: 1.05rem; margin: 1.4em 0 0.3em; }
.banner { border: 1px solid var(--line); border-left: 4px solid var(--link);
  background: var(--panel); padding: 8px 12px; margin: 16px 0; }
.note { border-left: 3px solid var(--line); padding: 2px 12px; color: var(--muted); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.95rem; }
caption { text-align: left; font-weight: 600; padding: 8px 0; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--panel); }
td.num { font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 4px; font-size: 0.8rem;
  font-weight: 700; letter-spacing: 0.03em; border: 1px solid currentColor; white-space: nowrap; }
.badge-pass { background: var(--pass-bg); color: var(--pass-fg); }
.badge-fail { background: var(--fail-bg); color: var(--fail-fg); }
.badge-test-local { background: var(--local-bg); color: var(--local-fg); }
.badge-pending { background: var(--pending-bg); color: var(--pending-fg); }
.badge-not-run { background: var(--notrun-bg); color: var(--notrun-fg); }
figure { margin: 12px 0; }
figure img { max-width: 100%; height: auto; background: #fff; border: 1px solid var(--line); }
figcaption { font-size: 0.875rem; color: var(--muted); }
ul.plain { padding-left: 1.2rem; }
.kv { color: var(--muted); font-size: 0.9rem; }
code { font-size: 0.9em; background: var(--panel); padding: 0 4px; border-radius: 3px; }
@media (max-width: 40rem) { h1 { font-size: var(--text-lg); } }
@media (max-width: 480px) { th, td { padding: 5px 6px; } }
.hero { margin: 32px 0 24px; }
ul.refs { overflow-wrap: anywhere; }
.hero h1 { margin: 0; }
.hero p { color: var(--muted); margin: 8px 0 0; max-width: 46rem; }
.banner.top { margin: 20px 0 24px; }
.cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--gap); margin: 0; }
@media (max-width: 40rem) {
  .hero { margin-top: 20px; }
  .cards { grid-template-columns: minmax(0, 1fr); }
}
.card { border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); padding: var(--gap);
  display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-rows: auto auto 1fr auto; gap: 4px 12px; }
.card h2 { font-size: var(--text-lg); line-height: var(--leading-head); border: 0; margin: 0; padding: 0; }
.card h2 a { text-decoration: none; }
.card h2 a:hover { text-decoration: underline; }
.card .kv { grid-column: 1 / -1; margin: 0; font-size: var(--text-sm); }
.card .count { grid-column: 2; grid-row: 1; margin: 0; font-size: var(--text-lg); font-weight: 700; line-height: var(--leading-head);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.card .count span { font-size: var(--text-sm); font-weight: 400; color: var(--muted); }
.chips { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; margin: 12px 0 0; align-content: start; }
.chips .badge { display: flex; justify-content: space-between; gap: 12px;
  padding: 3px 10px; font-size: var(--text-sm); font-variant-numeric: tabular-nums; }
.chips .zero { background: transparent; color: var(--muted); border-color: var(--line); font-weight: 400; }
.card .foot { grid-column: 1 / -1; margin: 8px 0 0; font-size: var(--text-sm); color: var(--muted); }
.legend { display: grid; gap: 8px; margin: 20px 0 0; font-size: var(--text-sm); color: var(--muted); }
.legend > span { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr); gap: 12px; align-items: baseline; }
.legend .badge { text-align: center; }
.legend b { font-weight: 400; }
"""

BASE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | JuPedSim V&amp;V</title>
<link rel="stylesheet" href="{{ root }}assets/style.css">
</head>
<body>
<header class="site"><div class="bar">
<div class="brand">
<a href="{{ root }}index.html"><img class="logo" src="{{ root }}assets/jupedsim_logo.svg" alt="JuPedSim"></a>
<p class="versions">{% for name, v in versions.items() %}{{ name }} {{ v or "not available" }}{{ " · " if not loop.last }}{% endfor %}</p>
</div>
<nav class="main" aria-label="Standards">
<a href="{{ root }}index.html"{% if std is not defined %} aria-current="page"{% endif %}>Summary</a>
{% for n in nav %}
<a href="{{ root }}standards/{{ n.id }}/index.html" title="{{ n.full_name }}"{% if std is defined and std.id == n.id %} aria-current="page"{% endif %}>{{ n.name.split()[0] }}</a>
{% endfor %}
</nav>
</div></header>
<main>
{% block body %}{% endblock %}
</main>
<footer class="site"><div class="bar"><span>
{% if run.local %}local build
{% else %}Run: commit {% if run.commit_sha %}<a href="{{ repo }}/commit/{{ run.commit_sha }}">{{ run.commit_sha[:12] }}</a>{% else %}unknown{% endif %},
{% if run.run_id %}run <a href="{{ repo }}/actions/runs/{{ run.run_id }}">{{ run.run_id }}</a>{% else %}run ID unknown{% endif %},
{% if run.time %}<time datetime="{{ run.time.iso }}">{{ run.time.text }}</time>{% else %}time unknown{% endif %}
{% endif %}</span>
<span>All numbers and figures on this site come from one run on the locked versions.</span>
{% block diagnostics %}{% endblock %}</div></footer>
</body>
</html>
"""

BADGE = """\
{% macro badge(status) %}<span class="badge badge-{{ status|badge_class }}">{{ status }}</span>{% endmacro %}
"""

INDEX = (
    """\
{% extends "base.html" %}
"""
    + BADGE
    + """\
{% block body %}
<section class="hero">
<h1>JuPedSim verification</h1>
<p>This page shows the live verification status of <a href="https://www.jupedsim.org">JuPedSim</a> against four published test suites for evacuation models: IMO MSC.1/Circ.1533, RiMEA 4.1.1, NIST TN 1822 and ISO 20414:2020. Each test is rebuilt from the standard's description, run on every change to the main branch with the versions shown above, and checked against the standard's expected result. Every test has its own page with the definition, the scenario, our result and the notebook. Failures are shown as they are. Code and scenarios: <a href="https://github.com/PedestrianDynamics/jupedsim-web-community">jupedsim-web-community on GitHub</a>.</p>
</section>
{% if not has_results %}<p class="note">Test results are not available in this run.</p>{% endif %}
{% if not has_notebooks %}<p class="note">Executed notebooks are not available in this run.</p>{% endif %}
<div class="cards">
{% for item in summary %}
<article class="card">
<h2><a href="standards/{{ item.std.id }}/index.html">{{ item.std.name }}</a></h2>
<p class="kv">{{ item.std.full_name }}</p>
<p class="count">{{ item.total }} <span>tests</span></p>
<div class="chips">
{% for st in order %}
<span class="badge badge-{{ st|badge_class }}{{ " zero" if not item.counts[st] }}">{{ st }} <span>{{ item.counts[st] }}</span></span>
{% endfor %}
</div>
<p class="foot">Blocking documented: {{ item.blocking }}</p>
</article>
{% endfor %}
</div>
<div class="legend" aria-label="Status legend">
<span><span class="badge badge-pending">PENDING</span> <b>the standard defines the test; the suite does not cover it yet</b></span>
<span><span class="badge badge-test-local">TEST-LOCAL</span> <b>asserts logic in the test file, not simulator output</b></span>
<span><span class="badge badge-not-run">NOT RUN</span> <b>no result was available in this build</b></span>
</div>
<h2 id="references">References</h2>
<ul class="plain refs">
{% for r in refs %}
<li>{{ r.citation }}{% if r.link %} <a href="{{ r.link }}">{{ r.link_text }}</a>{% endif %}</li>
{% endfor %}
</ul>
{% endblock %}
{% block diagnostics %}
{% if has_results and (orphans or no_results) %}
<details class="diag"><summary>Join diagnostics</summary>
<p>Result rows mapped to no coverage row: {{ orphans|length }}. Covered rows with no result rows: {{ no_results|length }}.</p>
</details>
{% endif %}
{% endblock %}
"""
)

STANDARD = (
    """\
{% extends "base.html" %}
"""
    + BADGE
    + """\
{% block body %}
<nav class="crumbs"><a href="{{ root }}index.html">Summary</a> / {{ std.name }}</nav>
<h1>{{ std.name }}</h1>
<p>{{ std_def.title or std.full_name }}{% if std_def.edition %} ({{ std_def.edition }}){% endif %}</p>
{% if std_def.citation %}<p class="kv">Reference: {{ std_def.citation }}
{% if std_def.url %}<a href="{{ std_def.url }}">{{ std_def.doi or std_def.url }}</a>{% endif %}</p>{% endif %}
<p class="banner">{{ banner }}</p>
{% for g in groups %}
<div class="table-wrap"><table>
<caption>{{ g.title }}</caption>
<thead><tr><th scope="col">Test</th><th scope="col">Title</th><th scope="col">Status</th><th scope="col">Page</th></tr></thead>
<tbody>
{% for e in g.entries %}
<tr><td>{{ e.row.id }}</td><td>{{ e.title }}</td>
<td>{{ badge(e.status) }}{% if e.blocking %} <span class="kv">blocking documented</span>{% endif %}</td>
<td><a href="{{ e.slug }}/index.html">Open test page</a></td></tr>
{% endfor %}
</tbody></table></div>
{% endfor %}
{% endblock %}
"""
)

TEST = (
    """\
{% extends "base.html" %}
"""
    + BADGE
    + """\
{% block body %}
<nav class="crumbs"><a href="{{ root }}index.html">Summary</a> /
<a href="../index.html">{{ std.name }}</a> / {{ row.id }}</nav>
<h1>{{ row.id }}: {{ e.title }}</h1>
<p>{{ badge(e.status) }}{% if e.blocking %} <span class="kv">blocking documented (see note)</span>{% endif %}</p>

<h2 id="definition">1. Definition</h2>
{% if definition.definition %}
<p>{{ definition.definition }}</p>
<p class="kv">Source: {{ definition.source }}. This is a paraphrase, not the text of the standard.</p>
{% else %}
<p>Definition text not written yet for this test.</p>
{% endif %}
<p class="kv">Reference: {{ std_def.citation or std.full_name }}
{% if std_def.url %}<a href="{{ std_def.url }}">{{ std_def.doi or std_def.url }}</a>{% endif %}
{% if std_def.page_note %} ({{ std_def.page_note }}){% endif %}</p>

<h2 id="scenario">2. Scenario</h2>
{% if scenario_imgs %}
{% for img in scenario_imgs %}
<figure><img src="{{ img }}" alt="Top view of the scenario for {{ row.id }}">
<figcaption>{{ scenario_note or "Scenario figure from the executed notebook." }}</figcaption></figure>
{% endfor %}
{% else %}
<p>Scenario picture not available.</p>
{% endif %}

<h2 id="expect">3. What to expect</h2>
{% if definition.expected %}<p>{{ definition.expected }}</p>{% endif %}
<p><strong>Our criterion:</strong> {{ row.criterion }}</p>
{% if row.note %}<p class="note">{{ row.note }}</p>{% endif %}

<h2 id="result">4. Our result</h2>
<p>Status: {{ badge(e.status) }}</p>
{% for n in e.notes if n != row.note %}<p class="note">{{ n }}</p>{% endfor %}
{% if not has_results %}<p>Test results are not available in this run.</p>
{% elif e.status in ("PENDING", "TEST-LOCAL") and not res_views %}
<p>No measured values: {{ "this test is not covered yet" if e.status == "PENDING" else "this test does not measure simulator output" }}.</p>
{% elif not has_measured %}<p>Measured values not reported yet.</p>{% endif %}
{% if res_views %}
<div class="table-wrap"><table>
<caption>Test cases of this row</caption>
<thead><tr><th scope="col">Case</th><th scope="col">Status</th><th scope="col">Quantity</th>
<th scope="col">Measured</th><th scope="col">Unit</th><th scope="col">Lower</th><th scope="col">Upper</th>
<th scope="col">n</th><th scope="col">Seed</th></tr></thead>
<tbody>
{% for v in res_views %}
<tr><td><code>{{ v.test }}</code></td><td>{{ v.status }}</td><td>{{ v.quantity }}</td>
<td class="num">{{ v.measured }}</td><td>{{ v.unit }}</td><td class="num">{{ v.lower }}</td>
<td class="num">{{ v.upper }}</td><td class="num">{{ v.n }}</td><td class="num">{{ v.seed }}</td></tr>
{% if v.rule %}<tr><td colspan="9" class="kv">Rule: {{ v.rule }}</td></tr>{% endif %}
{% endfor %}
</tbody></table></div>
{% endif %}
{% for img in result_imgs %}
<figure><img src="{{ img }}" alt="Result figure for {{ row.id }}"><figcaption>Result figure from the executed notebook.</figcaption></figure>
{% endfor %}
{% if not result_imgs %}{% for img in other_imgs %}
<figure><img src="{{ img }}" alt="Notebook figure for {{ row.id }}"><figcaption>Notebook figure.</figcaption></figure>
{% endfor %}{% endif %}

<h2 id="implementation">5. Our implementation</h2>
{% if definition.deviations %}
<p>Deviations from the standard:</p>
<ul class="plain">{% for d in definition.deviations %}<li>{{ d }}</li>{% endfor %}</ul>
{% else %}<p>No deviations listed yet.</p>{% endif %}
<p>Model: {{ model or "not available in this run" }}</p>
<ul class="plain">
{% if test_url %}<li>Test function: <a href="{{ test_url }}">{{ test_src }}</a></li>
{% elif row.test %}<li>Test: <code>{{ row.test }}</code> (source link not available in this run)</li>
{% else %}<li>No test function for this row.</li>{% endif %}
{% for l in scenario_links %}<li>Scenario file: <a href="{{ l.url }}">{{ l.name }}</a></li>{% endfor %}
</ul>

<h2 id="notebook">6. Our notebook</h2>
{% if nb %}
{% if nb.status == "acceptance_failed" %}<p>{{ badge("FAIL") }} The acceptance check of this notebook failed in this run.</p>
{% elif nb.status == "error" %}<p>{{ badge("FAIL") }} The notebook did not execute{% if nb.error %}: {{ nb.error }}{% endif %}.</p>{% endif %}
<ul class="plain">
{% if nb.has_html %}<li><a href="notebook.html">Executed notebook (HTML)</a></li>{% endif %}
<li><a href="{{ nb.source_url }}">Notebook source (.ipynb) on GitHub</a></li>
</ul>
{% else %}<p>No notebook for this test yet.</p>{% endif %}
{% endblock %}
"""
)

TEMPLATES = {
    "base.html": BASE,
    "index.html": INDEX,
    "standard.html": STANDARD,
    "test.html": TEST,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--definitions", type=Path)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--notebooks", type=Path)
    ap.add_argument("--outdir", type=Path, required=True)
    args = ap.parse_args(argv)
    manifest, definitions, results, nb_manifest, nb_dir = load_inputs(args)
    args.outdir.mkdir(parents=True, exist_ok=True)
    repo_root = args.manifest.resolve().parent.parent
    build = build_pages(
        manifest, definitions, results, nb_manifest, nb_dir, args.outdir, repo_root
    )
    _, orphans, no_results = build
    for t in orphans:
        print(f"join: results row maps to no coverage row: {t}", file=sys.stderr)
    for t in no_results:
        print(f"join: covered row has no results rows: {t}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
