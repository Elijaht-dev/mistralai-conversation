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
| Speech loudness | Opt-in per existing TTS entity; local bounded two-pass FFmpeg processing for new entities |
| Realtime STT | Incremental PCM upload, supervised WebSocket lifecycle, final transcript only |
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
  Services are prepared before use. For the pinned SDK 2.10.1, selected lazy
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
- Realtime STT is an explicit model choice. It sends PCM chunks during Assist's
  STT stage, uses provider language detection, and returns only a completed final
  transcript. Home Assistant retains end-of-speech detection. The batch model
  remains the default, and custom IDs retain their existing batch behavior.
- Realtime opens a separate WebSocket per request using Home Assistant's cached,
  verified TLS context. SDK 2.10.1's connection helper cannot accept that context,
  so a small connection adapter handles the handshake while the official SDK
  owns audio messages and transcription events. No global SDK or TLS function is
  replaced. The shared Home Assistant HTTP client remains in use for HTTP
  requests. Request tasks and connections are closed on completion, failure,
  cancellation, and entity unload.
  The audio size limit remains 25 MiB, with a 10-second connection deadline and
  a 300-second overall request deadline. A failed request is not retried through
  a different transcription endpoint.
- TTS is not auto-created during migration because the provider requires an
  explicit preset or saved voice choice.
- New TTS subentries enable local two-pass FFmpeg loudness normalization at
  -16 LUFS; migration keeps existing subentries disabled and preserves their
  model and voice. The whole-number target range is -24 to -12 LUFS. Home
  Assistant service and media-source options can override each request, and
  both defaults enter its TTS cache key. The disabled path requests and returns
  the original provider format without processing. When enabled, Mistral sends
  WAV; FFmpeg measures it, then applies bounded gain and a -2 dBTP ceiling.
  Upward gain is capped at 20 dB, and silence or short/unmeasurable audio is
  never amplified. Input and output are capped at 25 MiB, diagnostics at 64 KiB,
  processing at 30 seconds including queue time, and concurrent jobs at two per
  account. Audio stays in process pipes; timeout, cancellation, and unload kill
  and reap subprocesses. Local failures are translated without changing
  provider availability or silently returning unprocessed audio.
- Compatibility is declared from Home Assistant 2026.7.4. Automated tests use
  the newer Home Assistant 2026.9.2 development baseline without raising the
  user-facing minimum.

## Release gate

The `0.4.0` loudness-normalization candidate was checked on Home Assistant
2026.9.3 on 2026-09-23 after a backup, configuration validation, and full restart.
Installed files matched the tested candidate; migration preserved existing
settings and left normalization disabled. Live API requests produced normalized
WAV, MP3, FLAC, and float32 PCM within 1 LU of -16 LUFS, and Opus within 1 LU of
-24 LUFS, with decoded peaks below 0 dBTP. Conversation, a read-only Assist tool
round trip, structured AI Task output, existing batch STT, and unprocessed TTS
also passed. No warnings or errors for the running integration appeared in the
observed logs. These automated checks used synthetic speech without speaker
playback. I also tested the audio on my setup on 2026-09-24 before publication.
Publication additionally requires the public CI gate below.
End-to-end request timings include provider generation and do not isolate
FFmpeg processing latency.

Version `0.3.1` is dependency maintenance. I released it after the complete
automated gate without repeating the real-instance smoke test.
Its voice-discovery cold-start test covers preset and custom voice
responses, including the `type` field required by SDK 2.10.1.

Version `0.3.0` was checked on Home Assistant 2026.9.2 after configuration
validation and a full restart. The installed runtime matched the tested files,
and the observed logs contained no Mistral blocking-call warnings or errors.
Synthetic audio passed through a temporary Assist STT pipeline using Realtime;
cancellation and a subsequent transcription also passed. The temporary STT
subentry and pipeline were removed, preserving the original configuration.
Existing batch STT, Conversation, an Assist date/time tool round trip, structured
AI Task output, and TTS generation without playback also passed through Home
Assistant's public APIs. These checks do not measure physical microphone or
speaker behavior, nor establish a latency improvement over batch STT.

The `0.2.3b1` beta targets SDK startup blocking (issue #51). Configuration
validation and a full restart passed on Home Assistant 2026.9.2, with no Mistral
blocking-call warnings or setup errors in the observed startup logs.
I then tested Conversation, Assist tool-call, AI Task, STT, and TTS on that
instance. These manual checks complement the startup-log and installed-file
checks.
Reloading an integration alone cannot verify a cold-start fix because it may
reuse already imported modules. Version `0.2.3` promotes this tested beta to
stable without runtime code changes.

The public repository gate requires a clean secret and privacy review plus green
quality, HACS, and Hassfest validation. A versioned release should additionally
wait for a real Home Assistant installation to complete Conversation, tool-call,
AI Task, speech-to-text, and text-to-speech smoke tests.

For patch-only dependency maintenance, I may rely on the complete automated
gate without repeating the real-instance smoke test. The release notes must
state that boundary.

For 0.2.0, I tested the beta on two real Home Assistant installations (2026.8.6
and 2026.9.0) before publishing the stable release. I did not record individual
feature results in this repository. Runtime code is unchanged from 0.2.0b2.
