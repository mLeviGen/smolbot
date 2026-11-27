from smolbot.transports.base import execute


class LocalTransport:
    def __init__(self, timeout):
        self.timeout = timeout

    async def run(self, args, limit=2_000_000):
        return await execute(args, self.timeout, limit)
