# Mistral AI Conversation for Home Assistant

[![Validate](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml/badge.svg)](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml)
[![Home Assistant 2026.7.4+](https://img.shields.io/badge/Home%20Assistant-2026.7.4%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Mistral SDK 3.7.0](https://img.shields.io/badge/mistralai-3.7.0-FA520F.svg)](https://pypi.org/project/mistralai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Elijaht-dev&repository=mistralai-conversation&category=integration)

A custom Home Assistant integration for Mistral AI using the official Python
SDK. It provides Conversation, AI Task, speech-to-text and text-to-speech
entities, with English and French configuration interfaces.

This is an independent project, not developed, reviewed or endorsed by Home
Assistant or Mistral AI. This README describes the development branch;
[CHANGELOG.md](CHANGELOG.md) lists released and unreleased changes.

## Requirements

- Home Assistant 2026.7.4 or newer.
- A Mistral AI API key with credit and access to the selected models/endpoints.
- An available preset or saved voice for text-to-speech.
- FFmpeg through Home Assistant for speech loudness normalization.

The integration installs its pinned Mistral SDK dependencies automatically.
Current versions and compatibility details are in [QUALITY.md](QUALITY.md).

## Installation

### HACS

Find **Mistral AI Conversation** in HACS, download it and restart Home Assistant.
The HACS button above opens the repository in your instance.

### Manual installation

1. Copy `custom_components/mistral_conversation` into your Home Assistant
   `config/custom_components` directory.
2. Confirm that `config/custom_components/mistral_conversation/manifest.json`
   exists.
3. Restart Home Assistant.

## Configuration

1. Create an API key in the [Mistral AI console](https://console.mistral.ai/api-keys/).
2. Open **Settings → Devices & services → Add integration** and select
   **Mistral AI Conversation**.
3. Enter the key. The account creates Conversation, AI Task and STT entities.
4. Reconfigure these entities or add more from the integration page.
5. Add a TTS entity and explicitly select a preset or saved voice.
6. Select the desired entities in **Settings → Voice assistants**.

Each entity has its own settings. Upgrades preserve existing model selections,
voices, audio options and pipelines.

### Models

Model menus offer a curated list and accept custom or fine-tuned IDs. Saved
values remain visible even when absent from the list. Discovery supplies
capability checks, aliases and model-retirement repairs.

| New entity or setting | Default model |
| --- | --- |
| Conversation | `mistral-small-latest` |
| AI Task data | `mistral-small-latest` |
| AI Task image tool | `mistral-large-4` |
| STT | `voxtral-mini-transcribe-realtime-3-0` |
| TTS | `voxtral-mini-tts-3-0` |

Conversation and AI Task data offer Small 4, Large 4 and Ministral 3 in 3B, 8B
and 14B sizes. The image-tool menu offers Large 4 and Small 4. The STT menu
offers Realtime 3, Mini Transcribe 3 and Small Transcribe 3.

Known incompatible capabilities are rejected locally. Unknown custom models
remain configurable; Mistral determines whether their requests are supported.

### Conversation

| Option | Purpose |
| --- | --- |
| Instructions | Templated system instructions |
| Home Assistant APIs | Tools exposed to the model; clear for chat-only use |
| Maximum response tokens | Output limit |
| Temperature | Variation control from 0 to 1 |
| Reasoning | Automatic, Disabled, Enabled, or a fixed model state |
| Mistral safety prompt | Enable Mistral's provider-side safety prompt |

The built-in Assist API is enabled by default. Home Assistant controls which
entities are exposed and validates tool execution.

Reasoning controls depend on the selected model. Fixed states appear during
reconfiguration. Model changes save in one submission; reopen the form to see
the updated controls. Unsupported saved efforts must be corrected before use;
custom SDK efforts remain editable where supported.

With **Prefer handling commands locally** enabled, locally handled commands
and spoken replies remain available for later conversation turns.

### AI Task

AI Task supports plain text and structured data using native JSON-schema
output. Compatible models also accept image and PDF attachments.

The same entity provides `ai_task.generate_image`. Its independent image-tool
model interprets instructions and reference images; Mistral manages the
underlying image generator.

```yaml
action: ai_task.generate_image
data:
  entity_id: ai_task.mistral_ai_task
  task_name: landscape
  instructions: "Generate an illustration of a mountain lake at sunrise."
response_variable: generated_image
```

Home Assistant needs a media directory to store generated images. The action
returns its standard media result, including `media_source_id` and a signed
`url`. Image references provide visual context; exact image editing is not
guaranteed. PDF references are not supported for image generation.

A response without an image is an error and is not automatically retried.
The image action currently requires a Mistral file ID; URL-only responses are
not yet supported. See the [recorded validation](QUALITY.md#recorded-validation).

### Speech-to-text

STT accepts mono, 16-bit, 16 kHz PCM from Assist.

| Transcription mode | Behavior |
| --- | --- |
| Automatic | Use discovered capabilities, then documented model IDs; unknown IDs use Batch |
| Batch | Buffer one recording, add a WAV header and send the pipeline language |
| Realtime | Send audio during capture through the WebSocket endpoint |

Mini Transcribe 3 and Small Transcribe 3 use the file transcription endpoint.
This Batch mode processes one Assist recording per request.

Realtime uses automatic language detection. Assist determines when speech ends,
and the integration waits for the final transcript before starting the
conversation agent. Turn-detection events do not discard trailing words.
A failed Realtime request is not retried through Batch.

Manual mode selection supports custom models and still respects known
capability restrictions.

### Text-to-speech

TTS supports MP3, Opus, FLAC, WAV and raw PCM. Select a preset or saved custom
voice, or enter its ID.

New entities enable **Normalize speech loudness** at **-16 LUFS**. The target
accepts whole numbers from -24 to -12 LUFS. Existing entities retain their
normalization settings.

Options can be overridden for one call:

```yaml
action: tts.speak
target:
  entity_id: tts.mistral_text_to_speech
data:
  media_player_entity_id: media_player.living_room
  message: "Hello"
  options:
    normalize_audio: true
    target_loudness: -18
```

Use `normalize_audio: false` to request the original provider audio.
Normalization errors return a translated error. Processing limits are listed
in [QUALITY.md](QUALITY.md#limits).

## Data and control

Prompts, relevant history, exposed tool schemas/results, attachments, recordings
and text for speech generation are sent to Mistral AI. API use may incur charges.
Home Assistant stores API keys and redacts them from integration diagnostics.
Request traces exclude prompts, attachments, recordings, transcripts and audio.

Home Assistant can retain generated images and cached speech. The integration
attempts to delete generated Mistral image files whose IDs it receives;
this does not guarantee zero provider retention or delete HA's local media.

Voice creation and retention are managed by Mistral. Use a cloned voice only
with the speaker's informed consent.

Review the entities exposed to Assist, particularly safety-sensitive devices.
See [SECURITY.md](SECURITY.md) for deployment and vulnerability-reporting guidance.

| Input or output | Local limit |
| --- | --- |
| PNG, JPEG, GIF, WebP and PDF attachments | 10 files, 20 MiB per file |
| Generated image | 20 MiB |
| STT input / TTS output | 25 MiB per request |
| TTS text | 5,000 characters |

## Troubleshooting

| Problem | Check |
| --- | --- |
| Invalid API key | Complete Home Assistant's reauthentication flow |
| Model rejects a request | Check model access, endpoint and capabilities |
| Tools cannot control entities | Review exposed entities and the selected Home Assistant API |
| Attachment rejected | Check MIME type, size and model vision/document support |
| STT unavailable | Check endpoint access, transcription mode and PCM format |
| Voice missing | Confirm account access or enter the saved voice ID |
| Normalization fails | Confirm FFmpeg is available, or disable normalization |
| Image request returns no file | Review the image model and current validation status in QUALITY |

Authentication, connection, rate-limit, timeout and provider failures have
distinct handling. Connection failures mark entities unavailable until model
discovery recovers. Malformed responses and exceeded limits fail closed.

For temporary integration debug logging:

```yaml
logger:
  logs:
    custom_components.mistral_conversation: debug
```

Avoid verbose SDK logging in normal operation and redact logs before sharing.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks,
[QUALITY.md](QUALITY.md) for architecture and validation, and
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

[MIT](LICENSE)
