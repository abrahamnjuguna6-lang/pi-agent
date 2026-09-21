"""Worker service entrypoint.

Placeholder until T10.1 adds leader election and the APScheduler sweepers; it validates
configuration and idles so `docker compose --profile app up` starts every service.
"""

import asyncio
import logging

from lifeos.config import get_settings

log = logging.getLogger("lifeos.worker")


async def run() -> None:
    settings = get_settings()
    log.info("worker started (environment=%s); sweepers arrive in T10.1", settings.environment)
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
