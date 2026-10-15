#!/usr/bin/env python3
"""Execute the V&V notebooks and collect their outputs for publication.

Usage:
    uv run --frozen python scripts/run_notebooks.py [NOTEBOOK ...]
        [--outdir results/notebooks] [--timeout 1800] [--jobs N]

Without arguments every ``standards/**/*.ipynb`` is executed. Each notebook
runs in its own directory (up to --jobs in parallel) (so relative paths resolve), in a fresh kernel, with
errors allowed: a failing cell does not stop the notebook.

Per notebook ``<outdir>/<std>/<name>/`` receives the executed ``<name>.ipynb``,
``<name>.html`` and ``figures/*.png``. ``<outdir>/manifest.json`` records the
run provenance and, per notebook, status, duration, figures, and the
``standards/coverage.json`` rows that point at it. Rows without a notebook are
listed under ``no_notebook``. Figure, html and ipynb paths in the manifest are
relative to the manifest's directory.

Figure roles come from cell tags (see standards/README.md):
    vv-scenario  -> figures["scenario"]
    vv-result    -> figures["result"]
    (untagged)   -> figures["other"]

Status:
    ok                 no error output
    acceptance_failed  every error is an AssertionError: the published result
                       is a failure of the acceptance criterion
    error              any other exception, kernel death or timeout

Absolute paths (repo root, $GITHUB_WORKSPACE, runner/home/tmp prefixes) are
stripped from every output; a notebook whose outputs still contain one is
reported as ``error``. Exit code is non-zero only if a notebook has status
``error``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
import datetime
import importlib.metadata
import json
import multiprocessing
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time

import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
COVERAGE = REPO_ROOT / "standards" / "coverage.json"
ROLE_TAGS = {"vv-scenario": "scenario", "vv-result": "result"}
ROLES = ("scenario", "result", "other")
PACKAGES = ("jupedsim", "jupedsim-scenarios", "pedpy")

# Prefixes up to and including the checkout directory name.
_CHECKOUT = re.compile(
    r"(?:/home/runner/work|/Users|/private/tmp|/tmp|/var/folders|/private/var)"
    r"/(?:[^\s\"'<>/]+/)*?jupedsim-web-community/"
)
# Any remaining home / temp absolute prefix (outside the checkout).
_HOME = re.compile(r"/(?:Users|home)/[^/\s\"'<>]+/")
_TEMP = re.compile(r"(?:/private)?/(?:tmp|var/folders)/")
_LEAK = re.compile(r"/(?:Users|home|private/tmp|private/var|tmp|var/folders)/")


def _roots() -> list[str]:
    roots = {str(REPO_ROOT), os.path.realpath(REPO_ROOT)}
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        roots |= {workspace, os.path.realpath(workspace)}
    # Longest first so nested spellings are replaced before their parents.
    return sorted(roots, key=len, reverse=True)


def scrub(text: str) -> str:
    """Return text with absolute paths made relative; raise if any remain."""
    for root in _roots():
        text = text.replace(root.rstrip("/") + "/", "")
        text = text.replace(root, ".")
    text = _CHECKOUT.sub("", text)
    text = _HOME.sub("~/", text)
    text = _TEMP.sub("tmp/", text)
    leak = _LEAK.search(text)
    if leak:
        start = max(leak.start() - 40, 0)
        raise RuntimeError(
            f"absolute path remains: ...{text[start : leak.end() + 40]!r}"
        )
    return text


def scrub_json(value):
    """Apply scrub() to every string in a JSON-like structure."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [scrub_json(v) for v in value]
    if isinstance(value, dict):
        # Binary payloads (image/png, ...) are base64 and must stay untouched.
        return {
            k: v if k.startswith("image/") else scrub_json(v) for k, v in value.items()
        }
    return value


_BASE64 = re.compile(r"(base64,[A-Za-z0-9+/=\s]{64,})")


def scrub_html(text: str) -> str:
    """scrub() for HTML, leaving embedded base64 blobs untouched."""
    parts = _BASE64.split(text)
    return "".join(p if i % 2 else scrub(p) for i, p in enumerate(parts))


