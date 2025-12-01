import asyncio
from .storage.db import on_startup, on_cleanup
from .storage.migrations import apply_migrations


async def _main() -> None:
	app = object()
	await on_startup(app)
	try:
		await apply_migrations()
	finally:
		await on_cleanup(app)


if __name__ == "__main__":
	asyncio.run(_main())





