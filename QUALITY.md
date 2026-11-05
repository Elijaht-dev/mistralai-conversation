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
| Lifecycle | Shared HA HTTPX2 client, owned compatibility transport on older HA, coordinator refresh, deterministic close |
| Credentials | Validation, duplicate prevention, and reauthentication |
| Tools | HA LLM APIs, typed schemas, parallel calls, bounded rounds |
| Structured data | Native strict Mistral JSON-schema response format |
| Multimodal | Bounded AI Task and conversation image/PDF attachments |
| Image generation | Native AI Task image results, independent model, bounded download and best-effort remote cleanup |
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

- Conversation replay omits external tool calls and results matched to those
  calls, including locally handled Assist intents and sentence triggers. User
  messages, assistant replies, provider-native reasoning, and ordinary tool
  calls/results are preserved without modifying Home Assistant's chat log.
  Unmatched tool results are not silently discarded.
- SDK construction and synchronous cleanup run in Home Assistant's executor.
  Services are prepared before use. For the pinned SDK 3.7.0, selected lazy
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
- Realtime STT is the default for newly created entities. It sends PCM chunks
  during Assist's STT stage, uses provider language detection, and returns a final
  transcript. Home Assistant retains end-of-speech detection. Existing entities
  retain their model; legacy configurations without one retain the batch default.
  Unknown custom IDs use Batch in Automatic mode and can explicitly use Realtime.
- Realtime opens a separate WebSocket per request using Home Assistant's cached,
  verified TLS context. SDK 3.7.0's connection helper cannot accept that context,
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
- Existing AI Task entities also expose image generation. The image-model setting
  defaults to `mistral-large-4` for new entities. Migration 1.5 materializes the
  previous implicit `mistral-medium-latest` choice and preserves explicit values.
  This selects the Mistral model calling the image tool, not the underlying
  image generator managed by the provider.
- Image requests use the pinned SDK's asynchronous Conversations and Files APIs
  through Home Assistant's shared HTTP client. No saved agent is created and
  `store=False` is explicit. Only the image-generation tool is enabled, with a
  2,048-token text output limit and no automatic generation retry.
- Image references reuse the bounded attachment encoder, restricted to supported
  image types. Known model vision capabilities are checked; custom IDs remain
  configurable. References provide visual context, not a promise of exact editing.
- Image downloads are streamed with a 20 MiB limit and validated in the executor.
  The first image is returned through `GenImageTaskResult`; Home Assistant owns
  media storage and access. Generation/download share a 300-second deadline.
  Entity unload cancels active requests before the shared client closes.
- Remote cleanup attempts deletion of the deduplicated generated file IDs returned
  by that request, with a separate ten-second deadline. A failed cleanup does not
  mask the original failure or discard a downloaded image. Neither `store=False`
  nor best-effort deletion guarantees zero provider retention: files can remain
  when cleanup fails or the provider response containing their IDs is unavailable.
  Remote deletion does not remove Home Assistant's local media copy.
  I verified generated-file deletion against the live API on 2026-09-26:
  deletion was confirmed and a subsequent download was rejected. The reference
  request also succeeded; exact editing fidelity remains unverified.
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
- Compatibility is declared from Home Assistant 2026.7.4. Automated tests cover
  that minimum, 2026.10.4, and 2026.11.0 in separate environments.

The candidate uses Mistral SDK 3.7.0 and HTTPX2 2.14.0. Home Assistant 2026.11
supplies the shared transport; older supported releases use an explicitly owned
HTTPX2 client per account. Setup failure, cancellation, validation and unload
release owned resources without closing HA's shared client. The WebSocket
adapter still supplies HA's verified TLS context because the SDK connector has
no public SSL-context argument.

