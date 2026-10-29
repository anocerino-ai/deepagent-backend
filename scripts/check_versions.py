"""Fail if the umbrella and backend distributions are not released in lockstep.

Every distribution must share one version, and every cross-reference
(``deepagent-backend==X`` in backends, ``deepagent-backend-<x>==X`` in the extras)
must pin that same version.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(?P<version>[^\s;,]+)$")


def load(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def main() -> int:
    root = load(ROOT / "pyproject.toml")
    expected = root["project"]["version"]
    errors: list[str] = []
    seen = {root["project"]["name"]: expected}

    projects = {".": root}
    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        projects[str(path.parent.relative_to(ROOT))] = load(path)

    for where, data in projects.items():
        project = data["project"]
        seen[project["name"]] = project["version"]
        if project["version"] != expected:
            errors.append(f"{where}: version {project['version']} != {expected}")

        requirements = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            requirements.extend(extra)
        for req in requirements:
            match = PIN.match(req)
            if match and match["name"].startswith("deepagent-backend") and match["version"] != expected:
                errors.append(f"{where}: {req!r} should pin =={expected}")

    extras = root["project"].get("optional-dependencies", {})
    for name, reqs in extras.items():
        for req in reqs:
            dist = re.split(r"[=<>!~\[ ]", req, maxsplit=1)[0]
            if dist not in seen:
                errors.append(f"extra [{name}] references unknown distribution {dist!r}")

    backends = {n for n in seen if n.startswith("deepagent-backend-")}
    in_all = {re.split(r"[=<>!~\[ ]", r, maxsplit=1)[0] for r in extras.get("all", [])}
    if backends != in_all:
        missing, unknown = sorted(backends - in_all), sorted(in_all - backends)
        errors.append(f"extra [all] mismatch: missing {missing}, unknown {unknown}")

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if not errors:
        print(f"ok: {len(seen)} distributions at {expected}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
