"""Tests for bounded local processing of generated speech audio."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import struct
import subprocess
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.mistral_conversation.audio import (
    AudioNormalizer,
    AudioOutputTooLargeError,
    AudioProcessingError,
    LoudnessMeasurement,
    _parse_measurement,
    parse_wav_metadata,
)

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is unavailable")


def _tone(*, amplitude: float = 0.1, seconds: float = 2.0, rate: int = 24000) -> bytes:
    """Create a synthetic mono PCM16 signal without external media files."""
    samples = (
        round(32767 * amplitude * math.sin(2 * math.pi * 440 * index / rate))
        for index in range(round(seconds * rate))
    )
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack(f"<{round(seconds * rate)}h", *samples))
    return stream.getvalue()


def _float_wav(*, seconds: float = 1.0, rate: int = 24000) -> bytes:
    """Build a provider-like IEEE float32 WAV header and samples."""
    count = round(seconds * rate)
    samples = struct.pack(
        f"<{count}f",
        *(0.1 * math.sin(2 * math.pi * 440 * index / rate) for index in range(count)),
    )
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    return (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(samples))
        + (
            b"WAVEfmt "
            + struct.pack("<I", len(fmt))
            + fmt
            + b"data"
            + struct.pack("<I", len(samples))
            + samples
        )
    )


def _pcm_peak(wav_data: bytes) -> float:
    """Read peak amplitude of test PCM16 WAV output."""
    with wave.open(BytesIO(wav_data), "rb") as input_audio:
        assert input_audio.getsampwidth() == 2
        samples = struct.iter_unpack(
            "<h", input_audio.readframes(input_audio.getnframes())
        )
    return max(abs(sample) for (sample,) in samples) / 32768


def _high_crest_speech() -> bytes:
    """Create syllable-like tone bursts with peaks that require dynamic limiting."""
    rate = 24000
    count = 4 * rate
    samples = []
    for index in range(count):
        syllable = index % (rate // 2)
        envelope = 0.10 if syllable < rate // 3 else 0.015
        if rate // 10 <= syllable < rate // 10 + rate // 100:
            envelope = 0.60
        samples.append(
            round(32767 * envelope * math.sin(2 * math.pi * 440 * index / rate))
        )
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack(f"<{count}h", *samples))
    return stream.getvalue()


def _measure_output(
    audio_data: bytes, *, raw_float: bool = False
) -> LoudnessMeasurement:
    """Measure decoded loudness and true peak with an independent FFmpeg pass."""
    assert FFMPEG is not None
    input_args = ["-f", "f32le", "-ar", "24000", "-ac", "1"] if raw_float else []
    result = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            *input_args,
            "-i",
            "pipe:0",
            "-af",
            "loudnorm=I=-18:LRA=50:TP=-2:print_format=json",
            "-f",
            "null",
            "-",
        ],
        input=audio_data,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return _parse_measurement(result.stderr)


@requires_ffmpeg
@pytest.mark.parametrize("target_loudness", [-24, -18, -12])
async def test_normalizes_measurable_signal_to_requested_loudness(
    target_loudness: int,
) -> None:
    """A normal speech length signal reaches the requested integrated level."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(_tone(), "wav", target_loudness)
    finally:
        await normalizer.async_close()
    assert parse_wav_metadata(result).sample_rate == 24000
    assert _measure_output(result).integrated == pytest.approx(target_loudness, abs=1)
    assert _pcm_peak(result) <= 10 ** (-2 / 20) + 0.01


@requires_ffmpeg
async def test_gain_is_bounded_for_very_quiet_signal() -> None:
    """Normalization never amplifies weak input by more than 20 dB."""
    assert FFMPEG is not None
    source = _tone(amplitude=0.001)
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(source, "wav", -18)
    finally:
        await normalizer.async_close()
    assert _pcm_peak(result) <= _pcm_peak(source) * 10 + 2 / 32768
    assert _pcm_peak(result) > _pcm_peak(source) * 2


