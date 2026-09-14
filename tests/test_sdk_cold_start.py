"""Regression coverage for SDK imports hidden by a warm pytest interpreter."""

import asyncio
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("endpoint", ["models", "chat", "voices", "stt", "tts"])
@pytest.mark.parametrize("status", [200, 422])
async def test_first_sdk_request_does_not_block(endpoint: str, status: int) -> None:
    """A fresh SDK handles success/error responses with HA detection enabled."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("sdk_cold_start.py")),
        endpoint,
        str(status),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert process.returncode == 0, (stdout + stderr).decode()