Curated selectors retain custom IDs and saved legacy values. Discovery retains
chat and audio capabilities for validation and routing. STT Automatic uses
capabilities before documented IDs; explicit modes cannot bypass known limits.
Realtime session configuration is acknowledged before PCM upload. SDK-typed
turn events do not terminate transcription; Assist owns end-of-speech and the
final provider transcript is required. Existing model defaults are materialized
by configuration migration 1.5 before new entities adopt Voxtral 3 and Large 4.

Tests use the official HA 2026.11 fixture source pinned to the release commit,
with separate legacy fixture packages for HA 2026.10.4 and 2026.7.4. Fixture
imports and schema rendering follow the installed HA version. Test transports
and provider responses are mocked; normal CI never makes a live Mistral request.

## Release gate

The unreleased HTTPX2 / SDK 3 candidate passed 437 tests on each of Home
Assistant 2026.11.0, 2026.10.4, and 2026.7.4 under Python 3.14. Total coverage
was 94.18%, 94.18%, and 94.32%; branch coverage was 88.84%, 88.84%, and 88.99%,
respectively. Ruff, strict mypy on the 2026.11 baseline, dependency checks in all
three environments, matching English/French translation keys and placeholders,
and offline HACS validation passed. Public HACS and Hassfest remain CI gates.
The test environment uses PyTurboJPEG 2.6.1 with the checksum-verified native
libjpeg-turbo 3.2.1 backend; a real synthetic JPEG encode/decode test passes on
each HA baseline.

On 2026-11-04/05, after a verified integration/configuration backup, candidate
hash checks, configuration validation and a full restart on HA 2026.11.0,
the running integration reported SDK 3.7.0. Migration 1.5 preserved the original
models, saved voice, audio options and pipelines. Fresh Conversation, follow-ups
after one and two locally handled time queries, a read-only tool round trip,
structured AI Task, synthetic batch STT, and TTS generation with audio retrieval
and no playback passed through HA's public APIs. A temporary Realtime 3 STT
entity and isolated pipeline passed transcription, cancellation and recovery;
both were removed. The installed runtime still matched the validated candidate,
and the observed logs contained no Mistral warning/error or blocking-call lines.
Physical microphone and speaker behavior was not tested.

Live image generation is not validated for this candidate. The temporary
Large 4 entity's image action failed without a generated image file. Isolated
SDK 3.7.0 probes with Large 4, Small and the previous Medium alias likewise
returned no generated-file chunks. These observations do not establish the cause
or prove that Large 4 itself is inaccessible. The integration rejects missing
images without a silent retry or model fallback; original image settings were
preserved and the temporary entity was removed. Real image generation, reference
images and provider-file deletion still need a successful live validation before
publishing this feature/dependency release. The candidate remains installed on
the test instance with its original configuration and a recorded rollback.

Version `0.5.5` updates only the test environment, documentation, and integration
version. The full suite passed 385 tests on Home Assistant 2026.10.4 with
94.07% total coverage and 88.34% branch coverage. Ruff, strict mypy, dependency
checks, and offline HACS validation passed. Mistral SDK 2.10.1 and the Home
Assistant 2026.7.4 minimum are unchanged.

On Home Assistant 2026.10.4, the installed 0.5.4 runtime matched all 23 candidate
files other than the versioned manifest. Fresh Conversation, follow-ups after
one and two locally handled time queries, a read-only tool round trip, and
structured AI Task passed. Initial audio checks failed because both configured
model IDs were rejected by Mistral, and the saved voice required Voxtral 3.
After a protected backup, I reconfigured only those two model IDs to
`voxtral-mini-transcribe-3-0` for batch STT and `voxtral-mini-tts-3-0` for TTS,
preserving the saved voice, normalization settings, and pipelines. Synthetic
STT and TTS generation with audio retrieval and no playback then passed through
Home Assistant's public APIs using SDK 2.10.1. Conversation, local-intent
follow-ups, read-only tools, and structured AI Task also passed again. The
observed validation logs contained no Mistral warnings, errors, or blocking
warnings after correction. No integration runtime was installed or changed on
the instance; physical microphone and speaker behavior was not tested.