@requires_ffmpeg
async def test_peak_ceiling_limits_loud_signal() -> None:
    """The second pass protects the output peak when targeting louder speech."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(_tone(amplitude=0.95), "wav", -12)
    finally:
        await normalizer.async_close()
    assert _pcm_peak(result) <= 10 ** (-2 / 20) + 0.01


@requires_ffmpeg
async def test_high_crest_speech_reaches_target_with_peak_protection() -> None:
    """Peak limiting keeps bursty speech near target without clipping."""
    assert FFMPEG is not None
    source = _high_crest_speech()
    unprocessed_loudness = _measure_output(source).integrated
    assert unprocessed_loudness < -17
    assert 20 * math.log10(_pcm_peak(source)) + (-16 - unprocessed_loudness) > -2
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(source, "wav", -16)
    finally:
        await normalizer.async_close()
    assert _measure_output(result).integrated == pytest.approx(-16, abs=1)
    assert _pcm_peak(result) <= 10 ** (-2 / 20) + 0.01


@requires_ffmpeg
@pytest.mark.parametrize(
    "source", [_tone(seconds=0.1), _tone(amplitude=0.0)], ids=["short", "silence"]
)
async def test_short_or_silent_audio_returns_valid_output(source: bytes) -> None:
    """Unmeasurable audio is encoded without an invented loudness gain."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(source, "wav", -18)
    finally:
        await normalizer.async_close()
    assert parse_wav_metadata(result).duration_seconds == pytest.approx(
        parse_wav_metadata(source).duration_seconds, abs=0.01
    )
    assert _pcm_peak(result) == pytest.approx(_pcm_peak(source), abs=0.002)


@requires_ffmpeg
async def test_short_loud_audio_is_attenuated_below_peak_ceiling() -> None:
    """Unmeasurable loud audio remains protected from clipping."""
    assert FFMPEG is not None
    source = _tone(amplitude=0.95, seconds=0.1)
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(source, "wav", -16)
    finally:
        await normalizer.async_close()
    assert _pcm_peak(result) < _pcm_peak(source)
    assert _pcm_peak(result) <= 10 ** (-2 / 20) + 0.01


@requires_ffmpeg
async def test_float_wav_retains_sample_encoding() -> None:
    """Provider IEEE float samples remain float in the normalized WAV."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(_float_wav(), "wav", -18)
    finally:
        await normalizer.async_close()
    metadata = parse_wav_metadata(result)
    assert metadata.codec == "pcm_f32le"
    assert metadata.sample_rate == 24000
    assert _measure_output(result).integrated == pytest.approx(-18, abs=1)


@requires_ffmpeg
@pytest.mark.parametrize("output_format", ["mp3", "flac", "opus", "pcm"])
async def test_output_encoders(output_format: str) -> None:
    """Every requested format decodes to the target loudness."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(_tone(seconds=2), output_format, -18)
    finally:
        await normalizer.async_close()
    assert result
    assert _measure_output(result, raw_float=output_format == "pcm").integrated == (
        pytest.approx(-18, abs=1)
    )


@requires_ffmpeg
@pytest.mark.parametrize("output_format", ["mp3", "flac", "opus", "pcm"])
async def test_high_crest_encoded_audio_keeps_decoded_peak_below_zero(
    output_format: str,
) -> None:
    """Lossy encoding must not create a clipping peak after dynamic limiting."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    try:
        result = await normalizer.async_normalize(
            _high_crest_speech(), output_format, -16
        )
    finally:
        await normalizer.async_close()
    measurement = _measure_output(result, raw_float=output_format == "pcm")
    assert measurement.integrated == pytest.approx(-16, abs=1)
    assert measurement.true_peak < 0


@pytest.mark.parametrize(
    "stderr",
    [
        b"unrelated FFmpeg diagnostic",
        b'{"input_i": "-18"}',
        b'{"input_i": "NaN", "input_lra": "1", "input_tp": "-3", '
        b'"input_thresh": "-28", "target_offset": "0"}',
        b'{"input_i": "-18", "input_lra": "1", "input_tp": "Infinity", '
        b'"input_thresh": "-28", "target_offset": "0"}',
    ],
)
def test_rejects_missing_or_invalid_measurement(stderr: bytes) -> None:
    """Malformed FFmpeg measurements never become normalization parameters."""
    with pytest.raises(AudioProcessingError, match="loudness measurements"):
        _parse_measurement(stderr)


@pytest.mark.parametrize(
    "corruption",
    ["not_riff", "truncated_chunk", "missing_fmt", "bad_alignment", "unsupported"],
)
def test_rejects_invalid_wav_metadata(corruption: str) -> None:
    """Malformed provider WAV headers fail before launching FFmpeg."""
    source = bytearray(_tone(seconds=0.01))
    if corruption == "not_riff":
        source[:4] = b"NOPE"
    elif corruption == "truncated_chunk":
        struct.pack_into("<I", source, 16, len(source))
    elif corruption == "missing_fmt":
        source[12:16] = b"JUNK"
    elif corruption == "bad_alignment":
        struct.pack_into("<H", source, 32, 3)
    else:
        struct.pack_into("<H", source, 20, 6)
    with pytest.raises(AudioProcessingError):
        parse_wav_metadata(bytes(source))


async def test_missing_ffmpeg_is_a_sanitized_processing_error() -> None:
    """A missing binary does not leak an OS path or leave a process registered."""
    normalizer = AudioNormalizer("/no/such/ffmpeg")
    with pytest.raises(AudioProcessingError, match="FFmpeg could not be started"):
        await normalizer.async_normalize(_tone(), "wav", -18)
    assert not normalizer._processes
    await normalizer.async_close()


async def test_rejects_oversized_source_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source bound applies before parsing or starting FFmpeg."""
    monkeypatch.setattr(
        "custom_components.mistral_conversation.audio.MAX_TTS_AUDIO_BYTES", 4
    )
    normalizer = AudioNormalizer("/no/such/ffmpeg")
    with pytest.raises(AudioOutputTooLargeError):
        await normalizer.async_normalize(_tone(seconds=0.01), "wav", -18)
    assert not normalizer._processes


