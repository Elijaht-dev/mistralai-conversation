"""Exercise the native JPEG backend required by the pinned test environment."""

import numpy as np
from turbojpeg import TurboJPEG


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
