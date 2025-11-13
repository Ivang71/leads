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
		return resp.text
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
	await session.post(url, json={"chat_id": chat_id, "text": text})

async def _send_message_get_id(session: ClientSession, bot_token: str, chat_id: int, text: str) -> int | None:
	url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
	try:
		resp = await session.post(url, json={"chat_id": chat_id, "text": text})
		data = await resp.json()
		return ((data or {}).get("result") or {}).get("message_id")
	except Exception:
		return None

async def _edit_message_text(session: ClientSession, bot_token: str, chat_id: int, message_id: int, text: str) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
	try:
		await session.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": text})
	except Exception:
		pass

async def _delete_message(session: ClientSession, bot_token: str, chat_id: int, message_id: int) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
	try:
		await session.post(url, json={"chat_id": chat_id, "message_id": message_id})
	except Exception:
		pass

async def _process_update(bot_token: str, chat_id: int, text: str) -> None:
	logging.info("process_update chat_id=%s text='%s'", str(chat_id), (text or "")[:200])
	async with ClientSession() as session:
		if chat_id not in GREETED_CHAT_IDS:
			await _send_message(session, bot_token, chat_id, "👋 Hi! Send me a query.")
			GREETED_CHAT_IDS.add(chat_id)
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
			if answer and answer.strip():
				await _send_message(session, bot_token, chat_id, answer.strip())
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
