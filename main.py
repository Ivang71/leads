import os, sys, re, asyncio, json
from urllib.parse import urlparse
from dotenv import load_dotenv
from importlib import import_module
from bs4 import BeautifulSoup, Comment
from groq import Groq
import tiktoken
from aiohttp import web, ClientSession
import requests
import logging

GREETED_CHAT_IDS: set[int] = set()


load_dotenv()
logging.basicConfig(level=getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

TOKEN_LIMIT = 6000
SAFETY_TOKENS = 200

def _extend_sys_path():
    # Prefer explicit PHANTOM_PATH if provided
    phantom_path = os.environ.get("PHANTOM_PATH")
    if phantom_path and os.path.isdir(phantom_path) and phantom_path not in sys.path:
        sys.path.insert(0, phantom_path)
    # Also add parent dir so `phantom` package at ../phantom is importable
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if base not in sys.path:
        sys.path.insert(0, base)

def _extract_links(o):
	if isinstance(o, dict):
		for k, v in o.items():
			if k == "link" and isinstance(v, str):
				yield v
			else:
				yield from _extract_links(v)
	elif isinstance(o, list):
		for it in o:
			yield from _extract_links(it)

def _serper_search(query: str) -> dict:
	try:
		api_key = os.getenv("SERPER_API_KEY")
		if not api_key:
			logging.warning("SERPER_API_KEY is missing")
			return {}
		headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
		payload = json.dumps({
			"q": query,
			"gl": "ru",
			"hl": "ru",
			"autocorrect": False,
		})
		resp = requests.post("https://google.serper.dev/search", headers=headers, data=payload, timeout=12)
		if resp.status_code >= 400:
			logging.warning("Serper HTTP %s", resp.status_code)
			return {}
		return resp.json()
	except Exception:
		logging.exception("Serper request failed")
		return {}

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

async def fetch_all(query: str) -> None:
	_extend_sys_path()
	from tls_browser import TlsBrowser
	ua = "Mozilla/5.0"
	obj = _serper_search(query)
	links = list(dict.fromkeys(list(_extract_links(obj))))
	logging.info("query='%s' links=%d", query, len(links))
	if not links:
		logging.info("no links to fetch for query")
		return ""
	headers = {
		"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
		"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
	}
	out_dir = os.path.join(os.path.dirname(__file__), "htmls")
	os.makedirs(out_dir, exist_ok=True)

	sem = asyncio.Semaphore(50)
	written_txts = []

	async def fetch_one(i: int, url: str) -> None:
		async with sem:
			try:
				async with TlsBrowser(user_agent=ua, proxy=None) as browser:
					res = await browser.get(url, headers=headers, timeout=8, follow=True, max_bytes=16777216)
				final_url = (res or {}).get("url") or url
				content_val = (res or {}).get("content")
				body = content_val if isinstance(content_val, (bytes, bytearray)) else (bytes(content_val or b"") if content_val is not None else b"")
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
			except Exception:
				logging.warning("fetch failed: %s", url)
				return

	tasks = [asyncio.create_task(fetch_one(i, url)) for i, url in enumerate(links)]
	if tasks:
		await asyncio.gather(*tasks)
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
	answer_path = os.path.join(out_dir, "_answer.txt")
	with open(answer_path, "w", encoding="utf-8") as wf:
		wf.write(answer)
	logging.info("answer_len=%d", len(answer))
	return answer

async def _send_message(session: ClientSession, bot_token: str, chat_id: int, text: str) -> None:
	url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
	await session.post(url, json={"chat_id": chat_id, "text": text})

async def _process_update(bot_token: str, chat_id: int, text: str) -> None:
	logging.info("process_update chat_id=%s text='%s'", str(chat_id), (text or "")[:200])
	async with ClientSession() as session:
		if chat_id not in GREETED_CHAT_IDS:
			await _send_message(session, bot_token, chat_id, "👋 Hi! Send me a query.")
			GREETED_CHAT_IDS.add(chat_id)
		try:
			answer = await fetch_all(text)
		except Exception:
			logging.exception("processing failed")
			answer = ""
		if answer and answer.strip():
			await _send_message(session, bot_token, chat_id, answer.strip())

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

	app.router.add_get("/_health", health)
	return app

if __name__ == "__main__":
	port = int(os.environ.get("PORT") or 8000)
	web.run_app(create_app(), host="127.0.0.1", port=port)

