import logging, asyncpg, asyncio
from .. import config
from .migrations import apply_migrations

_POOL: asyncpg.Pool | None = None
_LOCK = asyncio.Lock()


async def on_startup(_: object) -> None:
	global _POOL
	async with _LOCK:
		if _POOL is not None:
			return
		_POOL = await asyncpg.create_pool(dsn=config.DB_DSN, min_size=1, max_size=5)
		if config.DB_AUTO_MIGRATE:
			async with _POOL.acquire() as conn:
				await apply_migrations(conn)


async def on_cleanup(_: object) -> None:
	global _POOL
	pool = _POOL
	_POOL = None
	if pool is not None:
		await pool.close()


def get_pool() -> asyncpg.Pool:
	if _POOL is None:
		raise RuntimeError("db pool not initialized")
	return _POOL
