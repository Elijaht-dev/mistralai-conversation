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
3.7.0. HA supplies HTTPX2 on 2026.11; older versions install it through the SDK's
dependency. Test environments pin HTTPX2 2.14.0 and also cover 2.16.0 on older HA
versions, matching their current fresh-install resolution.

HA 2026.11 supplies the shared HTTPX2 client. Older supported versions use an
owned HTTPX2 client per account with HA's verified TLS context. Setup failures,
cancellation and unload release owned resources without closing HA's shared
transport. SDK construction, synchronous cleanup and blocking media work run
in the executor. Services and SDK imports are prepared before requests.

Realtime STT and TTS use a separate verified WebSocket per request. Session
configuration must be acknowledged before input upload. Input and response
tasks are supervised and cancelled on failure or entity removal. TTS also
closes requests when its audio generator is closed, including paused consumers.

Configuration migration 1.5 preserves explicit model choices and materializes
previous implicit defaults before new entities adopt Voxtral 3 and Large 4.
Saved voices and normalization options are preserved; TTS requires an explicit
voice selection.
Configuration migration 1.6 materializes Standard synthesis mode for existing
TTS entities without changing models, voices, audio options or subentry IDs.

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

TTS Standard preserves the buffered HTTP path. Explicit Realtime mode bridges
HA's incremental text/audio interface to the official SDK speech protocol in
`responsive` mode. It requires normalization to be disabled, including service
overrides. Known speech modes and output formats are validated against discovery;
missing metadata does not prohibit custom models. Voice selection is explicit.

Realtime TTS validates its session acknowledgement and verifies UUID/slug voice
aliases against the official voice record when the acknowledged identifier
differs. An unrelated voice is rejected before text upload. Metadata lookup
shares the 10-second setup deadline and preserves authentication, rate-limit
and transport error classification.
It decodes each base64 delta strictly and requires a final speech event after
input completion. It then closes the one-generation connection from the client
side, following the SDK contract. The provider may end TCP without a WebSocket
close frame after its final event.
Missing or premature final events still fail closed. A coalescing byte buffer
decouples protocol reads from a slow HA converter or audio consumer. It is
bounded by the cumulative 25 MiB output limit; fast readers receive audio
immediately. The WebSocket frame queue is also bounded. Text/audio limits are
cumulative and the request deadline runs independently of consumer speed. Provider failures
never cause an HTTP retry. A late failure invalidates the result but cannot
withdraw already played audio. Protocol errors contain only integration-owned
messages; provider event payloads and close-frame reasons are not logged.
WebSocket control frames and keepalive continue to be processed while HA's audio
consumer is paused. The overall 300-second deadline still closes stalled
provider requests independently of the consumer.

Container audio allows HA to convert for satellite playback. Raw Mistral PCM
remains float32 little-endian mono at 24 kHz and must not be labelled as Assist
16-bit PCM. Local TLS and mocked incremental-stream tests cover transport and
HA conversion; a physical satellite and measured playback latency are separate
release validation requirements.

Image generation uses Mistral's Conversations API with only the image-generation
tool and `store=False`. It creates no saved agent. Known models must advertise
vision for references, which use supported image formats.
The first valid image is returned through HA's native media result; a text-only
answer is an error.

Files API IDs take priority over URLs. URL-only results must come from the
image tool's structured result, never assistant text. Only HTTPS Mistral file
hosting and signed Azure Blob storage URLs are accepted. Downloads reuse the
account transport without forwarding authentication, default headers or cookies;
redirects are rejected. Both formats share the byte, decoding and timeout bounds.

Cleanup attempts deletion of deduplicated generated file IDs returned by the
request. A cleanup failure does not discard an image already retrieved.
URL-only results cannot be deleted through the Files API. Neither deletion,
signed-link expiration nor `store=False` guarantees zero provider retention.
Home Assistant owns its local media copy.

### Failures and privacy

Authentication starts reauthentication. Timeout, connection, rate-limit and
provider failures remain distinct. Connection failures update availability and
schedule discovery recovery; validation and rate-limit errors do not mark the
account offline.

Empty streams, explicit stream errors, unsupported content, malformed media and
exceeded limits fail closed. Diagnostics redact credentials, prompts and voice
IDs. Integration traces exclude prompt, attachment and audio content and signed
storage URLs. Provider error text is bounded and normalized. See
[SECURITY.md](SECURITY.md) for deployment guidance and private vulnerability
reporting.

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

The HA/HTTPX2 combinations below use separate pinned environments. The primary one
uses the official HA fixtures at the release source commit; older baselines use
their matching fixture packages. Fresh-process tests enable HA's blocking-call
detector and exercise the real SDK with mocked HTTP and local TLS transports.
Normal tests and CI never call the live Mistral API.

The test environment pins PyTurboJPEG 2.6.1. A real JPEG encode/decode test uses
checksum-verified libjpeg-turbo 3.2.1. Setup and commands are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Recorded validation

### Version 1.0.2

Packaging only: Python files and dependency pins are unchanged from 1.0.1.
ZIP layout is verified; runtime tests and live smoke checks were not repeated.

### Version 1.0.1

The voice UUID/slug acknowledgement regression is covered by public HA TTS
tests, strict rejection of unrelated voices, lookup failures, cancellation and
the real SDK over local TLS with HA's blocking detector enabled.

All 602 tests passed in each of the five pinned HA/HTTPX2 environments, with
94.72–94.84% total coverage and 90.08–90.20% branch coverage. Formatting, linting,
strict typing, dependency checks and offline HACS validation passed.

