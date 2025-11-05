import os, re, asyncio, time, sys
from urllib.parse import urlparse
from dotenv import load_dotenv
from bs4 import BeautifulSoup, Comment
from groq import Groq
import tiktoken
from aiohttp import web, ClientSession
import requests
import logging

GREETED_CHAT_IDS: set[int] = set[int]()


load_dotenv()
logging.basicConfig(level=getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200

YANDEX_SERP_TIMEOUT_SEC = 36

FETCH_CONCURRENCY = 40
FETCH_TIMEOUT_SEC = 6
FETCH_OVERALL_TIMEOUT_SEC = 6

def _extract_links(o):
	if isinstance(o, dict):
		for k, v in o.items():
			if k == "link" and isinstance(v, str):
				yield v
			elif k == "links" and isinstance(v, list):
				for s in v:
					if isinstance(s, str):
						yield s
			else:
				yield from _extract_links(v)
	elif isinstance(o, list):
		for it in o:
			yield from _extract_links(it)


def _strip_html_to_text(html_bytes: bytes) -> str:
	soup = BeautifulSoup(html_bytes, "lxml")
	for t in soup(["script", "style", "noscript", "svg", "picture", "source", "template", "iframe"]):
		t.decompose()
	for sel in ["header", "footer", "nav", "aside", "form", "menu"]:
		for t in soup.select(sel):
			t.decompose()
	for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
		c.extract()
	rm_re = re.compile(r"(nav|menu|breadcrumb|footer|header|social|subscribe|comment|related|sidebar|cookie|banner|ad|promo|partner|catalog|search|rating|license|policy)", re.I)
	for t in soup.find_all(True):
		try:
			attrs = getattr(t, 'attrs', None)
			if not isinstance(attrs, dict):
				continue
			id_val = attrs.get('id')
			classes = attrs.get('class') or []
			if isinstance(classes, str):
				classes = [classes]
			ident = " ".join([
				str(id_val or ""),
				" ".join([str(c) for c in classes])
			]).strip()
			if ident and rm_re.search(ident):
				t.decompose()
		except Exception:
			continue
	text = (soup.body or soup).get_text(separator="\n")
	lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
	email_re = re.compile(r"[\w\.-]+@[\w\.-]+\.[A-Za-zА-Яа-я]{2,}")
	phone_re = re.compile(r"\+?\d[\d\-\s\(\)]{8,}\d")
	drop_re = re.compile(r"^(Поиск|Главное|Новости|Публикации|Интервью|Спецпроекты|Подкасты|Афиша|RSS-новости|Рейтинги|Категории|Каталог|Кейсы|Ещё|Maps|Market|Contacts|Контакты|Мы в социальных сетях:|Подписывайтесь|Мы на связи|На главную|Этот сайт использует cookie|Политика конфиденциальности|Пользовательское соглашение|Положение об обработке персональных данных|Согласие на обработку персональных данных|©|Telegram|ВКонтакте|Одноклассники|Rutube|Все рейтинги|Лидеры рейтингов|Календарь событий)\b", re.I)
	kept = []
	seen = set()
	for ln in lines:
		if not ln:
			continue
		key = ln.lower()
		if key in seen:
			continue
		seen.add(key)
		if drop_re.search(ln):
			continue
		if len(ln.split()) < 3 and not (email_re.search(ln) or phone_re.search(ln)):
			continue
		kept.append(ln)
	return "\n".join(kept)

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

async def fetch_all(query: str, save_root: bool = False, on_llm_start = None) -> None:
	ua = "Mozilla/5.0"
	start_total = time.monotonic()
	dur_ms = 0.0
	dur_fetch = 0.0
	dur_llm = 0.0
	base = os.environ.get("YANDEX_SERP_URL")
	obj = []
	if not base:
		logging.warning("YANDEX_SERP_URL not set")
	else:
		url = (base.rstrip("/") + "/search")
		_ms_t0 = time.monotonic()
		try:
			resp = requests.get(url, params={"q": query}, timeout=YANDEX_SERP_TIMEOUT_SEC)
			if resp.status_code >= 400:
				logging.warning("yandex ms http=%s", resp.status_code)
			else:
				ct = (resp.headers.get("content-type") or "").split(";")[0].strip()
				if ct == "application/json":
					obj = resp.json()
				else:
					logging.warning("yandex ms non-json response")
		except requests.exceptions.ConnectTimeout:
			logging.warning("yandex ms timeout: %s", url)
		except requests.exceptions.ConnectionError as e:
			logging.warning("yandex ms unreachable: %s", str(e))
		except Exception:
			logging.exception("yandex ms request failed")
		finally:
			dur_ms = time.monotonic() - _ms_t0
	links = list(dict.fromkeys(list(_extract_links(obj))))
	logging.info("query='%s' links=%d", query, len(links))
	if not links:
		logging.info("no links to fetch for query")
		logging.info(
			"timing ms=%.2fs fetch=%.2fs llm=%.2fs total=%.2fs links=%d",
			dur_ms, dur_fetch, dur_llm, time.monotonic() - start_total, len(links)
		)
		return ""
	headers = {
		"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
	}
	out_dir = os.path.join(os.path.dirname(__file__), "htmls")
	os.makedirs(out_dir, exist_ok=True)

	logging.info(
		"fetch config: links=%d concurrency=%d per_url_timeout=%ds overall_timeout=%ds",
		len(links), FETCH_CONCURRENCY, FETCH_TIMEOUT_SEC, FETCH_OVERALL_TIMEOUT_SEC
	)
	sem = asyncio.Semaphore(FETCH_CONCURRENCY)
	written_txts = []
	counters = {"ok": 0, "timeout": 0, "cancel": 0, "fail": 0}

	async def fetch_one(i: int, url: str) -> None:
		async with sem:
			try:
				_link_t0 = time.monotonic()
				worker = os.path.abspath(os.path.join(os.path.dirname(__file__), "fetch_worker.py"))
				proc = await asyncio.create_subprocess_exec(
					sys.executable, "-u", worker, url, str(FETCH_TIMEOUT_SEC),
					stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
				)
				try:
					stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=FETCH_TIMEOUT_SEC + 1)
				except asyncio.TimeoutError:
					counters["timeout"] += 1
					try:
						proc.kill()
					except Exception:
						pass
					logging.warning("fetch timeout [%d] %.2fs %s", i+1, time.monotonic()-_link_t0, url)
					return
				if proc.returncode != 0:
					counters["fail"] += 1
					logging.warning("fetch failed [%d]: %s", i+1, url)
					return
				try:
					import json, base64
					res = json.loads(stdout.decode("utf-8", errors="ignore"))
					final_url = res.get("url") or url
					content_b64 = res.get("content") or ""
					body = base64.b64decode(content_b64) if content_b64 else b""
				except Exception:
					counters["fail"] += 1
					logging.warning("fetch failed [%d]: %s", i+1, url)
					return
				p = urlparse(final_url)
				host = (p.netloc or "site").replace(":", "_")
				path_part = (p.path or "/").strip("/").replace("/", "_")
				if not path_part:
					path_part = "index"
				if len(path_part) > 80:
					path_part = path_part[:80]
				base = f"{i+1:02d}_{host}_{path_part}"
				html_path = os.path.join(out_dir, base + ".html")
				with open(html_path, "wb") as wf:
					wf.write(body)
				txt = _strip_html_to_text(body) if body else ""
				txt_path = os.path.join(out_dir, base + ".txt")
				with open(txt_path, "w", encoding="utf-8") as tf:
					tf.write(txt)
				try:
					written_txts.append((i, txt_path))
				except Exception:
					pass
				counters["ok"] += 1
			except Exception:
				counters["fail"] += 1
				logging.warning("fetch failed [%d]: %s", i+1, url)
				return
			except asyncio.CancelledError:
				counters["cancel"] += 1
				logging.warning("fetch cancelled [%d]: %s", i+1, url)
				return

	_fetch_t0 = time.monotonic()
	tasks = [asyncio.create_task(fetch_one(i, url)) for i, url in enumerate(links)]
	if tasks:
		pending = set(tasks)
		try:
			while pending:
				remaining = FETCH_OVERALL_TIMEOUT_SEC - (time.monotonic() - _fetch_t0)
				if remaining <= 0:
					break
				done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
			if pending:
				logging.warning("fetch phase timed out after %ss; pending=%d", FETCH_OVERALL_TIMEOUT_SEC, len(pending))
		finally:
			# hard cancel everything we spawned
			left = 0
			for t in tasks:
				if not t.done():
					left += 1
					t.cancel()
			if left:
				logging.warning("force-cancelled remaining fetchers: %d", left)
			# do NOT await cancelled tasks; return immediately
	dur_fetch = time.monotonic() - _fetch_t0
	logging.info("fetch summary: ok=%d timeout=%d cancel=%d fail=%d", counters["ok"], counters["timeout"], counters["cancel"], counters["fail"])
	combined_path = os.path.join(out_dir, "_combined.txt")
	parts = []
	for _, pth in sorted(written_txts, key=lambda x: x[0]):
		try:
			with open(pth, "r", encoding="utf-8") as rf:
				content = rf.read().strip()
				if content:
					parts.append(content)
		except Exception:
			continue
	with open(combined_path, "w", encoding="utf-8") as wf:
		wf.write("\n\n-----\n\n".join(parts))

	with open(combined_path, "r", encoding="utf-8") as rf:
		combined_text = rf.read()
	if save_root:
		root_agg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "aggregated.txt"))
		with open(root_agg_path, "w", encoding="utf-8") as rf:
			rf.write(combined_text)
		logging.info("combined_text_chars=%d aggregated_path=%s", len(combined_text), root_agg_path)
	else:
		logging.info("combined_text_chars=%d", len(combined_text))
	system_prompt = (
		"Ты помощник-экстрактор фактов. Отвечай строго одним полным именем в именительном падеже."
		" Никаких дополнительных слов, знаков или комментариев. Если данных недостаточно — выдай пустую строку."
	)
	prefix = (
		"В следующем тексте твоя задача выдать наиболее актуальную информацию, отвечающую кто сейчас \"{q}\". "
		"Отвечай одним полным именем, например \"Иван Остапович Озёрный\" или \"Екатерина Васильевна Глухих\". "
		"Ты не можешь вставить в ответ ничего кроме одного единственного полного имени. "
		"Информация с более поздней датой имеет колоссальный приоритет.\n\n"
		"Текст:\n"
	).format(q=query)
	trimmed_text, _ = _trim_to_token_limit(prefix, combined_text, TOKEN_LIMIT, SAFETY_TOKENS)
	user_prompt = prefix + trimmed_text
	if callable(on_llm_start):
		try:
			await on_llm_start()
		except Exception:
			pass
	_llm_t0 = time.monotonic()
	client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
	resp = client.chat.completions.create(
		model="llama-3.1-8b-instant",
		messages=[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": user_prompt},
		],
		temperature=1,
		max_tokens=512,
		top_p=1,
		stream=False,
	)
	answer = (resp.choices[0].message.content or "").strip()
	dur_llm = time.monotonic() - _llm_t0
	answer_path = os.path.join(out_dir, "_answer.txt")
	with open(answer_path, "w", encoding="utf-8") as wf:
		wf.write(answer)
	logging.info(
		"answer_len=%d pages=%d links=%d timing ms=%.2fs fetch=%.2fs llm=%.2fs total=%.2fs",
		len(answer), len(written_txts), len(links), dur_ms, dur_fetch, dur_llm, time.monotonic() - start_total
	)
	return answer

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
			answer = await fetch_all(text, False, on_llm_start)
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
		base = os.environ.get("YANDEX_SERP_URL")
		if not base:
			return web.json_response({
				"error": "YANDEX_SERP_URL missing",
				"hint": "Set YANDEX_SERP_URL in .env, e.g. http://127.0.0.1:3000",
			}, status=500)
		try:
			ans = await fetch_all(q, True)
		except Exception as e:
			logging.exception("test failed")
			return web.json_response({
				"error": "processing failed",
				"reason": str(e),
			}, status=500)
		if not ans:
			return web.json_response({
				"error": "no answer produced",
				"hint": "Ensure the microservice returns links",
			}, status=502)
		return web.Response(text=ans)

	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
	return app

if __name__ == "__main__":
	web.run_app(create_app(), host="127.0.0.1", port=8000)
