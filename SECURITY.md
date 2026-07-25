# Security policy

## Supported versions

Security fixes are applied to the latest released version and the current
`main` branch.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository when available.
If that feature is unavailable, contact the repository owner privately before
disclosing details. Do not open a public issue containing an API key, exploit,
prompt transcript, entity inventory, attachment, or personal data.

Please include affected versions, reproduction conditions, impact, and a minimal
proof of concept. Reports will be acknowledged as soon as practical.

## Deployment guidance

- Expose only the entities and Home Assistant APIs the assistant needs.
- Use a dedicated Mistral API key and rotate it after suspected disclosure.
- Treat conversation history, tool results, and attachments as data sent to a
  third-party cloud service.
- Keep Home Assistant, this integration, and the Mistral SDK updated.
- Do not enable verbose SDK logging in normal operation.
- Add confirmations or other safeguards around high-impact automations and
  safety-sensitive devices.

This project cannot guarantee the behavior of a probabilistic model. Home
Assistant's tool schemas, exposure controls, permissions, and automation design
remain part of the security boundary.