@requires_ffmpeg
async def test_rejects_oversized_encoded_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The output pipe stops when encoding expands beyond the byte bound."""
    assert FFMPEG is not None
    source = _tone(seconds=1.0)
    monkeypatch.setattr(
        "custom_components.mistral_conversation.audio.MAX_TTS_AUDIO_BYTES",
        len(source) + 100,
    )
    normalizer = AudioNormalizer(FFMPEG)
    with pytest.raises(AudioOutputTooLargeError):
        await normalizer.async_normalize(source, "pcm", -18)
    assert not normalizer._processes
    await normalizer.async_close()


@requires_ffmpeg
async def test_subprocess_error_is_reaped() -> None:
    """Invalid source data fails cleanly after FFmpeg exits."""
    assert FFMPEG is not None
    normalizer = AudioNormalizer(FFMPEG)
    with pytest.raises(AudioProcessingError):
        await normalizer._async_run(
            (
                "-hide_banner",
                "-nostdin",
                "-f",
                "invalid",
                "-i",
                "pipe:0",
                "-f",
                "null",
                "-",
            ),
            b"invalid",
            max_output_bytes=0,
        )
    assert not normalizer._processes
    await normalizer.async_close()


@pytest.mark.skipif(
    os.name == "nt", reason="test helper uses a POSIX executable script"
)
async def test_timeout_cancels_job_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unresponsive helper is killed and awaited at the deadline."""
    script = tmp_path / "stall.py"
    script.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n", encoding="utf-8"
    )
    script.chmod(0o755)

    monkeypatch.setattr(
        "custom_components.mistral_conversation.audio.NORMALIZATION_TIMEOUT_SECONDS",
        0.05,
    )
    normalizer = AudioNormalizer(str(script))
    with pytest.raises(AudioProcessingError, match="timed out"):
        await normalizer.async_normalize(_tone(), "wav", -18)
    assert not normalizer._processes
    await normalizer.async_close()


async def test_close_cancels_active_and_queued_jobs() -> None:
    """Close wakes queued jobs and prevents later work from launching."""
    normalizer = AudioNormalizer("unused")
    started = asyncio.Event()
    release = asyncio.Event()

    async def measure(_source: bytes, _target: int) -> object:
        started.set()
        await release.wait()
        raise AssertionError("job should have been cancelled")

    normalizer._async_measure = AsyncMock(side_effect=measure)  # type: ignore[method-assign]
    jobs = [
        asyncio.create_task(normalizer.async_normalize(_tone(), "wav", -18))
        for _ in range(3)
    ]
    await started.wait()
    await normalizer.async_close()
    assert all(job.cancelled() for job in jobs)
    with pytest.raises(AudioProcessingError, match="closed"):
        await normalizer.async_normalize(_tone(), "wav", -18)
