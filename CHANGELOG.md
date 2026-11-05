# Changelog

Release history follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Accept signed image URLs from the image tool while preserving Files API
  downloads/deletion and compatibility with older Home Assistant versions.
- Reject unapproved storage URLs and redirects; keep storage credentials out
  of download errors and account credentials out of storage requests.

### Added

- Curated model menus with custom IDs and Automatic, Batch and Realtime STT modes.
- Voxtral 3 support, including Realtime turn events and cancellation/recovery.

### Changed

- Use Mistral SDK 3.7.0 and HTTPX2 2.14.0, with HA's shared transport on 2026.11
  and an owned verified transport on older supported versions.
- Default new audio entities to Voxtral 3 and new image-tool settings to Large 4.
  Migration 1.5 preserves existing explicit and implicit choices.
- Use discovered chat/audio capabilities and cursor-paginated voice discovery.
- Test HA 2026.11.0, 2026.10.4 and the unchanged 2026.7.4 minimum.
- Update test-only PyTurboJPEG to 2.6.1 with a verified libjpeg-turbo 3.2.1 backend.
- Reorganize the user, contributor and quality guides and condense release history.

### Validation

501 tests pass on each HA baseline. Live voice, Conversation, structured AI Task
and Large 4 image/reference generation with HA retrieval passed. Files API
download and deletion were verified with a private synthetic PNG; URL-only
images have no Files API deletion support. Details and publication requirements
are in [QUALITY.md](QUALITY.md#recorded-validation).

## [0.5.5] - 2026-11-01

### Changed

- Update tests to HA 2026.10.4, fixtures 0.13.375, intents 2026.10.27 and Ruff 0.17.3.
- Keep runtime behavior, SDK 2.10.1 and the HA 2026.7.4 minimum unchanged.

### Validation

385 automated tests passed. Live Conversation, tools, structured AI Task and
synthetic STT/TTS checks passed on HA 2026.10.4 with available Voxtral 3 model IDs.

## [0.5.4] - 2026-10-12

### Changed

- Update tests to HA 2026.10.1, fixtures 0.13.372, intents 2026.10.6,
  mypy 2.4.0 and Ruff 0.16.10.

### Fixed

- Support native HA tool results and schema typing while retaining older history
  and HA compatibility.

### Validation

385 tests passed on HA 2026.10.1 and 2026.9.4. Live checks covered Conversation,
local-intent follow-ups, tools, AI Task and synthetic voice on HA 2026.10.1.

## [0.5.3] - 2026-10-08

### Fixed

- Preserve follow-up conversations after locally handled Assist commands,
  omitting their external tool exchanges while retaining user messages/replies.

### Validation

384 automated tests passed. Conversation history, tools, AI Task and synthetic
voice checks passed on HA 2026.10.0.

## [0.5.2] - 2026-10-06

### Changed

- Update tests to HA 2026.9.4, fixtures 0.13.367, intents 2026.9.30 and Ruff 0.16.9.

### Validation

376 automated tests passed. Live smoke tests were not repeated for this
dependency-only maintenance release.

## [0.5.1] - 2026-09-26

### Changed

- Clarify the image-tool model setting in English and French.
- Update tests to HA 2026.9.3, fixtures 0.13.366, Hassil 3.12.1,
  intents 2026.9.17 and Ruff 0.16.8.
- Group HA and its pytest fixtures in Dependabot.

### Validation

376 automated tests passed. Live smoke tests were not repeated for this
maintenance release.

## [0.5.0] - 2026-09-26

### Added

- Image generation for AI Task with an independent model and optional references.
- Native HA media results, bounded downloads and best-effort provider-file cleanup.

### Changed

- Default new STT entities to Realtime while preserving existing batch choices.

### Validation

374 automated tests passed. Live checks on HA 2026.9.3 covered image generation,
references, HA downloads, provider deletion, existing features and Realtime
cancellation/recovery.

## [0.4.0] - 2026-09-24

### Added

- Optional two-pass FFmpeg loudness normalization, enabled at -16 LUFS for new
  TTS entities and disabled during migration for existing entities.
- Per-request loudness overrides and bounded gain, peaks, processing and concurrency.

### Validation

327 automated tests passed. Normalized formats and existing features were
checked on HA 2026.9.3, with a live audio check before release.

## [0.3.1] - 2026-09-21

### Changed

- Update SDK to 2.10.1, tests to HA 2026.9.2 with fixtures 0.13.365,
  gazetteer-matcher to 1.2.0 and Ruff to 0.16.7.
- Extend cold-start tests to preset/custom voice responses.

### Fixed

- Restore the 0.3.0 changelog comparison link.

### Validation

The complete automated gate passed; live smoke tests were not repeated for
this dependency-only release.

## [0.3.0] - 2026-09-20

### Added

- Optional Realtime STT for Assist with bounded input/duration and deterministic
  connection cleanup. Existing entities retain batch behavior.

### Validation

282 automated tests passed. Startup, Realtime cancellation/recovery and
existing integration features were checked on HA 2026.9.2.

## [0.2.3] - 2026-09-14

### Changed

- Promote the tested SDK startup fixes from 0.2.3b1 without further runtime changes.

### Validation

Live Conversation, tools, AI Task, STT and TTS checks passed on HA 2026.9.2.

## [0.2.3b1] - 2026-09-14

### Fixed

- Move SDK construction/cleanup off the event loop, prepare services before
  requests and release resources after initialization failure or cancellation.

### Validation

239 automated tests passed, including fresh-process blocking detection.
Configuration and full-startup checks passed on HA 2026.9.2.

## [0.2.2] - 2026-09-12

### Changed

- Update SDK to 2.10.0, tests to HA 2026.9.1 with fixtures 0.13.364,
  and Ruff to 0.16.6.

## [0.2.1] - 2026-09-06

### Changed

- Update SDK to 2.9.4 and Ruff to 0.16.5.

### Fixed

- Correct comparison links for the 0.2.0 release history.

## [0.2.0] - 2026-09-05

### Fixed

- Support HA 2026.9 schema conversion and model-specific reasoning controls.

### Changed

- Promote the beta fixes with simplified reasoning controls and single-submit
  model changes.

### Validation

The beta was checked on HA 2026.8.6 and 2026.9.0 before the stable release.

## [0.2.0b2] - 2026-09-05

### Fixed

- Prefer canonical model IDs and reasoning rules over redirected legacy aliases.

### Changed

- Show reasoning choices during creation and fixed model states on reconfiguration.
- Save model changes in one submission.

## [0.2.0b1] - 2026-09-05

### Fixed

- Omit unsupported reasoning parameters and reject incompatible saved efforts
  locally. Preserve custom-model effort choices.

### Changed

- Introduce Automatic, Disabled and Enabled reasoning controls.

## [0.1.8b1] - 2026-09-04

### Fixed

- Support HA 2026.9 schema conversion while retaining older HA compatibility.

### Changed

- Update the test baseline and fixtures to HA 2026.9.0.

## [0.1.7] - 2026-08-29

### Fixed

- Restore the supported minimum to HA 2026.7.4 independently of test baselines.

## [0.1.6] - 2026-08-29

### Changed

- Raise the minimum to HA 2026.8.3 and update fixtures, Hassil, intents and Ruff.

## [0.1.5] - 2026-08-23

### Changed

- Raise the minimum to HA 2026.8.2 and update its fixtures.
- Update SDK to 2.9.3 and refresh Ruff/mypy.
- Update HACS instructions after inclusion in the default catalog.

## [0.1.4] - 2026-08-15

### Changed

- Update SDK to 2.9.2.

## [0.1.3] - 2026-08-08

### Changed

- Raise the minimum to HA 2026.8.1 and update its fixtures.

## [0.1.2] - 2026-08-08

### Changed

- Update SDK to 2.9.1.

## [0.1.1] - 2026-08-01

### Changed

- Update SDK to 2.8.0.

### Fixed

- Correct the HA/HACS icon and logo assets.

## [0.1.0] - 2026-07-26

### Added

- Public HACS integration with typed SDK streaming, Conversation and AI Task.
- STT, TTS with explicit voices, bounded image/PDF attachments and Assist tools.
- Capability checks, model repairs, reauthentication, diagnostics and migrations.
- English/French translations and automated quality, HACS and Hassfest checks.

[Unreleased]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.5...HEAD
[0.5.5]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.4...v0.5.5
[0.5.4]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Elijaht-dev/mistralai-conversation/compare/v0.3.1...v0.4.0
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
