"""The committed web bundle must match the committed sources.

`web/dist/pw_color.js` is checked in so the pack works from a plain clone with
no node toolchain. That convenience has a failure mode: edit a `.ts`, forget to
rebuild, and the shipped frontend silently lags the source. It happened once
already — removing the parity node left a dangling import that broke the build
entirely, and nothing caught it because no test touched the bundle.

Skipped when the node toolchain is not installed, so a checkout without
`npm install` still runs the rest of the suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BUNDLE = WEB / "dist" / "pw_color.js"


def _npm() -> str:
    for candidate in ("npm.cmd", "npm"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("npm not on PATH")


def _require_toolchain() -> None:
    if not (WEB / "node_modules").is_dir():
        pytest.skip("web/node_modules missing; run `npm install` in web/")


def test_bundle_is_committed():
    assert BUNDLE.is_file(), "web/dist/pw_color.js is missing; a plain clone would have no frontend"
    assert BUNDLE.stat().st_size > 10_000, "bundle looks truncated"


def test_bundle_has_no_unresolved_imports():
    """esbuild leaves the host modules external; nothing else should be."""
    text = BUNDLE.read_text(encoding="utf-8", errors="replace")
    for spec in ("/scripts/app.js", "/scripts/api.js"):
        assert spec in text, f"expected external import {spec} in the bundle"
    assert "./nodes/" not in text, "bundle contains an unresolved relative import"


def test_bundle_matches_the_sources(tmp_path):
    """Rebuild and compare. Fails if someone edited TypeScript without running
    `npm run build`, which would ship a stale frontend."""
    _require_toolchain()
    out = tmp_path / "rebuilt.js"
    proc = subprocess.run(
        [
            _npm(), "exec", "--no", "--", "esbuild", "src/index.ts",
            "--bundle", "--format=esm", "--target=es2022",
            f"--outfile={out}", "--external:/scripts/*", "--external:/extensions/*",
        ],
        cwd=WEB, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"esbuild failed:\n{proc.stdout}\n{proc.stderr}")
    assert out.read_bytes() == BUNDLE.read_bytes(), (
        "web/dist/pw_color.js is stale. Run `npm run build` in web/ and commit the result."
    )


def test_typescript_has_no_errors():
    _require_toolchain()
    proc = subprocess.run(
        [_npm(), "exec", "--no", "--", "tsc", "--noEmit"],
        cwd=WEB, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"tsc reported errors:\n{proc.stdout}\n{proc.stderr}"


def test_every_source_module_is_reachable_from_the_entry_point():
    """An orphaned module is either dead code or a forgotten registration."""
    entry_tree: set[Path] = set()

    def walk(path: Path) -> None:
        if path in entry_tree or not path.is_file():
            return
        entry_tree.add(path)
        import re

        for spec in re.findall(r"from '([^']+)'", path.read_text(encoding="utf-8")):
            if spec.startswith("."):
                walk((path.parent / spec).resolve())

    walk(WEB / "src" / "index.ts")
    all_sources = {p.resolve() for p in (WEB / "src").rglob("*.ts")}
    orphans = sorted(p.relative_to(WEB).as_posix() for p in all_sources - entry_tree)
    assert not orphans, f"unreachable from index.ts: {orphans}"
