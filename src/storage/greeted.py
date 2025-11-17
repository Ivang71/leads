import os, json, asyncio, logging
from .. import config

def _atomic_write_text(path: str, text: str) -> None:
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as wf:
		wf.write(text)
	os.replace(tmp, path)

async def save_greeted(ids: set[int]) -> None:
	def _do():
		try:
			_atomic_write_text(config.GREETED_PATH, json.dumps(sorted(list(ids))))
		except Exception:
			logging.exception("failed to save greeted set")
	return await asyncio.to_thread(_do)

def load_greeted() -> set[int]:
	try:
		with open(config.GREETED_PATH, "r", encoding="utf-8") as rf:
			data = json.load(rf)
			if isinstance(data, list):
				out = set()
				for it in data:
					try:
						out.add(int(it))
					except Exception:
						continue
				return out
	except FileNotFoundError:
		return set()
	except Exception:
		logging.exception("failed to load greeted set")
		return set()
	return set()

GREETED_CHAT_IDS: set[int] = load_greeted()