def discover(args: list[str]) -> list[pathlib.Path]:
    if not args:
        found = sorted((REPO_ROOT / "standards").rglob("*.ipynb"))
        return [p for p in found if ".ipynb_checkpoints" not in p.parts]
    return [pathlib.Path(a).resolve() for a in args]


def load_coverage() -> tuple[dict[str, list[dict]], list[dict]]:
    """Return ({notebook path: rows}, rows without a notebook)."""
    by_nb: dict[str, list[dict]] = {}
    missing: list[dict] = []
    for std in json.loads(COVERAGE.read_text())["standards"]:
        for test in std["tests"]:
            row = {"standard": std["id"], "test": test["id"], "name": test.get("name")}
            nb = test.get("notebook")
            if nb:
                by_nb.setdefault(nb, []).append(row)
            else:
                missing.append(row)
    return by_nb, missing


def _errors(nb) -> list[tuple[int, dict]]:
    found = []
    for idx, cell in enumerate(nb.cells):
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error":
                found.append((idx, out))
    return found


def _describe(idx: int, out: dict) -> str:
    text = f"cell {idx}: {out['ename']}: {out['evalue']}"
    return " ".join(text.split())


def classify(nb) -> tuple[str, str | None]:
    errors = _errors(nb)
    if not errors:
        return "ok", None
    if all(o["ename"] == "AssertionError" for _, o in errors):
        return "acceptance_failed", _describe(*errors[0])
    return "error", _describe(
        *next(e for e in errors if e[1]["ename"] != "AssertionError")
    )


def execute(path: pathlib.Path, timeout: int):
    """Execute in the notebook's directory; return (nb, failure or None)."""
    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=None,
        allow_errors=True,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
    )
    failure = asyncio.run(_run_with_deadline(client, timeout))
    return client.nb, failure


async def _run_with_deadline(client: NotebookClient, timeout: int) -> str | None:
    """Run the client; return a failure text, or None on completion.

    The deadline is tracked explicitly so that the kernel death caused by our
    own cancellation is reported as a timeout, while a kernel that dies before
    the deadline is reported as such.
    """
    task = asyncio.ensure_future(client.async_execute())
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return f"timeout after {timeout} s"
    exc = task.exception()
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"


def extract_figures(nb, outdir: pathlib.Path, rel_base: pathlib.Path) -> dict:
    """Write image/png outputs to outdir/figures; return role -> relative paths."""
    figures = {role: [] for role in ROLES}
    fig_dir = outdir / "figures"
    for idx, cell in enumerate(nb.cells):
        tags = set(cell.get("metadata", {}).get("tags", []))
        role = next((ROLE_TAGS[t] for t in ROLE_TAGS if t in tags), "other")
        count = 0
        for out in cell.get("outputs", []):
            data = (
                out.get("data", {})
                if out.get("output_type")
                in (
                    "display_data",
                    "execute_result",
                )
                else {}
            )
            png = data.get("image/png")
            if not png:
                continue
            fig_dir.mkdir(parents=True, exist_ok=True)
            target = fig_dir / f"{role}-cell{idx:03d}-{count}.png"
            target.write_bytes(base64.b64decode(png))
            figures[role].append(target.relative_to(rel_base).as_posix())
            count += 1
    return figures


