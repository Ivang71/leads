import os, re, asyncio, time, sys, json
from dotenv import load_dotenv
from aiohttp import web, ClientSession
import requests
import logging
from groq import Groq
import tiktoken

GREETED_CHAT_IDS: set[int] = set[int]()

STATS_PATH = os.path.join(os.path.dirname(__file__), "stat.jsonl")
_REQUEST_STATS_BUFFER: list[dict] = []
_REQUEST_STATS_LOCK = asyncio.Lock()
_STATS_TASK: asyncio.Task | None = None

async def _record_request_stat(event: dict) -> None:
	async with _REQUEST_STATS_LOCK:
		_REQUEST_STATS_BUFFER.append(event)

async def _flush_stats_now() -> None:
	async with _REQUEST_STATS_LOCK:
		if not _REQUEST_STATS_BUFFER:
			return
		to_write = _REQUEST_STATS_BUFFER[:]
		_REQUEST_STATS_BUFFER.clear()
	def _write():
		try:
			os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
		except Exception:
			pass
		try:
			with open(STATS_PATH, "a", encoding="utf-8") as wf:
				for ev in to_write:
					wf.write(json.dumps(ev, ensure_ascii=False) + "\n")
		except Exception:
			logging.exception("failed to append stats")
	await asyncio.to_thread(_write)

async def _stats_flush_loop() -> None:
	while True:
		try:
			await asyncio.sleep(60)
			await _flush_stats_now()
		except asyncio.CancelledError:
			break
		except Exception:
			logging.exception("stats flush failed")


load_dotenv()
logging.basicConfig(level=getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200

MS_TIMEOUT_SEC = 36

def _trim_to_token_limit(instruction_prefix: str, text: str, token_limit: int, safety: int) -> tuple[str, dict]:
	enc = tiktoken.get_encoding("cl100k_base")
	pref_tokens = enc.encode(instruction_prefix or "")
	text_tokens = enc.encode(text or "")
	budget = max(0, token_limit - len(pref_tokens) - max(0, safety))
	keep = min(len(text_tokens), budget)
	trimmed_text = enc.decode(text_tokens[:keep]) if keep > 0 else ""
	return trimmed_text, {
		"pref_tokens": len(pref_tokens),
		"text_tokens": len(text_tokens),
		"budget": budget,
		"kept": keep,
		"total_after": len(pref_tokens) + keep,
	}

def _extract_name_with_groq(query: str, text: str) -> str:
	text = (text or "").strip()
	if not text:
		return ""
	api_key = os.environ.get("GROQ_API_KEY")
	if not api_key:
		logging.warning("GROQ_API_KEY not set")
		return ""
	system_prompt = ("Ты помощник-экстрактор данных. Выдавай исключительно что просит пользователь без дополнительных комментариев.")
	control_prompt = (f"""
		В следующем тексте твоя задача выдать наиболее актуальную информацию, отвечающую кто сейчас {query}. 
		Отвечай исключительно в формате "[модификатор]\nПолное имя, Должность" например "[exact]\nИван Остапович Озёрный, Директор по развитию"
		или "[alternative]\nЕкатерина Васильевна Глухих, Head of Marketing, kate@example.com\nМихаил Александрович Кузнецов, Head of Sales". 
		Модификаторы точности: [exact] - точное совпадение, [alternative] - альтернативные должности (используй если точное совпадение не найдено).\n
		Ты можешь выдать несколько альтернативных если они предлагаются как несколько уникальных имён.\nДобавляй почту если она есть в тексте.\n
		Не выдавай позиции без имени. Если данных недостаточно — выдай абсолютно пустую строку.\nТекст:\n\n
	""")
	trimmed_text, _ = _trim_to_token_limit(control_prompt, text, TOKEN_LIMIT, SAFETY_TOKENS)
	prompt = control_prompt + trimmed_text
	try:
		client = Groq(api_key=api_key)
		resp = client.chat.completions.create(
			model="llama-3.1-8b-instant",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": prompt},
			],
			temperature=0.2,
			max_tokens=64,
			top_p=1,
			stream=False,
		)
		return ((resp.choices[0].message.content or "").strip())
	except Exception:
		logging.exception("groq extract failed")
		return ""

