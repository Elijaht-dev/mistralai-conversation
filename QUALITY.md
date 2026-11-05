# Quality and architecture

This document describes the integration's runtime guarantees, verification and
release requirements. User configuration is covered in [README.md](README.md).

## Architecture

| Area | Responsibility |
| --- | --- |
| Home Assistant entities | Thin Conversation, AI Task, STT and TTS adapters |
| Configuration | One account entry with typed feature subentries and versioned migrations |
| Provider boundary | Official Mistral SDK and generated request/message types |
| Shared state | Typed `ConfigEntry.runtime_data` and coordinator-managed discovery/availability |
| Model selection | Curated menus, custom IDs, capability validation, aliases and retirement repairs |
| Conversation engine | Message conversion, streaming, native reasoning replay and bounded tool execution |
| Media | Bounded attachments, image downloads and local speech normalization |
| Support | Translated errors, reauthentication, repairs and redacted diagnostics |

### Lifecycle and compatibility

Home Assistant 2026.7.4 is the supported minimum. The runtime pins Mistral SDK
3.7.0 and HTTPX2 2.14.0.

HA 2026.11 supplies the shared HTTPX2 client. Older supported versions use an
owned HTTPX2 client per account with HA's verified TLS context. Setup failures,
cancellation and unload release owned resources without closing HA's shared
transport. SDK construction, synchronous cleanup and blocking media work run
in the executor. Services and SDK imports are prepared before requests.

Realtime uses a separate verified WebSocket per request. Session configuration
must be acknowledged before audio upload. Upload and response tasks are
supervised and cancelled on failure or entity removal.

Configuration migration 1.5 preserves explicit model choices and materializes
previous implicit defaults before new entities adopt Voxtral 3 and Large 4.
Saved voices and normalization options are preserved; TTS requires an explicit
voice selection.

### Models, conversation and tools

- Discovered capabilities are authoritative for known models. Unknown custom
  and fine-tuned IDs remain configurable.
- Exact model IDs take precedence over aliases. Documented model-specific
  reasoning rules are shared by configuration and runtime.
- Automatic reasoning leaves the choice to Mistral. Unsupported reasoning
  parameters are omitted or rejected locally, according to model capabilities.
- Provider-native signed reasoning is replayed in its original format.
  Signatures are never altered, exposed or logged.
- Home Assistant owns tool schemas, validation and execution. Malformed,
  incomplete or ambiguous streamed calls are rejected before execution.
- Conversation replay omits external tool calls and their matched results
  while preserving user messages, replies and ordinary tool exchanges.
  Home Assistant's original chat log is not modified.
- Structured AI Task data uses native JSON-schema output.

### Voice and image handling

STT Automatic resolves discovered capabilities before documented IDs. Explicit
modes cannot bypass known incompatibilities. Unknown IDs use Batch unless
Realtime is selected. Assist owns end-of-speech; Realtime turn events are
advisory and the final transcript is required. Failures do not trigger a retry
through another endpoint.

TTS normalization measures provider WAV audio and applies a second FFmpeg pass.
Gain and peaks are bounded; silence and short/unmeasurable clips are not
amplified. Audio stays in process pipes. Timeout, cancellation and unload reap
subprocesses. Normalization failure does not return unprocessed audio or mark
the provider unavailable. Resampling and lossy encoding can shift decoded peaks.

Image generation uses Mistral's Conversations and Files APIs with only the
image-generation tool and `store=False`. It creates no saved agent. Known models
must advertise vision for references, which use supported image formats.
The first valid image is returned through HA's native media result; a text-only
answer is an error.

Cleanup attempts deletion of deduplicated generated file IDs returned by the
request. A cleanup failure does not discard an image already retrieved.
Neither this deletion nor `store=False` guarantees zero provider retention.
Home Assistant owns its local media copy.

### Failures and privacy

Authentication starts reauthentication. Timeout, connection, rate-limit and
provider failures remain distinct. Connection failures update availability and
schedule discovery recovery; validation and rate-limit errors do not mark the
account offline.

