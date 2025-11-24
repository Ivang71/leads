import os, json, asyncio, logging, time, uuid
from .. import config


def _atomic_write_text(path: str, text: str) -> None:
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as wf:
		wf.write(text)
	os.replace(tmp, path)


def _load_users() -> dict:
	try:
		with open(config.USERS_PATH, "r", encoding="utf-8") as rf:
			data = json.load(rf)
			if isinstance(data, dict):
				return data
	except FileNotFoundError:
		return {}
	except Exception:
		logging.exception("failed to load users")
		return {}
	return {}


_USERS: dict = _load_users()
_LOCK = asyncio.Lock()


async def _save_users() -> None:
	def _do():
		try:
			os.makedirs(os.path.dirname(config.USERS_PATH), exist_ok=True)
		except Exception:
			pass
		try:
			_atomic_write_text(config.USERS_PATH, json.dumps(_USERS, ensure_ascii=False))
		except Exception:
			logging.exception("failed to save users")
	return await asyncio.to_thread(_do)


async def get_or_create_uid_for_telegram(chat_id: int) -> str:
	async with _LOCK:
		for uid, data in _USERS.items():
			try:
				if int(data.get("telegram_chat_id") or 0) == int(chat_id):
					return uid
			except Exception:
				continue
		uid = uuid.uuid4().hex
		_USERS[uid] = {"telegram_chat_id": int(chat_id), "active_until": 0}
		await _save_users()
		return uid


async def mark_subscription_paid(uid: str, days: int) -> None:
	if not uid:
		return
	async with _LOCK:
		now = int(time.time())
		u = _USERS.get(uid)
		if not isinstance(u, dict):
			u = {"telegram_chat_id": None, "active_until": 0}
			_USERS[uid] = u
		base = int(u.get("active_until") or 0)
		if base < now:
			base = now
		u["active_until"] = base + days * 86400
		await _save_users()


def get_subscription_status(uid: str) -> tuple[bool, int]:
	u = _USERS.get(uid)
	if not isinstance(u, dict):
		return False, 0
	active_until = int(u.get("active_until") or 0)
	now = int(time.time())
	return active_until >= now, active_until


