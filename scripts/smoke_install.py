"""Install the freshly built wheels in clean venvs and verify every extra.

Usage: python scripts/smoke_install.py dist

For each extra (``s3``, ``redis``, ..., ``all``) it creates a throw-away venv, installs
``deepagent-backend[<extra>]`` from the local wheels (third-party dependencies still
come from the package index) and checks that
  * the backend's classes import through the umbrella package,
  * backends that were NOT requested raise a helpful ImportError.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

CLASSES = {
    "s3": "S3Backend",
    "postgres": "PostgresBackend",
    "azure-blob": "AzureBlobBackend",
    "gcs": "GCSBackend",
    "mongodb": "MongoDBBackend",
    "redis": "RedisBackend",
}

CHECK = """
import sys
from deepagent_backend import run_async_safely  # noqa: F401  (base install always works)
import deepagent_backend

wanted = set(sys.argv[1].split(",")) if sys.argv[1] else set()
classes = {classes!r}
for extra, cls in classes.items():
    if extra in wanted:
        getattr(deepagent_backend, cls)
        print(f"  ok      {{cls}}")
    else:
        try:
            getattr(deepagent_backend, cls)
        except ImportError as exc:
            assert f"deepagent-backend[{{extra}}]" in str(exc), exc
            print(f"  absent  {{cls}} (helpful ImportError)")
        else:
            raise SystemExit(f"{{cls}} importable without its extra")
"""


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main(dist: Path) -> int:
    dist = dist.resolve()
    if not list(dist.glob("deepagent_backend-*.whl")):
        print(f"no wheels in {dist}; run `just build` first", file=sys.stderr)
        return 1

    # (extras to install, backends expected to import)
    cases = [([], [])] + [([e], [e]) for e in CLASSES] + [(["all"], list(CLASSES))]
    for extras, wanted in cases:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            run("uv", "venv", "-q", str(venv))
            spec = f"deepagent-backend[{','.join(extras)}]" if extras else "deepagent-backend"
            print(f"== pip install {spec}")
            run("uv", "pip", "install", "-q", "--python", str(py), "--find-links", str(dist), spec)
            run(str(py), "-c", CHECK.format(classes=CLASSES), ",".join(wanted))
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist")))
