"""Bounded FFmpeg processing for generated speech audio."""

from __future__ import annotations

import asyncio
import json
import math
import re
import struct
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass

from .const import MAX_TTS_AUDIO_BYTES

LOUDNESS_TRUE_PEAK_DB = -2.0
MAX_LOUDNESS_GAIN_DB = 20.0
NORMALIZATION_TIMEOUT_SECONDS = 30.0
MAX_NORMALIZATION_JOBS = 2
MAX_FFMPEG_DIAGNOSTIC_BYTES = 64 * 1024
MIN_MEASURABLE_DURATION_SECONDS = 0.4
_PIPE_CHUNK_BYTES = 64 * 1024
_LOUDNORM_JSON = re.compile(rb"\{\s*\"input_i\".*?\}", re.DOTALL)
_WAVE_GUID_SUFFIX = bytes.fromhex("00001000800000aa00389b71")


class AudioProcessingError(Exception):
    """Raised when local audio processing fails."""


class AudioOutputTooLargeError(AudioProcessingError):
    """Raised when processed audio exceeds the configured output bound."""


@dataclass(frozen=True, slots=True)
class WavMetadata:
    """Audio properties read from an uncompressed WAV header."""

    sample_rate: int
    channels: int
    duration_seconds: float
    codec: str


@dataclass(frozen=True, slots=True)
class LoudnessMeasurement:
    """First-pass EBU R128 measurements emitted by FFmpeg loudnorm."""

    integrated: float
    loudness_range: float
    true_peak: float
    threshold: float
    offset: float


@dataclass(frozen=True, slots=True)
class AudioOutput:
    """Requested FFmpeg output container and codec."""

    container: str
    codec: str
    sample_rate: int


