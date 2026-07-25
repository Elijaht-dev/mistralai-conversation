# Quality and architecture

## Objective

Mistral AI Conversation aims for the engineering shape and operational behavior
of Home Assistant's first-party cloud conversation integrations while remaining
an independent custom integration. “Comparable” here describes architecture,
defensive behavior, and automated assurance; it does not claim first-party
status, Home Assistant review, or a Quality Scale certification.

## Design comparison

| Concern | Implementation |
| --- | --- |
| Home Assistant API | `ConversationEntity` and `ChatLog` streaming |
| Configuration | Config entry plus multiple conversation subentries |
| Provider boundary | Official `mistralai` SDK with generated message models |
| Lifecycle | Shared HA HTTP client, coordinator refresh, deterministic close |
| Credentials | Validation, duplicate prevention, and reauthentication |
| Tools | HA LLM APIs, typed schemas, parallel calls, bounded rounds |
| Multimodal | Bounded image and PDF data-URL attachments |
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
public. HACS cannot fetch private repository manifests. Weekly scheduled
validation and Dependabot help detect compatibility drift.

## Deliberate boundaries

- Unknown custom model IDs are allowed because model-card capabilities may be
  unavailable; Mistral remains the final capability authority.
- Mistral API behavior is mocked in CI. Live-provider conformance is a separate,
  opt-in activity because it costs money and requires secrets.
- HACS cannot install a private repository. The private preview is manual-only.
- Model-generated actions are not deterministic. Entity exposure and safeguards
  must be designed in Home Assistant.
- Compatibility is declared from Home Assistant 2026.7.4 and is continuously
  checked against the pinned development baseline.

## Release gate

A public release should not be made until validation is green, a real Home
Assistant installation has completed chat and tool-call smoke tests, private
review feedback is addressed, and repository visibility is intentionally changed
by the owner.
