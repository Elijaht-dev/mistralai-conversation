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
- Realtime STT is the default for newly created entities. It sends PCM chunks
  during Assist's STT stage, uses provider language detection, and returns a final
  transcript. Home Assistant retains end-of-speech detection. Existing entities,
  legacy configurations with no saved model, and migration-created legacy STT
  entities retain batch behavior. Custom IDs continue to use the batch API.
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
- Existing AI Task entities also expose image generation. An optional image-model
  setting defaults to `mistral-medium-latest` without rewriting saved data or
  changing data-generation settings. No new config subentry or migration is needed.
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
- Compatibility is declared from Home Assistant 2026.7.4. Automated tests use
  the newer Home Assistant 2026.9.4 development baseline without raising the
  user-facing minimum.

## Release gate

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