def parse_wav_metadata(data: bytes) -> WavMetadata:
    """Read sample metadata from a bounded RIFF/WAVE PCM file."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioProcessingError("speech response is not a RIFF/WAVE file")

    fmt: bytes | None = None
    data_size: int | None = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        if chunk_id == b"data" and chunk_size == 0xFFFFFFFF:
            chunk_size = len(data) - start
        end = start + chunk_size
        if end > len(data):
            raise AudioProcessingError("WAV chunk exceeds the speech response")
        if chunk_id == b"fmt " and fmt is None:
            fmt = data[start:end]
        elif chunk_id == b"data" and data_size is None:
            data_size = chunk_size
        offset = end + (chunk_size & 1)

    if fmt is None or len(fmt) < 16 or data_size is None:
        raise AudioProcessingError("WAV header is missing required chunks")

    format_tag, channels, sample_rate, byte_rate, _align, bits = struct.unpack_from(
        "<HHIIHH", fmt
    )
    if format_tag == 0xFFFE:
        if len(fmt) < 40:
            raise AudioProcessingError("WAV extensible format is incomplete")
        subformat = fmt[24:40]
        if subformat[4:] != _WAVE_GUID_SUFFIX:
            raise AudioProcessingError("WAV extensible subformat is unsupported")
        format_tag = struct.unpack_from("<I", subformat)[0]

    codecs = {
        (1, 8): "pcm_u8",
        (1, 16): "pcm_s16le",
        (1, 24): "pcm_s24le",
        (1, 32): "pcm_s32le",
        (3, 32): "pcm_f32le",
        (3, 64): "pcm_f64le",
    }
    codec = codecs.get((format_tag, bits))
    expected_align = channels * bits // 8
    if (
        codec is None
        or channels < 1
        or sample_rate < 1
        or bits % 8
        or expected_align < 1
        or _align != expected_align
        or byte_rate != sample_rate * expected_align
        or data_size % expected_align
    ):
        raise AudioProcessingError("WAV response is not valid uncompressed PCM")

    return WavMetadata(
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=data_size / byte_rate,
        codec=codec,
    )


def _parse_measurement(stderr: bytes) -> LoudnessMeasurement:
    """Parse the final bounded JSON measurement from loudnorm output."""
    matches = list(_LOUDNORM_JSON.finditer(stderr))
    if not matches:
        raise AudioProcessingError("FFmpeg did not return loudness measurements")
    try:
        payload = json.loads(matches[-1].group())
        measurement = LoudnessMeasurement(
            integrated=float(payload["input_i"]),
            loudness_range=float(payload["input_lra"]),
            true_peak=float(payload["input_tp"]),
            threshold=float(payload["input_thresh"]),
            offset=float(payload["target_offset"]),
        )
    except KeyError, TypeError, ValueError, json.JSONDecodeError:
        raise AudioProcessingError(
            "FFmpeg returned invalid loudness measurements"
        ) from None
    values = (
        measurement.integrated,
        measurement.loudness_range,
        measurement.true_peak,
        measurement.threshold,
        measurement.offset,
    )
    if any(math.isnan(value) for value in values) or any(
        value == math.inf
        for value in (
            measurement.integrated,
            measurement.loudness_range,
            measurement.true_peak,
            measurement.threshold,
        )
    ):
        raise AudioProcessingError("FFmpeg returned invalid loudness measurements")
    if not math.isfinite(measurement.loudness_range) or not math.isfinite(
        measurement.threshold
    ):
        raise AudioProcessingError("FFmpeg returned invalid loudness measurements")
    if math.isfinite(measurement.integrated):
        if not math.isfinite(measurement.true_peak) or not math.isfinite(
            measurement.offset
        ):
            raise AudioProcessingError("FFmpeg returned invalid loudness measurements")
    elif measurement.integrated != -math.inf:
        raise AudioProcessingError("FFmpeg returned invalid loudness measurements")
    return measurement


def _output_for_format(output_format: str, metadata: WavMetadata) -> AudioOutput:
    """Select a codec while retaining source properties where supported."""
    if output_format == "mp3":
        mp3_rates = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
        mp3_rate = (
            metadata.sample_rate
            if metadata.sample_rate in mp3_rates
            else min(mp3_rates, key=lambda rate: abs(rate - metadata.sample_rate))
        )
        return AudioOutput("mp3", "libmp3lame", mp3_rate)
    if output_format == "opus":
        opus_rate = (
            metadata.sample_rate
            if metadata.sample_rate in (8000, 12000, 16000, 24000, 48000)
            else 48000
        )
        return AudioOutput("opus", "libopus", opus_rate)
    if output_format == "flac":
        return AudioOutput("flac", "flac", metadata.sample_rate)
    if output_format == "wav":
        return AudioOutput("wav", metadata.codec, metadata.sample_rate)
    if output_format == "pcm":
        # Mistral documents its raw PCM response as little-endian float32.
        return AudioOutput("f32le", "pcm_f32le", metadata.sample_rate)
    raise AudioProcessingError("unsupported speech output format")


def _finite_measurement(measurement: LoudnessMeasurement) -> bool:
    """Return whether loudnorm supplied all values required by pass two."""
    return all(
        math.isfinite(value)
        for value in (
            measurement.integrated,
            measurement.loudness_range,
            measurement.true_peak,
            measurement.threshold,
            measurement.offset,
        )
    )


def _normalization_filter(
    measurement: LoudnessMeasurement,
    target_loudness: int,
    duration_seconds: float,
) -> str | None:
    """Build pass-two filtering with bounded gain and peak-safe fallback."""
    measurable = (
        duration_seconds >= MIN_MEASURABLE_DURATION_SECONDS
        and _finite_measurement(measurement)
    )
    if not measurable:
        if math.isfinite(measurement.true_peak) and (
            measurement.true_peak > LOUDNESS_TRUE_PEAK_DB
        ):
            attenuation = LOUDNESS_TRUE_PEAK_DB - measurement.true_peak
            return f"volume={attenuation:.2f}dB"
        return None

    requested_gain = target_loudness - measurement.integrated
    gain = min(requested_gain, MAX_LOUDNESS_GAIN_DB)
    effective_target = measurement.integrated + gain
    # A target LRA of 50 never narrows ordinary speech dynamics. The peak
    # ceiling can still switch loudnorm from constant gain to dynamic limiting.
    target_lra = 50.0
    linear = (
        measurement.loudness_range <= target_lra
        and measurement.true_peak + gain <= LOUDNESS_TRUE_PEAK_DB
    )
    offset = measurement.offset if requested_gain <= MAX_LOUDNESS_GAIN_DB else 0
    return (
        f"loudnorm=I={effective_target:.2f}:LRA={target_lra:.2f}:"
        f"TP={LOUDNESS_TRUE_PEAK_DB:.2f}:"
        f"measured_I={measurement.integrated:.2f}:"
        f"measured_LRA={measurement.loudness_range:.2f}:"
        f"measured_TP={measurement.true_peak:.2f}:"
        f"measured_thresh={measurement.threshold:.2f}:"
        f"offset={offset:.2f}:"
        f"linear={'true' if linear else 'false'}:print_format=summary"
    )


async def _write_input(writer: asyncio.StreamWriter, source: bytes) -> None:
    """Feed a child process through a bounded pipe without closing waits."""
    try:
        for offset in range(0, len(source), _PIPE_CHUNK_BYTES):
            writer.write(source[offset : offset + _PIPE_CHUNK_BYTES])
            await writer.drain()
    except BrokenPipeError, ConnectionResetError:
        pass
    finally:
        writer.close()


async def _read_audio_output(reader: asyncio.StreamReader, maximum: int) -> bytes:
    """Drain stdout while enforcing the output bound."""
    result = bytearray()
    while chunk := await reader.read(_PIPE_CHUNK_BYTES):
        if len(result) + len(chunk) > maximum:
            raise AudioOutputTooLargeError(
                "processed speech exceeds the audio size limit"
            )
        result.extend(chunk)
    return bytes(result)


async def _read_diagnostics(reader: asyncio.StreamReader) -> bytes:
    """Drain stderr without retaining more than the diagnostic limit."""
    result = bytearray()
    while chunk := await reader.read(_PIPE_CHUNK_BYTES):
        remaining = MAX_FFMPEG_DIAGNOSTIC_BYTES - len(result)
        if remaining > 0:
            result.extend(chunk[:remaining])
    return bytes(result)


async def _discard_stream(reader: asyncio.StreamReader | None) -> None:
    """Drain remaining child output after a process is killed."""
    if reader is not None:
        while await reader.read(_PIPE_CHUNK_BYTES):
            pass


class AudioNormalizer:
    """Run at most two bounded FFmpeg normalization jobs for one account."""

    def __init__(self, ffmpeg_binary: str) -> None:
        """Initialize the processor with Home Assistant's FFmpeg binary."""
        self._ffmpeg_binary = ffmpeg_binary
        self._semaphore = asyncio.Semaphore(MAX_NORMALIZATION_JOBS)
        self._processes: set[asyncio.subprocess.Process] = set()
        self._jobs: set[asyncio.Task[object]] = set()
        self._state_lock = asyncio.Lock()
        self._closed = False

    async def async_normalize(
        self,
        source_wav: bytes,
        output_format: str,
        target_loudness: int,
    ) -> bytes:
        """Measure, normalize, and encode a generated WAV response."""
        if len(source_wav) > MAX_TTS_AUDIO_BYTES:
            raise AudioOutputTooLargeError("speech exceeds the audio size limit")
        task = asyncio.current_task()
        if task is None:
            raise AudioProcessingError("audio processing task is unavailable")
        self._jobs.add(task)
        try:
            try:
                async with asyncio.timeout(NORMALIZATION_TIMEOUT_SECONDS):
                    async with self._semaphore:
                        if self._closed:
                            raise AudioProcessingError("audio processor is closed")
                        metadata = parse_wav_metadata(source_wav)
                        measurement = await self._async_measure(
                            source_wav, target_loudness
                        )
                        audio_filter = _normalization_filter(
                            measurement, target_loudness, metadata.duration_seconds
                        )
                        return await self._async_encode(
                            source_wav, metadata, output_format, audio_filter
                        )
            except TimeoutError as err:
                raise AudioProcessingError("FFmpeg processing timed out") from err
        finally:
            self._jobs.discard(task)

    async def async_close(self) -> None:
        """Terminate and reap every subprocess owned by this processor."""
        self._closed = True
        current = asyncio.current_task()
        jobs = tuple(task for task in self._jobs if task is not current)
        for task in jobs:
            task.cancel()
        async with self._state_lock:
            processes = tuple(self._processes)
        for process in processes:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        if processes:
            await asyncio.gather(
                *(process.wait() for process in processes), return_exceptions=True
            )

    async def _async_measure(
        self, source_wav: bytes, target_loudness: int
    ) -> LoudnessMeasurement:
        """Run the first loudnorm pass."""
        _stdout, stderr = await self._async_run(
            (
                "-hide_banner",
                "-nostdin",
                "-i",
                "pipe:0",
                "-vn",
                "-af",
                (
                    f"loudnorm=I={target_loudness}:LRA=50:"
                    f"TP={LOUDNESS_TRUE_PEAK_DB:.2f}:print_format=json"
                ),
                "-f",
                "null",
                "-",
            ),
            source_wav,
            max_output_bytes=0,
        )
        return _parse_measurement(stderr)

    async def _async_encode(
        self,
        source_wav: bytes,
        metadata: WavMetadata,
        output_format: str,
        audio_filter: str | None,
    ) -> bytes:
        """Apply pass-two normalization and encode the requested format."""
        output = _output_for_format(output_format, metadata)
        args = [
            "-hide_banner",
            "-nostdin",
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            "-vn",
        ]
        if audio_filter is not None:
            args.extend(("-af", audio_filter))
        if output_format == "mp3":
            # VBR preserves speech level and transients better than the encoder's
            # default bitrate, especially after peak limiting.
            args.extend(("-q:a", "2"))
        args.extend(
            (
                "-ar",
                str(output.sample_rate),
                "-ac",
                str(metadata.channels),
                "-c:a",
                output.codec,
                "-f",
                output.container,
                "pipe:1",
            )
        )
        stdout, _stderr = await self._async_run(
            args, source_wav, max_output_bytes=MAX_TTS_AUDIO_BYTES
        )
        if not stdout:
            raise AudioProcessingError("FFmpeg returned no speech audio")
        return stdout

    async def _async_run(
        self,
        args: Sequence[str],
        source: bytes,
        *,
        max_output_bytes: int,
    ) -> tuple[bytes, bytes]:
        """Run FFmpeg with bounded asynchronous pipes and deterministic cleanup."""
        async with self._state_lock:
            if self._closed:
                raise AudioProcessingError("audio processor is closed")
            try:
                process = await asyncio.create_subprocess_exec(
                    self._ffmpeg_binary,
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as err:
                raise AudioProcessingError("FFmpeg could not be started") from err
            self._processes.add(process)

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        tasks = (
            asyncio.create_task(_write_input(process.stdin, source)),
            asyncio.create_task(_read_audio_output(process.stdout, max_output_bytes)),
            asyncio.create_task(_read_diagnostics(process.stderr)),
        )
        try:
            _written, stdout, stderr = await asyncio.gather(*tasks)
            return_code = await process.wait()
            if return_code:
                raise AudioProcessingError(f"FFmpeg exited with status {return_code}")
            return stdout, stderr
        except BaseException:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.gather(
                _discard_stream(process.stdout),
                _discard_stream(process.stderr),
                process.wait(),
                return_exceptions=True,
            )
            raise
        finally:
            self._processes.discard(process)
