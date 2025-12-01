import os, logging
from .db import get_pool
from .. import config


async def apply_migrations() -> None:
	pool = get_pool()
	base = os.path.join(config.PROJECT_ROOT, "migrations")
	try:
		names = sorted([n for n in os.listdir(base) if n.endswith(".sql")])
	except FileNotFoundError:
		return
	async with pool.acquire() as conn:
		await conn.execute(
			"create table if not exists schema_migrations (id bigserial primary key, name text not null unique, applied_at timestamptz not null default now())"
		)
		rows = await conn.fetch("select name from schema_migrations")
		applied = {str(r["name"]) for r in rows}
		for name in names:
			if name in applied:
				continue
			path = os.path.join(base, name)
			try:
				with open(path, "r", encoding="utf-8") as f:
					sql = f.read()
			except Exception:
				logging.exception("failed to read migration %s", name)
				continue
			try:
				async with conn.transaction():
					await conn.execute(sql)
					await conn.execute("insert into schema_migrations(name) values($1)", name)
			except Exception:
				logging.exception("failed to apply migration %s", name)
				break





