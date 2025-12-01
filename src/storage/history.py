from typing import Any
from .db import get_pool
from .users import get_or_create_uid_for_telegram


async def log_telegram_interaction(message: Any, reply_text: str) -> None:
	if not message:
		return
	chat = getattr(message, "chat", None)
	from_user = getattr(message, "from_user", None)
	text = (getattr(message, "text", "") or "").strip()
	if not chat:
		return
	chat_id = int(getattr(chat, "id", 0) or 0)
	if not chat_id:
		return
	uid = await get_or_create_uid_for_telegram(chat_id, from_user)
	pool = get_pool()
	async with pool.acquire() as conn:
		user_row = await conn.fetchrow("select id from users where uid=$1", uid)
		if not user_row:
			return
		user_id = int(user_row["id"])
		convo_row = await conn.fetchrow(
			"insert into conversations(user_id) values($1) returning id",
			user_id,
		)
		conversation_id = int(convo_row["id"])
		await conn.executemany(
			"insert into messages(user_id, conversation_id, role, content) values($1,$2,$3,$4)",
			[
				(user_id, conversation_id, "user", text),
				(user_id, conversation_id, "assistant", reply_text),
			],
		)





