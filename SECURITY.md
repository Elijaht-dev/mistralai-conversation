# Security policy

## Supported versions

Security fixes target the latest released version and the current `main` branch.

## Reporting a vulnerability

Use this repository's private GitHub vulnerability reporting when available.
Otherwise, contact the maintainer privately before disclosure. Do not publish
exploits, credentials or personal data in a public issue.

Include affected versions, reproduction conditions, impact and a minimal proof
of concept. Reports are acknowledged as soon as practical.

## Deployment

- Expose only the entities and Home Assistant APIs the assistant needs.
- Use a dedicated Mistral API key and rotate it after suspected disclosure.
- Treat prompts, history, tool results, attachments and speech as cloud-bound data.
- Review retention of generated images and cached audio in Home Assistant.
- Keep Home Assistant and the integration updated.
- Avoid verbose SDK logging in normal operation and redact shared diagnostics.
- Use cloned voices only with the speaker's informed consent.
- Add safeguards around safety-sensitive devices and high-impact automations.

Home Assistant's tool schemas, exposure controls, permissions and automation
design are part of the security boundary. Model-generated actions remain
probabilistic. See [README.md](README.md#data-and-control) for data handling and
[QUALITY.md](QUALITY.md) for runtime limits.
