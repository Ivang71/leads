import asyncio, asyncpg
from . import config
from .storage.migrations import apply_migrations


async def _main() -> None:
	pool = await asyncpg.create_pool(dsn=config.DB_DSN, min_size=1, max_size=1)
	try:
		async with pool.acquire() as conn:
			await apply_migrations(conn)
	finally:
		await pool.close()


if __name__ == "__main__":
	asyncio.run(_main())





