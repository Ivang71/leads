import time, asyncio, os, json, logging
from typing import Callable, Awaitable
from groq import Groq
from ..clients.alice import ask_alice
from ..clients.http_client import get_session
from .. import config
from ..llm.extract import extract_name_with_groq, format_extracted_name

async def search_google(user_text: str) -> None:
	api_key = os.environ.get("SERPER_API_KEY")
	if not api_key:
		return
	user_text = (user_text or "").strip()
	if not user_text:
		return
	try:
		client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
		system_prompt = "Ты помощник по формированию поисковых запросов. Верни только валидный JSON массив из ровно 2 строк-запросов. Никакого текста вне JSON."
		user_prompt = f"Сформируй 2 кратких запроса для Google по тексту: \"{user_text}\". Верни массив из 2 строк. Например \"['shopify head of ecommerce', 'shopify head of business development']\"."
		resp = client.chat.completions.create(
			model="llama-3.1-8b-instant",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			temperature=0.2,
			max_tokens=256,
			top_p=1,
			stream=False,
		)
		raw = (resp.choices[0].message.content or "").strip()
		queries = []
		try:
			parsed = json.loads(raw)
			if isinstance(parsed, list):
				queries = parsed
			elif isinstance(parsed, dict) and isinstance(parsed.get("queries"), list):
				queries = parsed["queries"]
		except Exception:
			return
		string_queries = []
		for item in (queries or []):
			if isinstance(item, str):
				s = item.strip()
				if s:
					string_queries.append(s)
			elif isinstance(item, dict):
				q = (item.get("q") or "").strip()
				if q:
					string_queries.append(q)
		string_queries = string_queries[:2]
		normalized = [{"q": s, "gl": "ru"} for s in string_queries]
		if not normalized:
			return
		if os.environ.get("DEBUG") == "1":
			logging.info("groq google queries: %s", normalized)
		url = "https://google.serper.dev/search"
		session = get_session()
		headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
		async def _fetch_one(p):
			try:
				async with session.post(url, headers=headers, json=[p]) as r:
					return await r.text()
			except Exception:
				return None
		payloads = normalized + [{"q": s, "gl": "ru", "page": 2} for s in string_queries]
		texts = await asyncio.gather(*[_fetch_one(p) for p in payloads], return_exceptions=True)
		items_out = []
		for text in texts:
			if not isinstance(text, str) or not text:
				continue
			try:
				data = json.loads(text)
			except Exception:
				continue
			if isinstance(data, dict):
				data = [data]
			for one in (data or []):
				for it in ((one or {}).get("organic") or []):
					title = ((it.get("title") or "").strip())
					date = ((it.get("date") or "").strip())
					snippet = ((it.get("snippet") or "").strip())
					if title or date or snippet:
						items_out.append({"title": title, "date": date, "snippet": snippet})
		if os.environ.get("DEBUG") == "1" and items_out:
			with open(config.SERPER_RESULTS_PATH, "a", encoding="utf-8") as f:
				f.write(json.dumps(items_out, ensure_ascii=False) + "\n")
				logging.info("serper finished: saved %d items for %d queries", len(items_out), len(normalized))
	except Exception:
		logging.exception("search_google error")

async def process_query(user_text: str, on_progress: Callable[[], Awaitable[None]] | None = None) -> dict:
	"""
	Core flow: call Alice, extract with Groq, build final text.
	Returns: {"ok": bool, "ms_len": int, "final_text": str}
	"""
	# try: # disabled for now as does not more information than Alice
	# 	asyncio.create_task(search_google(user_text))
	# except Exception:
	# 	pass
	ms_text = await ask_alice(user_text or "", on_progress)
	ms = (ms_text or "").strip()
	name = extract_name_with_groq(user_text or "", ms)
	final_text = (format_extracted_name(name) or ms or "нет ответа").strip()
	return {
		"ok": bool(ms),
		"ms_len": len(ms),
		"final_text": final_text,
	}