def _parse_groq_tag(s: str) -> tuple[str | None, str]:
	s = (s or "").strip()
	if not s:
		return None, ""
	m = re.match(r"^\s*\[(exact|alternative)\]\s*(.*)$", s, flags=re.IGNORECASE)
	if m:
		tag = (m.group(1) or "").lower()
		rest = (m.group(2) or "").strip()
		return tag, rest
	return None, s

def _format_extracted_name(s: str) -> str:
	tag, rest = _parse_groq_tag(s)
	if not rest:
		return ""
	if tag == "exact":
		return f"Точное совпадение: {rest}"
	if tag == "alternative":
		return f"Точное совпадение не найдено, альтернатива: {rest}"
	return rest

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
	t0 = time.monotonic()
	ok_flag = False
	ms_len = 0
	tg_len = 0
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
			ms_text = (answer or "").strip()
			ms_len = len(ms_text)
			ok_flag = bool(ms_text)
			name = _extract_name_with_groq(text, ms_text)
			final_name = _format_extracted_name(name)
			final = (final_name or ms_text or "нет ответа").strip()
			tg_len = len(final)
			for chunk in _split_telegram_messages(final):
				await _send_message(session, bot_token, chat_id, chunk)
		finally:
			if status_id:
				await _delete_message(session, bot_token, chat_id, status_id)
	# record after finishing user-visible work
	try:
		await _record_request_stat({
			"ts": int(time.time()),
			"source": "tg",
			"chat_id": chat_id,
			"text_len": len(text or ""),
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok_flag,
			"ms_len": ms_len,
			"tg_len": tg_len,
		})
	except Exception:
		pass

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
		t0 = time.monotonic()
		ok = False
		q = (request.query.get("q") or "").strip()
		if not q:
			resp = web.json_response({"error": "q missing"}, status=400)
			await _record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": 0})
			return resp
		base = (os.environ.get("ALICE_URL"))
		if not base:
			resp = web.json_response({
				"error": "ALICE_URL missing",
				"hint": "Set ALICE_URL in .env, e.g. http://127.0.0.1:3000",
			}, status=500)
			await _record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": len(q)})
			return resp
		try:
			ms_text = await ask_alice(q)
		except Exception as e:
			logging.exception("test failed")
			resp = web.json_response({
				"error": "processing failed",
				"reason": str(e),
			}, status=500)
			await _record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": len(q)})
			return resp
		if not ms_text:
			resp = web.json_response({"error": "no answer produced"}, status=502)
			await _record_request_stat({
				"ts": int(time.time()),
				"source": "http_test",
				"dur": round(max(0.0, time.monotonic() - t0), 1),
				"ok": ok,
				"q_len": len(q),
				"ms_len": 0,
				"out_len": 0,
			})
			return resp
		name = _extract_name_with_groq(q, ms_text)
		out = (_format_extracted_name(name) or (ms_text or "").strip())
		if not out:
			resp = web.json_response({"error": "no answer produced"}, status=502)
			await _record_request_stat({
				"ts": int(time.time()),
				"source": "http_test",
				"dur": round(max(0.0, time.monotonic() - t0), 1),
				"ok": ok,
				"q_len": len(q),
				"ms_len": len((ms_text or "").strip()),
				"out_len": 0,
			})
			return resp
		ok = True
		resp = web.Response(text=out)
		await _record_request_stat({
			"ts": int(time.time()),
			"source": "http_test",
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok,
			"q_len": len(q),
			"ms_len": len((ms_text or "").strip()),
			"out_len": len(out),
		})
		return resp

	async def _stats_startup(_: web.Application) -> None:
		global _STATS_TASK
		if _STATS_TASK is None or _STATS_TASK.done():
			_STATS_TASK = asyncio.create_task(_stats_flush_loop())

	async def _stats_cleanup(_: web.Application) -> None:
		await _flush_stats_now()
		if _STATS_TASK and not _STATS_TASK.done():
			_STATS_TASK.cancel()
			try:
				await _STATS_TASK
			except asyncio.CancelledError:
				pass

	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
	app.on_startup.append(_stats_startup)
	app.on_cleanup.append(_stats_cleanup)
	return app

if __name__ == "__main__":
	web.run_app(create_app(), host="127.0.0.1", port=8000)
