import asyncio
import os
import signal
from dataclasses import dataclass
from typing import Protocol


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class Result:
    code: int
    output: str
    error: str
    truncated: bool = False


class Transport(Protocol):
    async def run(self, args: tuple[str, ...], limit: int = 2_000_000) -> Result: ...


async def _read(stream, limit):
    data = bytearray()
    truncated = False
    while chunk := await stream.read(8192):
        remaining = limit - len(data)
        data.extend(chunk[:remaining])
        truncated |= len(chunk) > remaining
    return data.decode("utf-8", errors="replace"), truncated


async def execute(args, timeout, limit):
    group = {"start_new_session": True} if os.name == "posix" else {}
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in {"BOT_TOKEN", "LOG_REDACT_VALUES"}
    }
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            **group,
        )
    except OSError:
        raise TransportError("Could not start the command") from None

    try:
        async with asyncio.timeout(timeout):
            # Drain both streams concurrently even when the retained output is full.
            async with asyncio.TaskGroup() as tasks:
                stdout = tasks.create_task(_read(process.stdout, limit))
                stderr = tasks.create_task(_read(process.stderr, 8192))
                tasks.create_task(process.wait())
        output, truncated = stdout.result()
        error, _ = stderr.result()
        return Result(process.returncode, output, error, truncated)
    except (TimeoutError, asyncio.CancelledError) as failure:
        try:
            if group:
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        await asyncio.gather(process.wait(), _read(process.stdout, 0), _read(process.stderr, 0))
        if isinstance(failure, TimeoutError):
            raise TransportError("Command timed out") from None
        raise
