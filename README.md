# Mistral AI Conversation for Home Assistant

[![Validate](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml/badge.svg)](https://github.com/Elijaht-dev/mistralai-conversation/actions/workflows/validate.yml)
[![Home Assistant 2026.7.2+](https://img.shields.io/badge/Home%20Assistant-2026.7.2%2B-41BDF5.svg)](https://www.home-assistant.io/)
[![Mistral SDK 2.7.0](https://img.shields.io/badge/mistralai-2.7.0-FA520F.svg)](https://pypi.org/project/mistralai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom Home Assistant conversation integration for Mistral AI, implemented
against the official `mistralai` Python SDK. Its architecture follows Home
Assistant's current `ConversationEntity`, `ChatLog`, config-subentry, coordinator,
repair, and diagnostics patterns.

This is an independent custom integration. It is not developed, reviewed, or
endorsed by Home Assistant or Mistral AI.

## Private preview

This repository is private during its first validation stage. HACS cannot install
from private GitHub repositories, so private-preview installations must use the
manual method below. Once the repository is intentionally made public, it can be
added to HACS as a custom repository.

No public release or repository visibility change is part of the private preview.

## Features

- UI configuration, API-key validation, and reauthentication
- Multiple independently configured conversation agents per Mistral account
- Live model discovery, aliases, capability metadata, and custom model IDs
- Streamed text, reasoning, token usage, and parallel tool calls
- Multi-turn history with provider-native signed reasoning replay
- Home Assistant Assist tools for reading and controlling exposed entities
- Bounded tool execution: 128 declared tools and 10 tool rounds per request
- PNG, JPEG, GIF, WebP, and PDF attachments with local type, count, and size checks
- Model-aware validation for tools, reasoning, vision, documents, and context size
- Configurable instructions, model, output limit, temperature, reasoning effort,
  Mistral safety prompt, and exposed Home Assistant APIs
- Coordinator-backed availability, translated runtime errors, diagnostics, and
  deprecated-model repairs
- English and French user-interface translations

## Requirements

- Home Assistant 2026.7.2 or newer
- A Mistral AI API key and available API credit
- A chat-capable Mistral model

Tool control, reasoning, image input, and PDF input also require the corresponding
capability on the selected model. Unknown custom and fine-tuned model IDs remain
configurable because their capabilities may not be present in model discovery.

The integration pins `mistralai==2.7.0`.

## Installation

### Private preview: manual installation

1. Download or clone this repository using a GitHub account that has access.
2. Copy `custom_components/mistral_conversation` into Home Assistant's
   `config/custom_components` directory.
3. Confirm the resulting path is
   `config/custom_components/mistral_conversation/manifest.json`.
4. Restart Home Assistant.
5. Clear the browser cache if the integration does not appear immediately.

### HACS after the repository is public

1. Open **HACS → Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/Elijaht-dev/mistralai-conversation` with category
   **Integration**.
4. Install **Mistral AI Conversation** and restart Home Assistant.

## Configuration

1. Create an API key in the
   [Mistral AI console](https://console.mistral.ai/api-keys/).
2. Open **Settings → Devices & services → Add integration**.
3. Search for **Mistral AI Conversation** and enter the API key.
4. Reconfigure the default agent or add more conversation agents from the
   integration page.
5. In **Settings → Voice assistants**, choose the new conversation entity as the
   assistant's conversation agent.

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

## Data, control, and security

Prompts, relevant conversation history, exposed tool definitions, tool results,
and attachments are sent to Mistral AI. API use may incur charges. API keys are
stored in Home Assistant's config-entry storage and are redacted from diagnostics.
The integration does not log prompt or attachment content in its request trace.

When a Home Assistant API is selected, the model can call its exposed tools.
Review the entities exposed to the voice assistant before enabling control,
especially locks, covers, alarms, garage doors, and other safety-sensitive
devices. Home Assistant remains responsible for tool validation and execution.

Attachments are encoded as data URLs. Each file is limited to 20 MB and each
request to 10 files. The configured model must support the input type.

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
  attachments, and runaway tool loops fail closed.
- Model deprecation metadata creates a Home Assistant repair issue when Mistral
  provides a replacement.

## Troubleshooting

- **Invalid API key:** complete the reauthentication notification.
- **Model rejects tools:** select a function-calling model or clear **Home
  Assistant APIs**.
- **Model rejects reasoning:** set **Reasoning effort** to **None**.
- **Attachment rejected:** select a vision/document-capable model and use a
  supported image or PDF.
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
Assistant-native pytest fixtures, branch coverage, HACS validation, and Hassfest.
Provider calls are mocked in tests; no live API key is required.

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
