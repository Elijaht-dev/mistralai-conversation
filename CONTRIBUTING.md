# Contributing

## Development setup

Use Python 3.14.2+ and an isolated virtual environment:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install --requirement requirements_test.txt
```

Install FFmpeg on `PATH` for speech-normalization tests. PyTurboJPEG 2.6.1
requires a native libjpeg-turbo 3.0+ library; use the
[official releases](https://github.com/libjpeg-turbo/libjpeg-turbo/releases).
On Linux, include its library directory in `LD_LIBRARY_PATH`.

CI extracts checksum-verified libjpeg-turbo 3.2.1 into a temporary directory.
The JPEG test performs real encoding and decoding with synthetic pixels.
These native dependencies belong to the development environment; Home Assistant
manages its own runtime image libraries.

Use separate virtual environments for the compatibility suites:

| Home Assistant | Requirements |
| --- | --- |
| 2026.11.0 | `requirements_test.txt` |
| 2026.10.4 | `requirements_test_2026_10.txt` |
| 2026.7.4 | `requirements_test_2026_7.txt` |

Shared pins live in `requirements_test_base.txt`. Keep each HA version paired
with its matching fixtures.

## Validation

Run the local gate before opening a pull request:

```bash
python -m pip check
ruff format --check .
ruff check .
mypy
pytest --cov=custom_components.mistral_conversation --cov-report=term-missing
python scripts/validate_hacs.py
git diff --check
```

CI also enforces at least 85% branch coverage and runs Hassfest/public HACS
validation. Live-provider checks are separate and require owner authorization;
normal tests use mocked provider behavior.

For documentation-only changes, review accuracy and links and run
`git diff --check`. The Python gate is not required.

## Changes and pull requests

- Keep patches focused and preserve strict typing and HA lifecycle conventions.
- Add regression tests for behavior, boundaries, cleanup and migrations.
- Keep provider objects behind the typed SDK boundary.
- Update English/French strings together and document changed behavior.
- Keep dependency pins, compatibility metadata and release notes consistent.
- Exclude credentials, personal data and generated artifacts.

Use conventional, imperative commit subjects. Describe the problem, resulting
behavior and validation in pull requests. [QUALITY.md](QUALITY.md) defines
runtime guarantees and release requirements.

## Reporting issues

Include integration/HA versions, selected model ID, reproduction steps and
redacted logs or diagnostics. Remove credentials, prompts, entity inventories,
addresses, attachments and audio before sharing.

Report suspected vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).
