"""Keep release metadata aligned for HACS and local development."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_project_versions_match() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "aecc_battery" / "manifest.json").read_text()
    )
    project = (ROOT / "pyproject.toml").read_text()
    project_version = re.search(
        r'^version = "([^"]+)"$',
        project,
        flags=re.MULTILINE,
    )

    assert project_version is not None
    assert manifest["version"] == project_version.group(1)


def test_current_version_has_a_changelog_section() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "aecc_battery" / "manifest.json").read_text()
    )
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert f"## {manifest['version']}" in changelog
