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

- SDK construction and synchronous cleanup run in Home Assistant's executor.
  Services are prepared before use. For the pinned SDK 2.10.0, selected lazy
  model exports and the utility/error exports are resolved and cached during
  integration import. This binds the original SDK objects without replacing
  their behavior or disabling Home Assistant's blocking-call detection.
- Fresh-process tests enable Home Assistant's blocking detector before importing
  the integration and exercise the real SDK against a mocked HTTP transport.
  These complement the Home Assistant-native tests, whose imports can otherwise
  hide first-use SDK imports. No live provider request is made.
- Unknown custom model IDs are allowed because model-card capabilities may be
  unavailable; Mistral remains the final capability authority.
- Conversation and AI Task share model-specific reasoning validation in setup
  and runtime. Discovery's boolean capability is authoritative for non-reasoning
  models; exact documented IDs and discovered aliases constrain Small 4 / Medium
  3.5 to None / High and Magistral to provider-default native reasoning. The SDK
  does not expose per-model effort lists, so other models remain permissive.
  Exact model IDs take precedence over aliases, and canonical reasoning rules
  take precedence over legacy aliases attached to a newer model.
- The UI labels auto / none / high as Automatic / Disabled / Enabled and shows
  fixed translated states for non-reasoning and native-reasoning models only
  during reconfiguration. Creation always shows the dropdown. Model changes save
  in one submission, applying the model's fixed behavior where necessary. Custom
  efforts remain manually configurable and retain their stored meaning.
- Automatic omits the reasoning parameter. Disabled also omits it for
  non-reasoning and unknown models. Existing incompatible settings are preserved
  until explicitly reconfigured; confirming a fixed state saves its applicable
  setting. No stored value is migrated solely to change its UI label.
- Mistral API behavior is mocked in CI. Live-provider conformance is a separate,
  opt-in activity because it costs money and requires secrets.
- Model-generated actions are not deterministic. Entity exposure and safeguards
  must be designed in Home Assistant.
- Speech recordings and text-to-speech input leave Home Assistant for Mistral;
  custom voice creation, consent, and retention remain outside this integration.
- TTS is not auto-created during migration because the provider requires an
  explicit preset or saved voice choice.
- Compatibility is declared from Home Assistant 2026.7.4. Automated tests use
  the newer Home Assistant 2026.9.1 development baseline without raising the
  user-facing minimum.

## Release gate

The `0.2.3b1` beta targets SDK startup blocking (issue #51). Configuration
validation and a full restart passed on Home Assistant 2026.9.2, with no Mistral
blocking-call warnings or setup errors in the observed startup logs.
The maintainer subsequently confirmed successful Conversation, Assist tool-call,
AI Task, STT, and TTS smoke tests on that instance. These functional results are
maintainer-reported and complement the startup-log and installed-file checks.
Reloading an integration alone cannot verify a cold-start fix because it may
reuse already imported modules. Version `0.2.3` promotes this tested beta to
stable without runtime code changes.

The public repository gate requires a clean secret and privacy review plus green
quality, HACS, and Hassfest validation. A versioned release should additionally
wait for a real Home Assistant installation to complete Conversation, tool-call,
AI Task, speech-to-text, and text-to-speech smoke tests.

For patch-only dependency maintenance, the maintainer may explicitly accept the
complete automated gate without repeating the real-instance smoke test. The
release report must state that boundary.

For 0.2.0, the maintainer reported testing the beta on two real Home Assistant
installations (2026.8.6 and 2026.9.0) and approved stable publication. This is
maintainer-reported installation testing; individual feature results were not
recorded in this repository. Runtime code is unchanged from 0.2.0b2.
