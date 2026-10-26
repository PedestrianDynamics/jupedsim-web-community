#!/usr/bin/env python3
"""Generate the standards coverage report from the manifest + pytest results.

Reads:
  - standards/coverage.json  (single source of truth: every test each standard
    defines, with declared status and the pytest class it maps to)
  - a JUnit XML from `pytest tests/vv` (live PASS/FAIL/XFAIL/SKIP), optional

Writes:
  - <outdir>/index.html  : self-contained GitHub Pages dashboard
  - <outdir>/summary.md  : Markdown mirror for the pinned tracking issue (#67)

Stdlib only, so CI's plain python3 can run it. For pytest-backed rows the live
JUnit result overrides the manifest's declared status; notebook/pending rows
keep their declared status.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import html
import json
import pathlib
import xml.etree.ElementTree as ET

REPO = "https://github.com/PedestrianDynamics/jupedsim-web-community"
LOGO = pathlib.Path(__file__).resolve().parent / "assets" / "jupedsim_logo.svg"


def logo_img() -> str:
    """Return the JuPedSim wordmark as an inline <img> (data URI) so the page
    stays self-contained. Empty string if the asset is missing."""
    if not LOGO.exists():
        return ""
    b64 = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    return (
        '<a class=logolink href="https://www.jupedsim.org" '
        'title="JuPedSim — open-source pedestrian dynamics simulator">'
        f'<img class=logo alt="JuPedSim" src="data:image/svg+xml;base64,{b64}"></a>'
    )

# status -> (label, css class). "covered" is the resolved-pass state.
STATUS_META = {
    "pass": ("PASS", "pass"),
    "fail": ("FAIL", "fail"),
    "xfail": ("XFAIL", "xfail"),
    "xpass": ("XPASS", "fail"),
    "skip": ("SKIP", "skip"),
    "pending": ("PENDING", "pending"),
    "notebook": ("NOTEBOOK", "notebook"),
    "test-local": ("TEST-LOCAL", "test-local"),
    "not-supported": ("NOT SUPPORTED", "not-supported"),
}


def parse_junit(xml_path: pathlib.Path) -> dict:
    """Return {(class_name, method_name): status} where status is one of
    pass/fail/xfail/xpass/skip. class_name is the last dotted component of the
    JUnit classname (e.g. 'TestNist52MaxFlow')."""
    results: dict = {}
    if not xml_path or not xml_path.exists():
        return results
    tree = ET.parse(xml_path)
    for tc in tree.iter("testcase"):
        cls = tc.get("classname", "").split(".")[-1]
        name = tc.get("name", "")
        # pytest encodes the parametrization in name like 'test_x[uniform]';
        # keep the base method name for matching.
        base = name.split("[")[0]
        skipped = tc.find("skipped")
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "fail"
        elif skipped is not None:
            kind = (skipped.get("type", "") + " " + skipped.get("message", "")).lower()
            status = "xfail" if "xfail" in kind else "skip"
        else:
            status = "pass"
        # xpass (strict xfail that passed) surfaces as a failure in JUnit, so
        # it is already caught above.
        results[(cls, base)] = status
    return results


def resolve(entry: dict, junit: dict) -> str:
    """Resolve a manifest entry to a live status token."""
    declared = entry.get("status", "pending")
    if declared in ("pending", "notebook", "not-supported"):
        return declared
    # "covered" is the manifest's word for "expected to pass"; the render/count
    # token for a pass is "pass".
    fallback = "pass" if declared == "covered" else declared
    ref = entry.get("test")
    if not ref:
        return fallback
    cls, _, method = ref.partition("::")
    matches = [
        st
        for (c, m), st in junit.items()
        if c == cls and (not method or m == method)
    ]
    if not matches:
        # No live result (suite not run / test missing) -> fall back to intent.
        return fallback
    if any(st == "fail" for st in matches):
        return "fail"
    if declared == "test-local":
        return declared
    if all(st == "xfail" for st in matches):
        return "xfail"
    if any(st == "pass" for st in matches):
        return "pass"
    if any(st == "skip" for st in matches):
        return "skip"
    return matches[0]


def summarise(standard: dict, junit: dict) -> dict:
    rows = []
    counts = {"pass": 0, "fail": 0, "xfail": 0, "skip": 0, "pending": 0, "notebook": 0, "test-local": 0, "not-supported": 0,}
    for t in standard["tests"]:
        st = resolve(t, junit)
        counts[st] = counts.get(st, 0) + 1
        rows.append((t, st))
    # Denominator is the number of rows we track (each row is one test/variant),
    # so the coverage bar always sums to exactly 100%.
    total = len(standard["tests"])
    # "covered" = a live pass, or a demonstrated notebook row.
    covered = counts["pass"] + counts["notebook"]
    return {"rows": rows, "counts": counts, "total": total, "covered": covered}


# --------------------------------------------------------------------------- #
# Markdown (pinned issue #67)
# --------------------------------------------------------------------------- #

MD_ICON = {
    "pass": ":white_check_mark:",
    "fail": ":x:",
    "xfail": ":warning:",
    "skip": ":fast_forward:",
    "pending": ":hourglass_flowing_sand:",
    "notebook": ":ledger:",
    "test-local": ":label:",
    "not-supported": "no_entry_sign",
}


def render_markdown(data: list, stamp: str) -> str:
    out = ["## V&V Standards Coverage\n", f"*Last updated: {stamp}*\n"]
    for std, s in data:
        out.append(f"### {std['name']} — {s['covered']}/{s['total']} covered")
        out.append("")
        out.append("| Test | Criterion | Status |")
        out.append("|------|-----------|--------|")
        for t, st in s["rows"]:
            label = STATUS_META[st][0]
            out.append(
                f"| {t['id']} {t['name']} | {t.get('criterion', '')} | {MD_ICON[st]} {label} |"
            )
        out.append("")
    # legend
    out.append(
        "Legend: :white_check_mark: pass · "
        ":warning: xfail (criterion not met) · "
        ":ledger: notebook-demonstrated · "
        ":fast_forward: skip (placeholder) · "
        ":label: test-local (tests adapter or configuration logic) · "
        ":hourglass_flowing_sand: pending (feasible but not implemented) · "
        ":no_entry_sign: not-supported (required functionality unavailable) · "
        ":x: fail\n"
    )
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# HTML (GitHub Pages dashboard)
# --------------------------------------------------------------------------- #

CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#6a737d;--card:#f6f8fa;--border:#d0d7de;
--pass:#1a7f37;--fail:#cf222e;--xfail:#9a6700;--skip:#6a737d;--pending:#8250df;--notebook:#0969da}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;
--card:#161b22;--border:#30363d;--pass:#3fb950;--fail:#f85149;--xfail:#d29922;--skip:#8b949e;--pending:#a371f7;--notebook:#58a6ff}}
:root[data-theme=light]{--bg:#fff;--fg:#1b1f24;--muted:#6a737d;--card:#f6f8fa;--border:#d0d7de;
--pass:#1a7f37;--fail:#cf222e;--xfail:#9a6700;--skip:#6a737d;--pending:#8250df;--notebook:#0969da}
:root[data-theme=dark]{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--card:#161b22;--border:#30363d;
--pass:#3fb950;--fail:#f85149;--xfail:#d29922;--skip:#8b949e;--pending:#a371f7;--notebook:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:2rem 1.25rem 4rem}
.logo{height:30px;width:auto;display:block;margin:0 0 1.1rem;border-radius:7px}
@media(prefers-color-scheme:dark){.logo{background:#fff;padding:6px 10px}}
:root[data-theme=light] .logo{background:none;padding:0}
:root[data-theme=dark] .logo{background:#fff;padding:6px 10px}
h1{font-size:1.6rem;margin:0 0 .25rem}.sub{color:var(--muted);margin:0 0 1.5rem}
.overall{display:flex;flex-wrap:wrap;gap:.75rem;margin:0 0 2rem}
.stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.6rem 1rem;min-width:96px}
.stat b{display:block;font-size:1.5rem;line-height:1.2}.stat span{color:var(--muted);font-size:.8rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.1rem 1.25rem;margin:0 0 1.25rem}
.card h2{font-size:1.15rem;margin:0}.card .full{color:var(--muted);font-size:.82rem;margin:.15rem 0 .8rem;font-weight:400}
.bar{display:flex;height:10px;border-radius:6px;overflow:hidden;margin:.2rem 0 .9rem;background:var(--border)}
.bar i{display:block}.bar .pass{background:var(--pass)}.bar .notebook{background:var(--notebook)}
.bar .xfail{background:var(--xfail)}.bar .skip{background:var(--skip)}.bar .test-local{background:var(--skip)}.bar .pending{background:var(--pending)}.bar .fail{background:var(--fail)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:.42rem .5rem;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em}
td.id{white-space:nowrap;color:var(--muted);font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:.08rem .5rem;border-radius:999px;font-size:.72rem;font-weight:700;
letter-spacing:.02em;color:#fff;white-space:nowrap}
.badge.pass{background:var(--pass)}.badge.fail{background:var(--fail)}.badge.xfail{background:var(--xfail)}
.badge.skip{background:var(--skip)}.badge.test-local{background:var(--skip)}.badge.pending{background:var(--pending)}.badge.notebook{background:var(--notebook)}
.badge.not-supported{background:var(--not-supported)}
.legend{color:var(--muted);font-size:.82rem;margin-top:1.5rem;line-height:1.9}
.legend .badge{margin-right:.25rem}
.tablewrap{overflow-x:auto}a{color:var(--notebook)}
"""


