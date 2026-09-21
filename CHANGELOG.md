# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-09-21

### Changed

- Update the official `mistralai[realtime]` SDK from 2.10.0 to 2.10.1.
- Update the automated test baseline to Home Assistant 2026.9.2 and
  `pytest-homeassistant-custom-component` 0.13.365 together, resolving the
  dependency conflicts in their separate updates. The supported Home Assistant
  minimum remains 2026.7.4.
- Update development dependencies to `gazetteer-matcher` 1.2.0 and Ruff 0.16.7.

### Validation

- Extend real-SDK cold-start coverage to populated preset and custom voice
  responses, including the required voice type in SDK 2.10.1.
- Real-instance smoke testing is not repeated for this dependency maintenance
  release, as explicitly accepted by the maintainer.

### Fixed

- Restore the missing 0.3.0 changelog comparison link.

## [0.3.0] - 2026-09-20

### Added

- Offer Voxtral Realtime as an optional speech-to-text model for Assist, sending
  audio while the user speaks and returning the completed transcript. Existing
  entities and the default batch model remain unchanged.
- Bound Realtime audio and request duration, close WebSocket connections on
  cancellation and unload, and preserve authentication and availability handling.

### Validation

- All 282 tests pass with 94.56% branch-enabled coverage, including fresh-process
  SDK and local TLS WebSocket tests with Home Assistant's blocking detector.
- On Home Assistant 2026.9.2, configuration validation and a full restart passed
  with no Mistral blocking-call warnings or errors in the observed logs.
- Real-instance checks passed for Realtime Assist transcription, cancellation
  and recovery, existing batch STT, Conversation, a read-only Assist tool round
  trip, structured AI Task output, and TTS generation without playback.

## [0.2.3] - 2026-09-14

### Changed

- Promote the SDK startup blocking fixes from `0.2.3b1` to stable without
  runtime code changes.

### Validation

- The maintainer confirmed successful Conversation, Assist tool-call, AI Task,
  STT, and TTS smoke tests on Home Assistant 2026.9.2 after the beta's full
  restart check. These functional results are maintainer-reported.

## [0.2.3b1] - 2026-09-14

### Fixed

- Move Mistral SDK construction and synchronous cleanup off Home Assistant's
  event loop while preserving the shared asynchronous HTTP transport.
- Prepare SDK services and cache the original lazy exports used by model
  discovery, chat, speech, transcription, and voice discovery before requests.
- Close SDK-owned resources when initialization fails or setup is cancelled.

### Validation

- Add fresh-process regression tests using Home Assistant's blocking-call
  detector and the real SDK with mocked HTTP responses.
- All 239 tests pass with 93.77% branch-enabled coverage.
- Configuration validation and a full restart passed on Home Assistant 2026.9.2,
  with no Mistral blocking-call warnings or setup errors in the observed logs.
  Live Conversation, tool-call, AI Task, STT, and TTS smoke tests remain pending.

## [0.2.2] - 2026-09-12

### Changed

- Updated the official `mistralai` SDK baseline from 2.9.4 to 2.10.0.
- Advanced the automated test baseline to Home Assistant 2026.9.1 and
  `pytest-homeassistant-custom-component` 0.13.364 without changing the
  user-facing minimum Home Assistant version.
- Updated the Ruff development baseline from 0.16.5 to 0.16.6.

## [0.2.1] - 2026-09-06

### Changed

- Updated the official `mistralai` SDK baseline from 2.9.3 to 2.9.4.
- Updated the Ruff development baseline from 0.16.4 to 0.16.5.

### Fixed

- Corrected the changelog comparison links for the 0.2.0 release history.

## [0.2.0] - 2026-09-05

### Fixed

- Restore setup on Home Assistant 2026.9 while retaining the legacy OpenAPI
  converter on older supported versions.
- Omit unsupported reasoning parameters for non-reasoning models and respect
  model-specific reasoning controls, including Small 4 with Magistral aliases.

### Changed

- Promote the beta fixes to stable without further runtime changes.
- Simplify reasoning configuration to Automatic / Disabled / Enabled during
  creation, with fixed model states on reconfiguration and single-submit saves.

### Validation

- The maintainer tested the beta on two real Home Assistant installations,
  running 2026.8.6 and 2026.9.0, and approved stable publication.

## [0.2.0b2] - 2026-09-05

### Fixed

- Keep Small 4 reasoning adjustable when its metadata includes legacy Magistral
  aliases. Exact model IDs now take precedence over aliases during discovery
  lookup, and canonical model reasoning rules take precedence over alias rules.

