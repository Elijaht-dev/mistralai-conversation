"""Validate the repository structure needed by HACS without network access."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
HACS_MANIFEST = ROOT / "hacs.json"
SEMANTIC_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[ab]\d+)?$")

INTEGRATION_MANIFEST_KEYS = {
    "codeowners",
    "documentation",
    "domain",
    "issue_tracker",
    "name",
    "version",
}
HACS_MANIFEST_KEYS = {
    "content_in_root",
    "country",
    "filename",
    "hacs",
    "hide_default_branch",
    "homeassistant",
    "name",
    "persistent_directory",
    "zip_release",
}


def _load_json_object(path: Path) -> dict[str, object]:
    """Load a JSON object or fail with a repository-facing message."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"{path.relative_to(ROOT)} is not valid JSON: {err}") from err

    if not isinstance(value, dict):
        raise SystemExit(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _validate_integration() -> None:
    """Validate the single-integration layout and its manifest."""
    integrations = sorted(
        path
        for path in CUSTOM_COMPONENTS.iterdir()
        if path.is_dir() and not path.name.startswith((".", "__"))
    )
    if len(integrations) != 1:
        raise SystemExit(
            "custom_components must contain exactly one integration directory"
        )

    integration = integrations[0]
    manifest = _load_json_object(integration / "manifest.json")
    missing = INTEGRATION_MANIFEST_KEYS - manifest.keys()
    if missing:
        raise SystemExit(
            "manifest.json is missing required keys: " + ", ".join(sorted(missing))
        )
    if manifest["domain"] != integration.name:
        raise SystemExit("manifest domain must match the integration directory")

    version = manifest["version"]
    if not isinstance(version, str) or SEMANTIC_VERSION.fullmatch(version) is None:
        raise SystemExit("manifest version must be a semantic version")

    icon = integration / "brand" / "icon.png"
    try:
        signature = icon.read_bytes()[:8]
    except OSError as err:
        raise SystemExit("integration brand/icon.png is required") from err
    if signature != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("integration brand/icon.png must be a PNG image")


def _validate_hacs_manifest() -> None:
    """Validate the root HACS manifest against supported public keys."""
    manifest = _load_json_object(HACS_MANIFEST)
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        raise SystemExit("hacs.json must contain a non-empty name")

    unsupported = manifest.keys() - HACS_MANIFEST_KEYS
    if unsupported:
        raise SystemExit(
            "hacs.json contains unsupported keys: " + ", ".join(sorted(unsupported))
        )

    minimum_ha = manifest.get("homeassistant")
    if minimum_ha is not None and (
        not isinstance(minimum_ha, str)
        or SEMANTIC_VERSION.fullmatch(minimum_ha) is None
    ):
        raise SystemExit("hacs.json homeassistant must be a semantic version")


def main() -> None:
    """Run all offline HACS readiness checks."""
    _validate_integration()
    _validate_hacs_manifest()
    print("HACS repository structure is valid")


if __name__ == "__main__":
    main()
