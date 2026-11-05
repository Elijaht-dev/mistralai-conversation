"""Verify transitive transport availability and the real native JPEG backend."""

from importlib.metadata import requires, version

import numpy as np
from packaging.requirements import Requirement
from turbojpeg import TurboJPEG


def test_sdk_supplies_httpx2_on_older_home_assistant() -> None:
    """The SDK must install the transport even when HA does not provide it."""
    dependencies = [Requirement(value) for value in requires("mistralai") or []]
    transports = [
        requirement
        for requirement in dependencies
        if requirement.name == "httpx2"
        and (requirement.marker is None or requirement.marker.evaluate())
    ]
    assert transports
    assert all(version("httpx2") in requirement.specifier for requirement in transports)


def test_jpeg_codec_encodes_and_decodes_synthetic_pixels() -> None:
    """PyTurboJPEG 2 must work with the actual native library, beyond importing."""
    jpeg = TurboJPEG()
    pixels = np.full((16, 16, 3), (24, 80, 160), dtype=np.uint8)

    encoded = jpeg.encode(pixels, quality=95)
    decoded = jpeg.decode(encoded)

    assert encoded.startswith(b"\xff\xd8")
    assert jpeg.decode_header(encoded)[:2] == (16, 16)
    assert decoded.shape == pixels.shape
    assert np.max(np.abs(decoded.astype(np.int16) - pixels)) <= 3