### Changed

- Always show the reasoning dropdown when creating Conversation or AI Task
  entities. Fixed reasoning labels appear only during reconfiguration.
- Save model changes in one submission without an intermediate control review.
  Models with fixed reasoning apply their supported behavior when saved; the
  corresponding label appears when reopening the configuration.

## [0.2.0b1] - 2026-09-05

### Fixed

- Fixed Conversation and AI Task requests failing with HTTP 400 on non-reasoning
  models such as Ministral when reasoning effort is set to None. The unsupported
  parameter is now omitted for these models and unknown custom model IDs, while
  retaining explicit reasoning controls for compatible models.
- Limited the reasoning choices for Mistral Small 4 and Medium 3.5 to their
  documented `none` / `high` controls, and added Automatic for provider-managed
  behavior, including Magistral's native reasoning. Incompatible saved efforts
  now fail locally with a translated reconfiguration message instead of being
  sent to Mistral. Undocumented custom-model efforts remain available.

### Changed

- Simplified the Reasoning control to Automatic / Disabled / Enabled, with
  fixed text for unavailable or always-active reasoning. Changing models across
  these states presents the updated controls before saving. Existing custom
  efforts remain editable without changing their stored meaning.

## [0.1.8b1] - 2026-09-04

### Fixed

- Restored setup on Home Assistant 2026.9 by using Probatio's OpenAPI schema
  converter while retaining the previous converter on older supported Home
  Assistant releases.

### Changed

- Advanced the automated test baseline to Home Assistant 2026.9.0 and its
  matching Home Assistant-native test fixture.

## [0.1.7] - 2026-08-29

### Fixed

- Restored the minimum Home Assistant version to 2026.7.4. The declared
  compatibility floor is no longer raised with the newer development test
  baseline.

## [0.1.6] - 2026-08-29

### Changed

- Raised the minimum Home Assistant version from 2026.8.2 to 2026.8.3 and
  synchronized the Home Assistant-native test fixture.
- Updated the Hassil, Home Assistant intents, and Ruff development baselines.

## [0.1.5] - 2026-08-23

### Changed

- Raised the minimum Home Assistant version from 2026.8.1 to 2026.8.2 and
  synchronized the Home Assistant-native test fixture.
- Updated the official `mistralai` SDK baseline from 2.9.2 to 2.9.3.
- Updated the Ruff and mypy development baselines.
- Updated the HACS installation instructions now that the integration is
  included in the default HACS catalog.

## [0.1.4] - 2026-08-15

### Changed

- Updated the official `mistralai` SDK baseline from 2.9.1 to 2.9.2.

## [0.1.3] - 2026-08-08

### Changed

- Raised the minimum Home Assistant version from 2026.7.4 to 2026.8.1 and
  synchronized the Home Assistant-native test fixture.

## [0.1.2] - 2026-08-08

### Changed

- Updated the official `mistralai` SDK baseline from 2.8.0 to 2.9.1.

## [0.1.1] - 2026-08-01

### Changed

- Updated the official `mistralai` SDK baseline from 2.7.2 to 2.8.0.

### Fixed

- Corrected the Home Assistant and HACS icon and logo assets.

## [0.1.0] - 2026-07-26

### Added

- Public HACS custom-repository baseline.
- Typed Mistral SDK request and streaming response boundary.
- Streaming text, reasoning, token usage, and parallel tool calls.
- Home Assistant conversation subentries and Assist tool support.
- Home Assistant AI Task entities with native JSON-schema structured output.
- Voxtral speech-to-text entities for Assist voice pipelines.
- Streaming Voxtral text-to-speech with explicit preset or saved voice
  selection.
- Images and PDF attachments with bounded local validation.
- Model capability validation, aliases, coordinator availability, diagnostics,
  reauthentication, migrations, and deprecated-model repairs.
- English and French translations.
- Ruff, strict mypy, pytest coverage, HACS, Hassfest, and Dependabot automation.

[Unreleased]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.3b1...v0.2.3
[0.2.3b1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.2...v0.2.3b1
[0.2.2]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.0b2...v0.2.0
[0.2.0b2]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.2.0b1...v0.2.0b2
[0.2.0b1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.8b1...v0.2.0b1
[0.1.8b1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.7...v0.1.8b1
[0.1.7]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Elijaht-dev/mistralai-conversation/releases/tag/v0.1.0