def process(path: pathlib.Path, outdir: pathlib.Path, timeout: int) -> dict:
    rel = path.relative_to(REPO_ROOT).as_posix()
    std, name = path.parent.name, path.stem
    nb_out = outdir / std / name
    entry = {
        "path": rel,
        "standard": std,
        "status": "error",
        "duration_s": None,
        "error_summary": None,
        "figures": {role: [] for role in ROLES},
        "html": None,
        "ipynb": None,
    }
    start = time.monotonic()
    try:
        nb, failure = execute(path, timeout)
        entry["duration_s"] = round(time.monotonic() - start, 1)
        status, summary = classify(nb)
        if failure:
            status, summary = "error", failure
        entry["status"], entry["error_summary"] = status, summary
        nb = nbformat.from_dict(scrub_json(nb))
        if entry["error_summary"]:
            entry["error_summary"] = scrub(entry["error_summary"])[:500]
        shutil.rmtree(nb_out, ignore_errors=True)
        nb_out.mkdir(parents=True)
        html, _ = HTMLExporter().from_notebook_node(nb)
        html = scrub_html(html)
        ipynb_path = nb_out / f"{name}.ipynb"
        html_path = nb_out / f"{name}.html"
        ipynb_path.write_text(nbformat.writes(nb), encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        entry["figures"] = extract_figures(nb, nb_out, outdir)
        entry["ipynb"] = ipynb_path.relative_to(outdir).as_posix()
        entry["html"] = html_path.relative_to(outdir).as_posix()
    except Exception as exc:  # noqa: BLE001 - any export failure becomes status error
        shutil.rmtree(nb_out, ignore_errors=True)
        entry["status"] = "error"
        entry["duration_s"] = entry["duration_s"] or round(time.monotonic() - start, 1)
        entry["error_summary"] = f"{type(exc).__name__}: {exc}"[:500]
        entry["figures"] = {role: [] for role in ROLES}
        entry["ipynb"] = entry["html"] = None
        try:
            entry["error_summary"] = scrub(entry["error_summary"])
        except RuntimeError:
            entry["error_summary"] = "output contained an absolute path"
    return entry


def provenance() -> dict:
    def version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            sha = None
    return {
        "commit_sha": sha,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "python_version": platform.python_version(),
        "versions": {name: version(name) for name in PACKAGES},
        "timeout_s": None,
    }


def summary_markdown(entries: list[dict]) -> str:
    lines = [
        "### Notebook execution",
        "",
        "| Notebook | Status | Duration (s) | Figures | Note |",
        "|---|---|---:|---:|---|",
    ]
    for e in entries:
        n_fig = sum(len(v) for v in e["figures"].values())
        note = (e["error_summary"] or "").replace("|", "\\|").replace("\n", " ")[:160]
        lines.append(
            f"| `{e['path']}` | {e['status']} | {e['duration_s']} | {n_fig} | {note} |"
        )
    counts = {
        s: sum(e["status"] == s for e in entries)
        for s in ("ok", "acceptance_failed", "error")
    }
    lines += ["", ", ".join(f"{k}: {v}" for k, v in counts.items()), ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("notebooks", nargs="*", help="notebooks (default: all)")
    parser.add_argument("--outdir", default="results/notebooks")
    parser.add_argument(
        "--timeout", type=int, default=1800, help="seconds per notebook"
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=os.cpu_count() or 1,
        help="notebooks executed in parallel (default: CPU count)",
    )
    args = parser.parse_args()

    outdir = pathlib.Path(args.outdir)
    outdir = (outdir if outdir.is_absolute() else REPO_ROOT / outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    by_nb, missing = load_coverage()

    paths = sorted(discover(args.notebooks))
    jobs = max(1, min(args.jobs, len(paths) or 1))
    by_path = {}
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(jobs, mp_context=ctx) as pool:
        futures = {
            pool.submit(process, path, outdir, args.timeout): path for path in paths
        }
        for future in concurrent.futures.as_completed(futures):
            entry = future.result()
            entry["tests"] = by_nb.get(entry["path"], [])
            by_path[entry["path"]] = entry
            print(
                f"{entry['path']}: {entry['status']} in {entry['duration_s']} s "
                f"{entry['error_summary'] or ''}",
                flush=True,
            )
    entries = [by_path[p] for p in sorted(by_path)]

    prov = provenance()
    prov["timeout_s"] = args.timeout
    manifest = {"provenance": prov, "notebooks": entries, "no_notebook": missing}
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    summary = summary_markdown(entries)
    (outdir / "summary.md").write_text(summary)
    failed = [e["path"] for e in entries if e["status"] == "error"]
    (outdir / "failed-notebooks.txt").write_text("".join(f"{p}\n" for p in failed))
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
