"""Opt-in checks against locally installed tools; never required by CI."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

OPT_IN = os.environ.get("CODEX_SMART_ROUTE_EXTERNAL_COMPAT") == "1"


@pytest.mark.skipif(
    not OPT_IN or shutil.which("rtk") is None,
    reason="requires CODEX_SMART_ROUTE_EXTERNAL_COMPAT=1 and an installed rtk executable",
)
def test_rtk_proxy_preserves_transport_contract(tmp_path: Path) -> None:
    executable = shutil.which("rtk")
    assert executable is not None
    child = tmp_path / "transport_probe.py"
    child.write_text(
        """import hashlib, os, pathlib, sys
data = sys.stdin.read()
path_hash = hashlib.sha256(os.environ["PATH"].encode()).hexdigest()
assert path_hash == os.environ["SMART_ROUTE_PATH_HASH"]
assert pathlib.Path.cwd() == pathlib.Path(os.environ["SMART_ROUTE_EXPECTED_CWD"])
pathlib.Path("modified.txt").write_text(data, encoding="utf-8")
print(os.environ["SMART_ROUTE_PROBE"] + ":" + data, end="")
print("probe-stderr", file=sys.stderr)
raise SystemExit(23)
""",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["SMART_ROUTE_PROBE"] = "environment-preserved"
    environment["SMART_ROUTE_PATH_HASH"] = hashlib.sha256(environment["PATH"].encode()).hexdigest()
    environment["SMART_ROUTE_EXPECTED_CWD"] = str(tmp_path)
    process = subprocess.run(  # noqa: S603
        [executable, "proxy", sys.executable, str(child)],
        cwd=tmp_path,
        env=environment,
        input="stdin-preserved",
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 23
    assert process.stdout == "environment-preserved:stdin-preserved"
    assert "probe-stderr" in process.stderr
    assert (tmp_path / "modified.txt").read_text(encoding="utf-8") == "stdin-preserved"
