# Contributing

Thank you for improving Mistral AI Conversation.

## Development setup

Use Python 3.14 and create an isolated virtual environment:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install --requirement requirements_test.txt
```

Install FFmpeg on your test machine and make `ffmpeg` available on `PATH` for
the speech-normalization tests. CI installs it explicitly.

The pinned PyTurboJPEG 2.6.1 also requires a native libjpeg-turbo 3.0 or newer.
Install a compatible library from the
[official libjpeg-turbo releases](https://github.com/libjpeg-turbo/libjpeg-turbo/releases).
The JPEG test encodes and decodes synthetic pixels to verify the native backend.
CI extracts the official 3.2.1 package into an isolated temporary directory,
verifies its SHA-256 digest, and selects it with `LD_LIBRARY_PATH`.
On Linux, keep that directory on the library path for local test runs too.

Use `requirements_test_2026_10.txt` or `requirements_test_2026_7.txt` in separate
virtual environments to run the compatibility suites. The primary environment
uses the official HA 2026.11 fixtures pinned to the release source commit.

Run the complete local quality gate before opening a pull request:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov=custom_components.mistral_conversation --cov-report=term-missing
```

## Change expectations

- Keep provider-specific objects behind the typed SDK boundary.
- Preserve Home Assistant config-entry, subentry, entity, translation, diagnostics,
  repair, and exception conventions.
- Add regression tests for behavior changes and provider edge cases.
- Mock Mistral calls; never commit API keys, recordings, prompts, or user data.
- Update user-facing translations and documentation with changed behavior.
- Keep changes focused and document compatibility or migration effects.

Use conventional, imperative commit subjects. Pull requests should explain the
user impact, testing performed, and any security or privacy implications.

## Reporting defects

Include the Home Assistant version, integration version, selected model ID,
sanitized diagnostics, and relevant logs. Remove API keys, prompt content, entity
names, addresses, and other private data before sharing.

For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public
issue.
