import os, re, asyncio, time, sys, json
from dotenv import load_dotenv
from aiohttp import web, ClientSession
import requests
import logging

GREETED_CHAT_IDS: set[int] = set[int]()


load_dotenv()
logging.basicConfig(level=getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200

MS_TIMEOUT_SEC = 36

GREETED_PATH = os.path.join(os.path.dirname(__file__), "greeted.json")

def _atomic_write_text(path: str, text: str) -> None:
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as wf:
		wf.write(text)
	os.replace(tmp, path)

def _load_greeted(path: str) -> set[int]:
	try:
		with open(path, "r", encoding="utf-8") as rf:
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

async def _save_greeted(ids: set[int]) -> None:
	def _do():
		try:
			_atomic_write_text(GREETED_PATH, json.dumps(sorted(list(ids))))
		except Exception:
			logging.exception("failed to save greeted set")
	return await asyncio.to_thread(_do)

# initialize greeted set from disk
GREETED_CHAT_IDS = _load_greeted(GREETED_PATH)

async def ask_alice(query: str, on_start = None) -> str:
	base = (os.environ.get("ALICE_URL")).strip()
	if not base:
		logging.warning("ALICE_URL not set")
		return ""
	url = (base.rstrip("/") + "/search")
	if callable(on_start):
		try:
			await on_start()
		except Exception:
			pass
	t0 = time.monotonic()
	try:
		resp = requests.get(url, params={"q": query}, timeout=MS_TIMEOUT_SEC)
		if resp.status_code >= 400:
			logging.warning("alice ms http=%s", resp.status_code)
			return ""
		body = resp.text or ""
		logging.info("alice ms http=%s body_chars=%d", resp.status_code, len(body))
		return body
	except requests.exceptions.ConnectTimeout:
		logging.warning("alice ms timeout: %s", url)
	except requests.exceptions.ConnectionError as e:
		logging.warning("alice ms unreachable: %s", str(e))
	except Exception:
		logging.exception("alice ms request failed")
	finally:
		logging.info("alice ms dur=%.2fs", time.monotonic() - t0)
	return ""

async def _send_message(session: ClientSession, bot_token: str, chat_id: int, text: str) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
	try:
		resp = await session.post(url, json={"chat_id": chat_id, "text": text})
		if resp.status >= 400:
			body = (await resp.text())[:200]
			logging.warning("tg sendMessage http=%s body=%s", resp.status, body)
		else:
			data = await resp.json()
			if not ((data or {}).get("ok")):
				logging.warning("tg sendMessage api_error=%s", json.dumps(data)[:200])
	except Exception:
		logging.exception("tg sendMessage failed")

async def _send_message_get_id(session: ClientSession, bot_token: str, chat_id: int, text: str) -> int | None:
	url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
	try:
		resp = await session.post(url, json={"chat_id": chat_id, "text": text})
		if resp.status >= 400:
			body = (await resp.text())[:200]
			logging.warning("tg sendMessage(http id) http=%s body=%s", resp.status, body)
			return None
		data = await resp.json()
		if not ((data or {}).get("ok")):
			logging.warning("tg sendMessage(http id) api_error=%s", json.dumps(data)[:200])
			return None
		return ((data.get("result") or {}).get("message_id"))
	except Exception:
		logging.exception("tg sendMessage(http id) failed")
		return None

async def _edit_message_text(session: ClientSession, bot_token: str, chat_id: int, message_id: int, text: str) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
	try:
		resp = await session.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": text})
		if resp.status >= 400:
			body = (await resp.text())[:200]
			logging.warning("tg editMessageText http=%s body=%s", resp.status, body)
		else:
			data = await resp.json()
			if not ((data or {}).get("ok")):
				logging.warning("tg editMessageText api_error=%s", json.dumps(data)[:200])
	except Exception:
		logging.exception("tg editMessageText failed")

def _split_telegram_messages(text: str, limit: int = 4096) -> list[str]:
	if not text:
		return []
	out = []
	buf = []
	curr = 0
	for ln in text.splitlines(keepends=True):
		if len(ln) > limit:
			if curr > 0:
				out.append("".join(buf))
				buf = []
				curr = 0
			for i in range(0, len(ln), limit):
				out.append(ln[i:i+limit])
			continue
		if curr + len(ln) > limit:
			out.append("".join(buf))
			buf = []
			curr = 0
		buf.append(ln)
		curr += len(ln)
	if curr > 0:
		out.append("".join(buf))
	return out

async def _delete_message(session: ClientSession, bot_token: str, chat_id: int, message_id: int) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
	try:
		resp = await session.post(url, json={"chat_id": chat_id, "message_id": message_id})
		if resp.status >= 400:
			body = (await resp.text())[:200]
			logging.warning("tg deleteMessage http=%s body=%s", resp.status, body)
		else:
			data = await resp.json()
			if not ((data or {}).get("ok")):
				logging.warning("tg deleteMessage api_error=%s", json.dumps(data)[:200])
	except Exception:
		logging.exception("tg deleteMessage failed")

async def _process_update(bot_token: str, chat_id: int, text: str) -> None:
	logging.info("process_update chat_id=%s text='%s'", str(chat_id), (text or "")[:200])
	async with ClientSession() as session:
		if chat_id not in GREETED_CHAT_IDS:
			await _send_message(session, bot_token, chat_id, "👋 Hi! Send me a query.")
			GREETED_CHAT_IDS.add(chat_id)
			try:
				await _save_greeted(GREETED_CHAT_IDS)
			except Exception:
				pass
		status_id = await _send_message_get_id(session, bot_token, chat_id, "Ищу")
		async def on_llm_start():
			if status_id:
				await _edit_message_text(session, bot_token, chat_id, status_id, "Думаю")
		try:
			answer = await ask_alice(text, on_llm_start)
		except Exception:
			logging.exception("processing failed")
			answer = ""
		try:
			answer = (answer or "").strip()
			if not answer:
				answer = "нет ответа"
			for chunk in _split_telegram_messages(answer):
				await _send_message(session, bot_token, chat_id, chunk)
		finally:
			if status_id:
				await _delete_message(session, bot_token, chat_id, status_id)

async def handle_webhook(request: web.Request) -> web.Response:
	logging.info("webhook hit")
	bot_token = os.environ.get("TG_BOT_TOKEN")
	if not bot_token:
		return web.Response(status=500, text="TG_BOT_TOKEN missing")
	path_token = request.match_info.get("token")
	if path_token != bot_token:
		return web.Response(status=403)
	try:
		update = await request.json()
	except Exception:
		return web.Response(status=400)
	message = (update.get("message") or update.get("edited_message") or {})
	chat = message.get("chat") or {}
	chat_id = chat.get("id")
	text = (message.get("text") or "").strip()
	logging.info("update chat_id=%s text_len=%d", str(chat_id), len(text))
	if chat_id:
		asyncio.create_task(_process_update(bot_token, chat_id, text))
	return web.json_response({"ok": True})

def create_app() -> web.Application:
	app = web.Application()
	app.router.add_post("/tg/{token}", handle_webhook)

	async def health(_: web.Request) -> web.Response:
		return web.Response(text="ok")


	async def test(request: web.Request) -> web.Response:
		q = (request.query.get("q") or "").strip()
		if not q:
			return web.json_response({"error": "q missing"}, status=400)
		base = (os.environ.get("ALICE_URL"))
		if not base:
			return web.json_response({
				"error": "ALICE_URL missing",
				"hint": "Set ALICE_URL in .env, e.g. http://127.0.0.1:3000",
			}, status=500)
		try:
			ans = await ask_alice(q)
		except Exception as e:
			logging.exception("test failed")
			return web.json_response({
				"error": "processing failed",
				"reason": str(e),
			}, status=500)
		if not ans:
			return web.json_response({"error": "no answer produced"}, status=502)
		return web.Response(text=ans)

	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
	return app

if __name__ == "__main__":
	web.run_app(create_app(), host="127.0.0.1", port=8000)