Version `0.5.4` updates the test baseline to Home Assistant 2026.10.1 and
supports its native tool-result container while preserving older history.
The existing Probatio/OpenAPI conversion is retained; schema annotations now
match Home Assistant's runtime alias. The full suite passed 385 tests on both
Home Assistant 2026.10.1 and 2026.9.4, with 94.07% total coverage and 88.34%
branch coverage on the newer baseline, Ruff, strict mypy, dependency checks,
and offline HACS validation.

I checked this candidate on Home Assistant 2026.10.1 on 2026-10-12 after a
protected integration/configuration backup, candidate hash verification,
configuration validation, and full restart. Fresh Conversation, follow-ups
after one and two locally handled time queries, a read-only tool round trip,
structured AI Task, synthetic STT, and TTS generation without playback passed.
Configuration and pipelines were preserved. The observed logs contained no
Mistral errors or blocking-call warnings. Physical microphone and speaker
behavior was not tested. Publication also requires successful public CI on
the release commit.

I validated the `0.5.3` local-intent history fix with 384 passing tests,
94.15% total coverage, 88.50% branch coverage, Ruff, strict mypy, and offline
HACS structure validation. On Home Assistant 2026.10.0 on 2026-10-08, after
backing up the installed integration and configuration, I checked a fresh
Mistral conversation and Mistral follow-ups after one and two locally handled
time queries using the same conversation ID. All succeeded, and Home Assistant's
original local-turn history remained intact. A read-only date/time tool round
trip and a structured AI Task also succeeded. The observed post-install logs
contained no Mistral errors or blocking-call warnings. Synthetic speech-to-text
input and text-to-speech generation without playback also succeeded on that
instance. Satellite playback was not tested.

Version `0.5.2` updates the combined test environment to Home Assistant 2026.9.4,
pytest fixtures 0.13.367, intents 2026.9.30, and Ruff 0.16.9. I validated it
locally with 376 passing tests, 94.04% total coverage, 88.23% branch coverage,
`pip check`, Ruff, strict mypy, and HACS structure validation. I skipped the
real-instance smoke test for this maintenance release. Runtime requests,
Mistral SDK 2.10.1, and the Home Assistant 2026.7.4 minimum are unchanged.
Mistral SDK 3 is deferred until Home Assistant provides a compatible shared
HTTPX2 client. Public HACS and Hassfest checks remain release gates.

Version `0.5.1` clarifies the image-tool model setting and updates test dependencies.
I validated it locally with 376 passing tests, 94.04% total coverage and 88.23%
branch coverage, Ruff, strict mypy, and HACS structure validation. I skipped the
real-instance smoke test for this maintenance release. The Mistral SDK and runtime
request behavior are unchanged; public HACS and Hassfest checks remain release gates.

Before releasing image generation, I must check text-only generation, image
references, the native Home Assistant media result, and remote file deletion on
a real instance. Deletion verification must include the API's deletion status
and a subsequent failed retrieval of the test image. The existing Conversation,
tool-call, structured AI Task, STT, and TTS smoke tests also remain required.

I checked the image-generation candidate on Home Assistant 2026.9.3
on 2026-09-26 after backing up the integration and its configuration, validating
the configuration, and restarting Home Assistant. Installed files matched the
tested candidate and existing settings were unchanged. Text-only generation,
a generated-image reference, and both native media downloads succeeded.
Conversation, a read-only Assist tool round trip, structured AI Task data, batch
STT, Realtime transcription with cancellation and recovery, and TTS without
speaker playback also passed. I observed no integration
warnings, cleanup warnings, or blocking-call warnings. The automated gate passed
374 tests with 94.03% total coverage and 88.23% branch coverage, Ruff, strict mypy,
and local HACS validation. Publication also requires successful public HACS and
Hassfest checks in CI.

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
