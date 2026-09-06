"""Run the frontend's own test suite.

The TypeScript layer had no runner at all: `tsc` proves it compiles and
`test_build.py` proves the committed bundle matches the sources, but neither
can tell you that shift-click removes the wrong curve point.

`web/test/` uses node's built-in test runner with type stripping — no bundler,
no framework, nothing to install beyond node itself — for the same reason the
parity harnesses do: a test that needs a build step is a test people stop
running. It is driven from here so that `pytest` stays the one command, and so
CI cannot pass while the frontend tests are failing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"


def test_frontend_suite_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")

    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "--test", "test/*.test.ts"],
        cwd=WEB,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail("frontend tests failed:" + proc.stdout[-4000:] + proc.stderr[-2000:])

    # A runner that matched no files still exits 0 and reports nothing, which
    # would make this test a decoration rather than a check.
    match = re.search(r"^.*\bpass (\d+)$", proc.stdout, re.M)
    assert match, "could not read a pass count from the runner:" + proc.stdout[-2000:]
    assert int(match.group(1)) > 0, "the frontend runner matched no tests:" + proc.stdout[-2000:]