After backup, configuration validation and a full restart on HA 2026.11.0,
all 25 installed runtime files matched the candidate. The existing voice UUID,
entity settings and Assist pipeline were preserved. Standalone and Assist audio
returned HTTP 200 and decoded successfully. The owner confirmed playback in the
browser and mobile app. Synthetic Assist audio arrived before text completion.

### Version 1.0.0

Automated validation completed on 2026-11-06 under Python 3.14.7 with Mistral
SDK 3.7.0. Ruff formatting/lint, strict mypy on the primary baseline, dependency
checks, translation parity and offline HACS validation passed.

| Home Assistant | HTTPX2 | Tests passed | Total coverage | Branch coverage |
| --- | --- | --- | --- | --- |
| 2026.11.0 | 2.14.0 | 580 | 94.69% | 90.00% |
| 2026.10.4 | 2.14.0 | 580 | 94.69% | 90.00% |
| 2026.7.4 | 2.14.0 | 580 | 94.82% | 90.13% |
| 2026.10.4 | 2.16.0 | 580 | 94.69% | 90.00% |
| 2026.7.4 | 2.16.0 | 580 | 94.82% | 90.13% |

Automated checks use synthetic audio, mocked provider events and a local TLS
server. They cover incremental delivery, WAV conversion for satellite formats,
TCP termination after the final speech event, and heartbeat handling with a slow
audio consumer. Truncated streams without a final event still fail closed.

A real HA 2026.11.0 instance was backed up, configuration-checked and fully
restarted with the candidate. Migration 1.6 preserved saved models, voices,
normalization settings, subentry IDs and pipelines. All 25 installed runtime
files matched the validated candidate.

| Live check | Result |
| --- | --- |
| Conversation and follow-ups after one/two local intents | Passed |
| Read-only Assist tool round trip and structured AI Task | Passed |
| Synthetic batch STT and Realtime STT cancellation/recovery | Passed |
| Standard TTS generation and retrieval without playback | Passed |
| Realtime TTS through a native Assist pipeline and standalone retrieval | Passed |
| Complete realtime audio decoded with FFmpeg | Passed |
| Provider TTS incremental input, cancellation and recovery | Passed |
| Image generation and reference-image retrieval | Not revalidated: account HTTP 429 rate limit |

In the final synthetic Assist check, the first audio bytes arrived 4.95 seconds
after the request, before intent processing finished at 8.45 seconds. Complete
audio retrieval took 38.20 seconds. These are API-stream measurements from one
request, not physical playback latency or a general performance guarantee.

Temporary entities and pipelines were removed. The final candidate's observed
integration logs contained no warning/error or blocking-call lines. Physical
microphone, satellite and speaker behavior was not tested.

Live image requests, including direct SDK checks, hit the Mistral account's
HTTP 429 rate limit. No successful image/reference round trip is claimed for
1.0.0; the last successful live image validation remains recorded under 0.6.0.

### Version 0.6.0

Validation for 0.6.0, including SDK 3 and image URL support, was performed on
2026-11-05 under Python 3.14:

| Home Assistant | HTTPX2 | Tests passed | Total coverage | Branch coverage |
| --- | --- | --- | --- | --- |
| 2026.11.0 | 2.14.0 | 502 | 94.45% | 89.43% |
| 2026.10.4 | 2.14.0 | 502 | 94.45% | 89.43% |
| 2026.7.4 | 2.14.0 | 502 | 94.59% | 89.57% |
| 2026.10.4 | 2.16.0 | 502 | 94.45% | 89.43% |
| 2026.7.4 | 2.16.0 | 502 | 94.59% | 89.57% |

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
| Large 4 image generation, reference image and HA retrieval | Passed |
| Files API download/deletion with a private synthetic PNG | Passed |
| Files API deletion of URL-only images | Unavailable: no file ID |

Temporary entities and the Realtime pipeline were removed. Installed runtime
files matched the candidate. No Mistral warning/error or blocking-call lines
appeared in the observed logs. Physical microphone and speaker behavior was
not tested.

Live image requests returned signed URLs, also observed with SDK 2.10.1 and
direct HTTP. After installing the fix and restarting HA, text-only and reference
requests both produced valid native HA media. The original Files API path was
independently verified with an owned private synthetic PNG: download, deletion
and failed retrieval after deletion all passed. URL-only images provide no file
ID for deletion. Original configuration and pipelines were preserved, temporary
entities removed, and installed files verified against the candidate.
An additional image/reference check was rate-limited by the provider (HTTP 429);
the earlier successful reference test used identical runtime code.

## Publication gate

The release workflow attaches `mistral_conversation.zip`, built from the tagged
integration files, when a release is published. CI checks archive integrity.
Existing assets are not overwritten, preserving their download counts.

Before a feature or SDK release:

1. Pass the complete local checks and public HACS/Hassfest CI.
2. Back up a real instance, verify installed files, validate configuration and
   perform a full restart.
3. Verify Conversation, local-intent follow-ups, a read-only tool round trip,
   structured AI Task, batch STT, Realtime cancellation/recovery and TTS retrieval.
4. For image generation, verify a text request, a reference image and HA media
   retrieval. For Files API results, confirm provider deletion and failed
   retrieval after deletion. For URL-only results, verify the approved storage
   download and record the absence of provider deletion support.
5. Confirm preserved configuration, removed test entities and clean observed
   integration logs. Record untested paths and limitations.

Patch-only dependency maintenance may rely on the complete automated gate
without repeating live smoke tests, with that boundary stated in the release
notes. Publishing a release or tag requires explicit owner authorization.
