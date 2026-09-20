"""A local TLS server for the real Realtime transport cold-start regression."""

import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.asyncio.server import serve


def prepare_tls(api):
    """Build ephemeral certificates off the event loop without external requests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with TemporaryDirectory() as directory:
        cert_path, key_path = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        server_context.load_cert_chain(cert_path, key_path)
        api.get_default_context().load_verify_locations(cafile=cert_path)
    return serve, server_context


async def exercise_realtime(hass, api, client, status):
    """Exercise real TLS, WebSocket handshake, SDK serialization and parsing."""
    serve, server_context = await hass.async_add_executor_job(prepare_tls, api)
    messages = []

    async def handler(socket):
        await socket.send(
            json.dumps(
                {
                    "type": "session.created",
                    "session": {
                        "request_id": "test-request",
                        "model": "test-model",
                        "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
                    },
                }
            )
        )
        async for raw in socket:
            message = json.loads(raw)
            messages.append(message["type"])
            if message["type"] == "input_audio.end":
                event = (
                    {
                        "type": "transcription.done",
                        "text": "Hello",
                        "model": "test-model",
                        "language": "en",
                        "usage": {},
                    }
                    if status == 200
                    else {
                        "type": "error",
                        "error": {"code": 422, "message": "test error"},
                    }
                )
                await socket.send(json.dumps(event))

    async def audio():
        yield b"ab"

    async with serve(handler, "127.0.0.1", 0, ssl=server_context) as server:
        client.sdk_configuration.server_url = (
            f"https://localhost:{server.sockets[0].getsockname()[1]}"
        )
        try:
            assert (
                await api.async_transcribe_realtime(client, "test-model", audio())
                == "Hello"
            )
        except api.RealtimeError:
            assert status != 200
        else:
            assert status == 200
    assert messages == [
        "session.update",
        "input_audio.append",
        "input_audio.flush",
        "input_audio.end",
    ]
