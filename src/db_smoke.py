import asyncio, os
from . import config
from .storage.db import on_startup, on_cleanup, get_pool
from .storage.users import get_or_create_uid_for_telegram, mark_subscription_paid, get_subscription_status


async def main() -> None:
	app = object()
	await on_startup(app)
	try:
		uid = await get_or_create_uid_for_telegram(123456789)
		await mark_subscription_paid(uid, 1)
		active, active_until = await get_subscription_status(uid)
		print("uid", uid)
		print("active", active, "until", active_until)
		pool = get_pool()
		async with pool.acquire() as conn:
			row = await conn.fetchrow("select count(*) as c from messages")
			print("messages_count", int(row["c"]))
	finally:
		await on_cleanup(app)


if __name__ == "__main__":
	os.environ.setdefault("DB_DSN", config.DB_DSN)
	asyncio.run(main())





