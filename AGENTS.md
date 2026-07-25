# Repository instructions for coding agents

These instructions apply to the entire repository.

## Mission

Maintain a production-quality HACS custom integration that gives Home Assistant
a Mistral AI conversation agent through the official `mistralai` Python SDK.
Preserve Home Assistant conventions and defensive behavior; do not imply that
this project is first-party, Home Assistant-certified, or endorsed by Mistral AI.

## Read before editing

1. Read `README.md` for supported user behavior and installation requirements.
2. Read `QUALITY.md` for architectural promises and assurance boundaries.
3. Read `CONTRIBUTING.md` and the tests nearest the code being changed.
4. Check current Home Assistant and Mistral SDK APIs when behavior is uncertain.
   Do not copy examples written for older Home Assistant conversation APIs.

## Repository map

- `custom_components/mistral_conversation/` is the only runtime integration.
- `api.py` owns SDK client creation, credential validation, and model discovery.
- `coordinator.py` owns the shared client, model metadata, availability, and
  refresh lifecycle.
- `conversation.py` is the thin Home Assistant `ConversationEntity` adapter.
- `entity.py` owns message conversion, streaming, capability checks,
  attachments, and the tool-execution loop.
- `tool_calls.py` reconstructs fragmented and parallel streamed tool calls.
- `config_flow.py`, `diagnostics.py`, and `repairs.py` implement their matching
  Home Assistant surfaces.
- `translations/en.json` and `translations/fr.json` are both maintained.
- `tests/` uses Home Assistant-native fixtures and mocked provider behavior.
- `hacs.json`, `manifest.json`, the brand assets, and
  `.github/workflows/validate.yml` form the HACS publication surface.

## Architectural invariants

### Home Assistant lifecycle

- Keep configuration UI-only: one account config entry with one or more
  conversation config subentries.
- Store shared runtime state in typed `ConfigEntry.runtime_data`.
- Use Home Assistant's shared HTTP client when constructing the Mistral client,
  and close provider resources deterministically during unload or failed setup.
- Keep the event loop non-blocking. Put filesystem or other blocking work in
  `hass.async_add_executor_job`.
- Raise translated Home Assistant exceptions and preserve reauthentication,
  retry, repair, and entity-availability semantics.
- When persisted data changes, update config-entry migration logic and add
  migration tests. Never silently reinterpret existing user configuration.

### Mistral provider boundary

- Use the official SDK and its generated request/message types. Keep
  provider-specific objects behind the typed boundary in `api.py`, `entity.py`,
  and `tool_calls.py`.
- Preserve streamed text, reasoning, usage, finish-state, and parallel tool-call
  handling. A stream may split a tool name or JSON arguments across many chunks.
- Replay provider-native signed reasoning only in the native form expected by
  Mistral. Never invent, alter, expose, or log reasoning signatures.
- Treat discovered capability metadata as authoritative for known models, but
  continue allowing unknown custom and fine-tuned model IDs.
- Normalize SDK and transport failures through `errors.py`. Authentication,
  timeout, connection, rate-limit, and generic provider failures have different
  Home Assistant behavior and must remain distinguishable.
- Do not add a live Mistral request to normal tests or CI.

### Tools and attachments

- Home Assistant remains the authority for tool schemas and execution. Preserve
  the model-to-Assist round trip through `ChatLog`.
- Reject incomplete, malformed, or ambiguous streamed tool calls before
  execution.
- Preserve the bounds in `const.py`: declared tools, tool rounds, attachment
  count, attachment size, supported MIME types, output tokens, and timeouts.
  Changing a bound requires a clear user-facing reason and boundary tests.
- Validate model capabilities before sending tools, reasoning, images, or PDFs.
- Fail closed on empty streams, explicit stream errors, unsupported chat-log
  content, oversized input, and runaway tool loops.

### Security and privacy

- Never commit or print API keys, access tokens, real prompts, entity inventories,
  addresses, attachments, recordings, diagnostics, or user data.
- Use obviously fake credentials such as `test-api-key` in tests.
- Keep diagnostics redacted and request logging free of prompt and attachment
  content. Bound and normalize provider error text before displaying or logging
  it.
- Do not broaden entity control, remove capability checks, or weaken limits as a
  convenience fix.
- Report suspected vulnerabilities through `SECURITY.md`, not a public issue.

## Change expectations

- Make the smallest coherent change and preserve strict typing.
- Add regression tests for behavior changes, provider edge cases, migrations,
  and fixed bugs.
- Prefer public behavior assertions over private implementation assertions.
- Mock the SDK at the integration boundary and use realistic async streams.
- Add or update English and French strings together. Keep translation keys,
  nesting, and placeholders identical across languages.
- Update `README.md`, `QUALITY.md`, and `CHANGELOG.md` when supported behavior,
  compatibility, privacy, or release status changes.
- Keep all runtime files beneath the single
  `custom_components/mistral_conversation/` directory required by HACS.
- Keep `brand/icon.png` and
  `custom_components/mistral_conversation/brand/icon.png` identical; use
  `scripts/generate_brand_icon.ps1` when regenerating them.

When changing dependency or compatibility versions, update every matching
surface:

- Mistral SDK: `manifest.json`, `requirements_test.txt`, and README text/badges.
- Home Assistant minimum: `hacs.json`, `requirements_test.txt`, and README
  text/badges.
- Integration release: `manifest.json` and `CHANGELOG.md`.

Do not publish a release, create a tag, change repository visibility, or submit
the project to the default HACS catalog without explicit owner authorization.
A versioned release also requires the real Home Assistant smoke test described
in `QUALITY.md`.

## Validation

Use Python 3.14 and install the pinned test environment:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install --requirement requirements_test.txt
```

Run focused tests while iterating, then the complete gate:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov=custom_components.mistral_conversation --cov-report=term-missing
python scripts/validate_hacs.py
```

Branch coverage must remain at or above 85%. GitHub Actions is authoritative for
Hassfest and the official public HACS validation. Do not relax lint, typing,
coverage, Hassfest, or HACS checks to make a change pass.

## Definition of done

A change is complete only when:

- the intended Home Assistant behavior works through the public integration
  surface;
- relevant success, failure, cleanup, and boundary paths are tested;
- strict typing, formatting, linting, tests, coverage, and offline HACS
  validation pass;
- translations, documentation, migrations, and version metadata are updated
  where applicable;
- no credential, private data, generated artifact, or unrelated worktree change
  is included.