Empty streams, explicit stream errors, unsupported content, malformed media and
exceeded limits fail closed. Diagnostics redact credentials, prompts and voice
IDs. Request traces exclude prompt, attachment and audio content. Provider error
text is bounded and normalized. See [SECURITY.md](SECURITY.md) for deployment
guidance and private vulnerability reporting.

## Limits

| Bound | Value |
| --- | --- |
| Declared tools / tool rounds | 128 / 10 per request |
| Configured response tokens | 32,768 maximum |
| Attachments | 10 files, 20 MiB each |
| Generated image | 20 MiB; 2,048-token text-output limit |
| STT input / TTS output | 25 MiB per request |
| TTS input text | 5,000 characters |
| Setup / provider request timeout | 10 / 300 seconds |
| Realtime / image generation and download | 300-second overall deadline |
| Generated-file cleanup | Separate 10-second deadline |
| Voice discovery | 1,000 rows maximum |
| Speech normalization | 30 seconds including queue time; two jobs per account |
| Loudness target / maximum upward gain | Whole-number -24 to -12 LUFS / 20 dB |
| Normalization true-peak ceiling | -2 dBTP during processing |
| FFmpeg diagnostic output | 64 KiB |

## Automated assurance

CI runs formatting, lint, dependency checks, HA-native tests, coverage, offline
HACS validation and Hassfest. Public repositories also run the official HACS
validator. Strict mypy runs on the primary HA baseline. Branch coverage must
remain at or above 85%.

The three HA versions below use separate pinned environments. The primary one
uses the official HA fixtures at the release source commit; older baselines use
their matching fixture packages. Fresh-process tests enable HA's blocking-call
detector and exercise the real SDK with mocked HTTP and local TLS transports.
Normal tests and CI never call the live Mistral API.

The test environment pins PyTurboJPEG 2.6.1. A real JPEG encode/decode test uses
checksum-verified libjpeg-turbo 3.2.1. Setup and commands are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Recorded validation

The unreleased SDK 3 candidate was checked on 2026-11-04/05 under Python 3.14:

| Home Assistant | Tests passed | Total coverage | Branch coverage |
| --- | --- | --- | --- |
| 2026.11.0 | 437 | 94.18% | 88.84% |
| 2026.10.4 | 437 | 94.18% | 88.84% |
| 2026.7.4 | 437 | 94.32% | 88.99% |

Ruff, strict mypy on the primary baseline, dependency checks, English/French
translation parity and offline HACS validation passed. Public CI remains
required for publication.

A real HA 2026.11.0 instance was backed up, checked and fully restarted with
the validated candidate. The running SDK was 3.7.0. Migration preserved original
model choices, voice/audio settings and pipelines.

| Live check | Result |
| --- | --- |
| Fresh Conversation and follow-ups after one/two local intents | Passed |
| Read-only Assist tool round trip | Passed |
| Structured AI Task | Passed |
| Synthetic batch STT | Passed |
| Realtime 3 transcription, cancellation and recovery | Passed |
| TTS generation and audio retrieval without playback | Passed |
| Image generation, references and provider-file deletion | Not validated |

Temporary entities and the Realtime pipeline were removed. Installed runtime
files matched the candidate. No Mistral warning/error or blocking-call lines
appeared in the observed logs. Physical microphone and speaker behavior was
not tested.

Image requests with Large 4, Small and the previous Medium alias returned no
generated-file chunks in the SDK 3.7.0 checks. The cause is not established;
this does not prove that Large 4 is inaccessible. Missing images are rejected,
and the original image configuration was preserved.

## Publication gate

Before a feature or SDK release:

1. Pass the complete local checks and public HACS/Hassfest CI.
2. Back up a real instance, verify installed files, validate configuration and
   perform a full restart.
3. Verify Conversation, local-intent follow-ups, a read-only tool round trip,
   structured AI Task, batch STT, Realtime cancellation/recovery and TTS retrieval.
4. For image generation, verify a text request, a reference image, HA media
   retrieval, provider deletion confirmation and failed retrieval after deletion.
5. Confirm preserved configuration, removed test entities and clean observed
   integration logs. Record untested paths and limitations.

Patch-only dependency maintenance may rely on the complete automated gate
without repeating live smoke tests, with that boundary stated in the release
notes. Publishing a release or tag requires explicit owner authorization.
