import asyncio
import logging
import sys

import uvicorn

from app import db, jobs
from app.api.routes import create_app
from app.config import config

log = logging.getLogger("app")


async def main() -> None:
    db.setup()
    try:
        await db.init_db()
    except db.LegacyDatabaseError as e:
        sys.exit(str(e))
    if not config.owner_code:
        log.warning("OWNER_CODE не задан — главного админа назначить не получится. См. .env.example")
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host=config.host, port=config.port, log_level="info",
                       proxy_headers=True, forwarded_allow_ips="*")
    )
    background = asyncio.create_task(jobs.run())
    try:
        await server.serve()  # uvicorn сам ловит SIGTERM/SIGINT
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