def bar_segments(counts: dict, total: int) -> str:
    segs = []

    order = [
        "pass",
        "notebook",
        "xfail",
        "skip",
        "test-local",
        "fail",
        "not-supported",
        "pending",
    ]

    for k in order:
        n = counts.get(k, 0)

        if n:
            pct = 100 * n / total if total else 0
            segs.append(
                f'<i class="{k}" style="width:{pct:.4f}%"></i>'
            )

    return "".join(segs)


def render_html(data: list, stamp: str) -> str:
    totals = {"pass": 0, "fail": 0, "xfail": 0, "skip": 0, "pending": 0, "notebook": 0, "test-local": 0, "not-supported": 0,}
    for _std, s in data:
        for k in totals:
            totals[k] += s["counts"].get(k, 0)

    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        "<title>V&amp;V Standards Coverage — JuPedSim Web Community</title>",
        f"<style>{CSS}</style></head><body><div class=wrap>",
        logo_img(),
        "<h1>V&amp;V Standards Coverage</h1>",
        f'<p class=sub>Verification &amp; validation across IMO, RiMEA, NIST TN 1822 and ISO 20414. '
        f'Generated {html.escape(stamp)} from <code>standards/coverage.json</code> + the latest '
        f'<a href="{REPO}/actions/workflows/vv.yml">V&amp;V run</a>.</p>',
    ]
    # overall stat tiles
    parts.append("<div class=overall>")
    for key, lbl in [
        ("pass", "Pass"),
        ("notebook", "Notebook"),
        ("xfail", "XFail"),
        ("skip", "Skip"),
        ("pending", "Pending"),
        ("not-supported", "Not Supported"),
        ("fail", "Fail"),
    ]:
        parts.append(f'<div class=stat><b>{totals[key]}</b><span>{lbl}</span></div>')
    parts.append("</div>")

    for std, s in data:
        parts.append("<section class=card>")
        parts.append(
            f'<h2>{html.escape(std["name"])} '
            f'<span style="color:var(--muted);font-weight:400;font-size:.9rem">— '
            f'{s["covered"]}/{s["total"]} covered</span></h2>'
        )
        vlabel = "pytest-asserted" if std["verification"] == "pytest" else "notebook-demonstrated"
        parts.append(f'<p class=full>{html.escape(std["full_name"])} · {vlabel}</p>')
        parts.append(f'<div class=bar>{bar_segments(s["counts"], s["total"])}</div>')
        parts.append('<div class=tablewrap><table><thead><tr>'
                     "<th>Test</th><th>Name</th><th>Criterion</th><th>Status</th>"
                     "</tr></thead><tbody>")
        for t, st in s["rows"]:
            label, cls = STATUS_META[st]
            name = html.escape(t["name"])
            if t.get("notebook"):
                name = f'<a href="{REPO}/blob/main/{t["notebook"]}">{name}</a>'
            note = t.get("note", "")
            crit = html.escape(t.get("criterion", ""))
            if note:
                crit += f'<br><span style="color:var(--muted);font-size:.82rem">{html.escape(note)}</span>'
            parts.append(
                f'<tr><td class=id>{html.escape(t["id"])}</td><td>{name}</td>'
                f"<td>{crit}</td><td><span class=\"badge {cls}\">{label}</span></td></tr>"
            )
        parts.append("</tbody></table></div></section>")

    parts.append(
        '<p class=legend>'
        '<span class="badge pass">PASS</span> criterion asserted &amp; met · '
        '<span class="badge xfail">XFAIL</span> criterion asserted but not met (known limitation) · '
        '<span class="badge notebook">NOTEBOOK</span> demonstrated in a notebook (no automated assert) · '
        '<span class="badge skip">SKIP</span> placeholder test · '
        '<span class="badge test-local">TEST-LOCAL</span> asserts test-file logic, not simulator output (see note) · '
        '<span class="badge pending">PENDING</span> feasible but not implemented · '
        '<span class="badge not-supported">NOT SUPPORTED</span> required functionality unavailable in JuPedSim · '
        '<span class="badge fail">FAIL</span> asserted &amp; failing'
        "</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="standards/coverage.json")
    ap.add_argument("--junit", default="results/vv-tests.xml")
    ap.add_argument("--outdir", default="site")
    args = ap.parse_args()

    manifest = json.loads(pathlib.Path(args.manifest).read_text())
    junit = parse_junit(pathlib.Path(args.junit))
    data = [(std, summarise(std, junit)) for std in manifest["standards"]]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(render_html(data, stamp), encoding="utf-8")
    (outdir / "summary.md").write_text(render_markdown(data, stamp), encoding="utf-8")
    print(f"wrote {outdir/'index.html'} and {outdir/'summary.md'}")


if __name__ == "__main__":
    main()
