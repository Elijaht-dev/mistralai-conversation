# Quality and architecture

## Objective

Mistral AI Conversation aims for the engineering shape and operational behavior
of Home Assistant's first-party cloud AI integrations while remaining an
independent custom integration. “Comparable” here describes architecture,
defensive behavior, and automated assurance; it does not claim first-party
status, Home Assistant review, or a Quality Scale certification.

## Design comparison

| Concern | Implementation |
| --- | --- |
| Home Assistant API | Conversation, AI Task, STT, and TTS entities |
| Configuration | One account entry with typed feature subentries |
| Provider boundary | Official `mistralai` SDK and generated request models |
| Lifecycle | Shared HA HTTP client, coordinator refresh, deterministic close |
| Credentials | Validation, duplicate prevention, and reauthentication |
| Tools | HA LLM APIs, typed schemas, parallel calls, bounded rounds |
| Structured data | Native strict Mistral JSON-schema response format |
| Multimodal | Bounded AI Task and conversation image/PDF attachments |
| Voice | Bounded Voxtral transcription and streamed speech generation |
| Voice identity | Explicit preset/saved voice selection; no silent cloning |
| Reasoning | Streamed display plus provider-native signed replay |
| Model lifecycle | Discovery, aliases, capabilities, deprecation repairs |
| Failures | Classified, translated errors and availability updates |
| Supportability | Redacted diagnostics and privacy-conscious request tracing |
| Evolution | Versioned config-entry migration |

## Automated assurance

Every push and pull request runs:

- Ruff formatting and a broad lint rule set
- strict mypy over integration code
- Home Assistant-native pytest tests with mocked provider streams
- branch coverage with an 85% minimum
- offline HACS repository-structure validation
- Home Assistant Hassfest validation

The official HACS remote validator additionally runs whenever the repository is
public. Weekly scheduled validation and Dependabot help detect compatibility
drift.

## Deliberate boundaries

- Unknown custom model IDs are allowed because model-card capabilities may be
  unavailable; Mistral remains the final capability authority.
- Mistral API behavior is mocked in CI. Live-provider conformance is a separate,
  opt-in activity because it costs money and requires secrets.
- Model-generated actions are not deterministic. Entity exposure and safeguards
  must be designed in Home Assistant.
- Speech recordings and text-to-speech input leave Home Assistant for Mistral;
  custom voice creation, consent, and retention remain outside this integration.
- TTS is not auto-created during migration because the provider requires an
  explicit preset or saved voice choice.
- Compatibility is declared from Home Assistant 2026.7.4 and is continuously
  checked against the pinned development baseline.

## Release gate

The public repository gate requires a clean secret and privacy review plus green
quality, HACS, and Hassfest validation. A versioned release should additionally
wait for a real Home Assistant installation to complete Conversation, tool-call,
AI Task, speech-to-text, and text-to-speech smoke tests.
