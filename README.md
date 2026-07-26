# Mistral AI Conversation for Home Assistant

[![Validate](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml/badge.svg)](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml)
[![Home Assistant 2026.7.4+](https://img.shields.io/badge/Home%20Assistant-2026.7.4%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Mistral SDK 2.7.2](https://img.shields.io/badge/mistralai-2.7.2-FA520F.svg)](https://pypi.org/project/mistralai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Elijaht-dev&repository=mistralai-conversation&category=integration)

A custom Home Assistant AI integration for Mistral AI, implemented against the
official `mistralai` Python SDK. It provides Conversation, AI Task,
speech-to-text, and text-to-speech entities using Home Assistant's current
entity, config-subentry, coordinator, repair, and diagnostics patterns.

This is an independent custom integration. It is not developed, reviewed, or
endorsed by Home Assistant or Mistral AI.

## Features

- UI configuration, API-key validation, and reauthentication
- Multiple independently configured conversation agents per Mistral account
- Home Assistant AI Task data generation with native Mistral JSON-schema output
- AI Task image and PDF attachments on compatible multimodal models
- Voxtral speech-to-text for Home Assistant Assist voice pipelines
- Streaming Voxtral text-to-speech with preset and saved custom Mistral voices
- Live model discovery, aliases, capability metadata, and custom model IDs
- Streamed text, reasoning, token usage, and parallel tool calls
- Multi-turn history with provider-native signed reasoning replay
- Home Assistant Assist tools for reading and controlling exposed entities
- Bounded tool execution: 128 declared tools and 10 tool rounds per request
- PNG, JPEG, GIF, WebP, and PDF attachments with local type, count, and size
  checks
- Model-aware validation for tools, reasoning, vision, documents, and context size
- Configurable instructions, model, output limit, temperature, reasoning effort,
  Mistral safety prompt, and exposed Home Assistant APIs
- Coordinator-backed availability, translated runtime errors, diagnostics, and
  deprecated-model repairs
- English and French user-interface translations

## Requirements

- Home Assistant 2026.7.4 or newer
- A Mistral AI API key and available API credit
- A chat-capable Mistral model for Conversation and AI Task
- Access to Mistral's audio endpoints for speech-to-text or text-to-speech

Tool control, reasoning, image input, and PDF input also require the corresponding
capability on the selected model. Unknown custom and fine-tuned model IDs remain
configurable because their capabilities may not be present in model discovery.
Text-to-speech additionally requires a preset or saved voice available to the
Mistral account.

The integration pins `mistralai==2.7.2`.

## Installation

### HACS

This integration is available as a HACS custom repository. It is not currently
part of the default HACS catalog.

Use the **Open your Home Assistant instance** button above, or add the repository
manually:

1. Open **HACS → Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/Elijaht-dev/mistralai-conversation` with category
   **Integration**.
4. Select **Mistral AI Conversation**, choose **Download**, and restart Home
   Assistant.

### Manual installation

1. Download or clone this repository.
2. Copy `custom_components/mistral_conversation` into Home Assistant's
   `config/custom_components` directory.
3. Confirm the resulting path is
   `config/custom_components/mistral_conversation/manifest.json`.
4. Restart Home Assistant.
5. Clear the browser cache if the integration does not appear immediately.

## Configuration

1. Create an API key in the
   [Mistral AI console](https://console.mistral.ai/api-keys/).
2. Open **Settings → Devices & services → Add integration**.
3. Search for **Mistral AI Conversation** and enter the API key.
4. Reconfigure the default Conversation, AI Task, or speech-to-text entity, or
   add more entities from the integration page.
5. Add a text-to-speech entity and explicitly select a preset or saved Mistral
   voice.
6. In **Settings → Voice assistants**, select the Mistral conversation,
   speech-to-text, and text-to-speech entities for the desired pipeline.

### Conversation

| Option | Purpose |
| --- | --- |
| Name | Label shown for this agent |
| Model | Discovered, custom, or fine-tuned Mistral model ID |
| Instructions | Templated system instructions |
| Home Assistant APIs | Tool APIs exposed to Mistral; clear for chat-only use |
| Maximum response tokens | Upper bound for generated output |
| Temperature | Predictability-to-variation control from 0 to 1 |
| Reasoning effort | None, minimal, low, medium, high, or extra high |
| Mistral safety prompt | Requests Mistral's provider-side safety prompt |

The default model is `mistral-small-latest`, with the built-in Assist API enabled.

### AI Task

The default AI Task entity uses the same chat-model controls as Conversation,
without a conversation prompt or a fixed Home Assistant tool selection. It
supports unstructured text and schema-constrained data through Mistral's native
JSON-schema response format. Compatible models can also receive Home Assistant
image and PDF attachments. Mistral image generation is not currently exposed.

### Speech-to-text

Speech-to-text defaults to `voxtral-mini-latest`. It accepts the conservative
Assist format of mono, 16-bit, 16 kHz PCM audio, adds the WAV container expected
by the batch transcription API, and forwards the pipeline language to Mistral.
The input is bounded locally before upload.

### Text-to-speech

Text-to-speech defaults to `voxtral-mini-tts-2603` and supports MP3, Opus, FLAC,
WAV, and raw PCM output. The setup flow reads preset and saved custom voices
available to the account while still allowing a custom voice ID.

Mistral requires an explicit `voice_id`, so the integration does not silently
create, clone, or select a voice. Only use a cloned voice with the speaker's
informed consent. Voice creation and retention are managed by Mistral, not by
this integration.

## Data, control, and security

Prompts, AI Task instructions, relevant conversation history, exposed tool
definitions, tool results, attachments, speech recordings, and text submitted
for speech generation are sent to Mistral AI. Generated speech may enter Home
Assistant's TTS cache. API use may incur charges. API keys are stored in Home
Assistant's config-entry storage and are redacted from diagnostics. The
integration does not log prompt, attachment, recording, transcript, or generated
audio content in its request trace.

When a Home Assistant API is selected, the model can call its exposed tools.
Review the entities exposed to the voice assistant before enabling control,
especially locks, covers, alarms, garage doors, and other safety-sensitive
devices. Home Assistant remains responsible for tool validation and execution.

Attachments are encoded as data URLs. Each file is limited to 20 MB and each
request to 10 files. The configured model must support the input type.

Speech-to-text input and decoded text-to-speech output are each limited to 25 MB
per request. Text-to-speech input is limited to 5,000 characters. These local
bounds protect Home Assistant memory independently of provider-side limits.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and deployment
guidance.

## Failure behavior

- Authentication errors start Home Assistant's reauthentication flow.
- Timeouts, connection failures, rate limits, and provider errors are translated
  into distinct Home Assistant errors.
- Connection-level failures mark the entity unavailable until model discovery
  succeeds again; request validation and rate-limit errors do not.
- Invalid or incomplete streamed tool calls are rejected before execution.
- Empty responses, explicit stream errors, excessive tools, oversized
  attachments, oversized audio, malformed speech data, and runaway tool loops
  fail closed.
- Model deprecation metadata creates a Home Assistant repair issue when Mistral
  provides a replacement.

## Troubleshooting

- **Invalid API key:** complete the reauthentication notification.
- **Model rejects tools:** select a function-calling model or clear **Home
  Assistant APIs**.
- **Model rejects reasoning:** set **Reasoning effort** to **None**.
- **Attachment rejected:** select a vision/document-capable model and use a
  supported image or PDF.
- **Speech-to-text unavailable:** confirm the account can use Voxtral
  transcription and that the Assist pipeline uses the advertised PCM format.
- **No text-to-speech entity:** add one from the integration page and select a
  preset or saved voice.
- **Voice missing from the list:** enter its saved Mistral voice ID manually, or
  confirm that the API key can list voices.
- **No entities can be controlled:** confirm that the voice assistant exposes
  them and that an appropriate Home Assistant API is selected.

Enable debug logging only while diagnosing a problem:

```yaml
logger:
  logs:
    custom_components.mistral_conversation: debug
```

SDK debug logging can expose additional request metadata and should be enabled
with care.

## Quality and development

The repository validates on Python 3.14 with Ruff, strict mypy, Home
Assistant-native pytest fixtures, branch coverage, HACS structural readiness, and
Hassfest. The official HACS remote validator also runs on every public push and
pull request. Provider calls are mocked in tests; no live API key is required.

```bash
python -m pip install --requirement requirements_test.txt
ruff format --check .
ruff check .
mypy
pytest --cov=custom_components.mistral_conversation --cov-report=term-missing
```

See [QUALITY.md](QUALITY.md) for the design comparison and assurance boundaries,
and [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

## License

MIT
