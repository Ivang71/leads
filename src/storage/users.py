import time, uuid, logging
from typing import Any
from .db import get_pool


async def get_or_create_uid_for_telegram(chat_id: int, tg_user: Any | None = None) -> str:
	pool = get_pool()
	chat_id_int = int(chat_id)
	async with pool.acquire() as conn:
		row = await conn.fetchrow("select uid from users where telegram_id=$1", chat_id_int)
		if row:
			if tg_user:
				await _update_profile(conn, chat_id_int, tg_user)
			return str(row["uid"])
		uid = uuid.uuid4().hex
		username = None
		first_name = None
		last_name = None
		display_name = None
		locale = None
		if tg_user:
			username = (getattr(tg_user, "username", "") or "").strip() or None
			first_name = (getattr(tg_user, "first_name", "") or "").strip() or None
			last_name = (getattr(tg_user, "last_name", "") or "").strip() or None
			display_name = (getattr(tg_user, "full_name", "") or "").strip() or None
			locale = (getattr(tg_user, "language_code", "") or "").strip() or None
		await conn.execute(
			"insert into users(uid, telegram_id, telegram_username, display_name, first_name, last_name, locale) "
			"values($1,$2,$3,$4,$5,$6,$7)",
			uid,
			chat_id_int,
			username,
			display_name,
			first_name,
			last_name,
			locale,
		)
		return uid


async def _update_profile(conn, chat_id: int, tg_user: Any) -> None:
	try:
		username = (getattr(tg_user, "username", "") or "").strip() or None
		first_name = (getattr(tg_user, "first_name", "") or "").strip() or None
		last_name = (getattr(tg_user, "last_name", "") or "").strip() or None
		display_name = (getattr(tg_user, "full_name", "") or "").strip() or None
		locale = (getattr(tg_user, "language_code", "") or "").strip() or None
		await conn.execute(
			"update users "
			"set telegram_username=$2, display_name=$3, first_name=$4, last_name=$5, locale=$6, updated_at=now() "
			"where telegram_id=$1",
			chat_id,
			username,
			display_name,
			first_name,
			last_name,
			locale,
		)
	except Exception:
		logging.exception("failed to update user profile")


async def mark_subscription_paid(uid: str, days: int) -> None:
	if not uid or days <= 0:
		return
	pool = get_pool()
	now_ts = int(time.time())
	async with pool.acquire() as conn:
		user_row = await conn.fetchrow("select id from users where uid=$1", uid)
		if not user_row:
			return
		user_id = int(user_row["id"])
		sub_row = await conn.fetchrow(
			"select id, expires_at from subscriptions where user_id=$1 and status='active' order by started_at desc limit 1",
			user_id,
		)
		if sub_row and sub_row["expires_at"]:
			base = int(sub_row["expires_at"].timestamp())
			if base < now_ts:
				base = now_ts
			await conn.execute(
				"update subscriptions set status='expired', updated_at=now() where id=$1",
				int(sub_row["id"]),
			)
		else:
			base = now_ts
		new_expires = base + days * 86400
		await conn.execute(
			"insert into subscriptions(user_id, plan_code, status, started_at, expires_at) "
			"values($1,$2,'active', to_timestamp($3), to_timestamp($4))",
			user_id,
			"default",
			now_ts,
			new_expires,
		)


async def get_subscription_status(uid: str) -> tuple[bool, int]:
	if not uid:
		return False, 0
	pool = get_pool()
	now_ts = int(time.time())
	async with pool.acquire() as conn:
		user_row = await conn.fetchrow("select id from users where uid=$1", uid)
		if not user_row:
			return False, 0
		user_id = int(user_row["id"])
		sub_row = await conn.fetchrow(
			"select id, expires_at from subscriptions where user_id=$1 and status='active' order by started_at desc limit 1",
			user_id,
		)
		if not sub_row or not sub_row["expires_at"]:
			return False, 0
		expires_ts = int(sub_row["expires_at"].timestamp())
		if expires_ts < now_ts:
			try:
				await conn.execute(
					"update subscriptions set status='expired', updated_at=now() where id=$1",
					int(sub_row["id"]),
				)
			except Exception:
				logging.exception("failed to expire subscription")
			return False, expires_ts
		return True, expires_ts

