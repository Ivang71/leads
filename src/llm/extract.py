import os, json, logging
import tiktoken
from groq import Groq
from .. import config

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

def extract_name_with_groq(query: str, text: str) -> dict:
	text = (text or "").strip()
	if not text:
		return {}
	api_key = os.environ.get("GROQ_API_KEY")
	if not api_key:
		logging.warning("GROQ_API_KEY not set")
		return {}
	system_prompt = ("Ты помощник-экстрактор данных. Возвращай только валидный JSON без комментариев.")
	control_prompt = (f"""
		Извлеки из текста наиболее актуальную информацию, кто сейчас {query}.
		Возвращай СТРОГО JSON-объект строго такого вида:
		{{
			"type": "exact" | "alternative" | "none",
			"candidates": [{{"full_name": string, "position": string, "email": string[]}}]
		}}
		Правила:
		- "exact" означает точное совпадение; "alternative" означает близкие должности; "none" означает отсутствие подходящих данных.
		- "candidates" может содержать несколько объектов. В каждом укажи "full_name", "position" и два варианта email этого человека в поле "email" по шаблонам ivan.ivanov@company.ru i.ivanov@company.ru для Иван Иванов Иванович заменяя домен на сайт компании.
		- Не добавляй пояснений, текста вне JSON и не нарушай структуру.
		- Ты можешь вернуть только этот json и ничего больше.
		Текст:\n\n
	""")
	trimmed_text, _ = _trim_to_token_limit(control_prompt, text, config.TOKEN_LIMIT, config.SAFETY_TOKENS)
	prompt = control_prompt + trimmed_text
	try:
		client = Groq(api_key=api_key)
		raw = None
		if os.environ.get("DEBUG") == "1":
			logging.info("groq extract prompt: %s", prompt)
		for attempt in range(3):
			resp = client.chat.completions.create(
				model="llama-3.1-8b-instant",
				messages=[
					{"role": "system", "content": system_prompt},
					{"role": "user", "content": prompt},
				],
				temperature=0.2,
				max_tokens=128,
				top_p=1,
				stream=False,
				response_format={"type": "json_object"},
			)
			raw = (resp.choices[0].message.content or "").strip()
			if not raw:
				if os.environ.get("DEBUG") == "1":
					logging.warning("groq empty response, retrying...")
				continue
			if len(raw) <= 600:
				break
			if os.environ.get("DEBUG") == "1":
				logging.warning("groq response too long (%d chars), retrying...", len(raw))
		if not raw or len(raw) > 600:
			if os.environ.get("DEBUG") == "1" and raw:
				logging.warning("groq response still too long after retries (%d chars), treating as failed", len(raw))
			return {}
		if os.environ.get("DEBUG") == "1":
			logging.info("\n\ngroq response: %s", raw)
		try:
			data = json.loads(raw)
			if not isinstance(data, dict):
				return {}
			t = data.get("type")
			if t not in ("exact", "alternative", "none"):
				data["type"] = "none"
			cands = data.get("candidates") or []
			if isinstance(cands, list):
				norm = []
				for c in cands:
					if not isinstance(c, dict):
						continue
					full_name = (c.get("full_name") or "").strip()
					position = (c.get("position") or "").strip()
					raw_email = c.get("email")
					emails = []
					if isinstance(raw_email, list):
						for v in raw_email:
							if isinstance(v, str):
								s = v.strip()
								if s and s not in emails:
									emails.append(s)
					elif isinstance(raw_email, str):
						s = raw_email.strip()
						if s:
							emails.append(s)
					if full_name and position and emails:
						norm.append({"full_name": full_name, "position": position, "emails": emails[:2]})
				data["candidates"] = norm
			else:
				data["candidates"] = []
			return data
		except Exception:
			logging.warning("groq returned non-JSON")
			return {}
	except Exception as e:
		if os.environ.get("DEBUG") == "1":
			resp = getattr(e, "response", None)
			if resp is not None:
				try:
					body = getattr(resp, "text", None)
					if callable(body):
						body = body()
					if not body:
						body = str(resp)
					logging.error("groq error response body: %s", body)
				except Exception:
					pass
		logging.exception("groq extract failed")
		return {}

def format_extracted_name(data) -> str:
	if not isinstance(data, dict):
		return ""
	t = (data.get("type") or "none").lower()
	cands = data.get("candidates") or []
	if t == "none" or not cands:
		return ""
	def _emails(c):
		out = []
		for v in (c.get("emails") or []):
			if isinstance(v, str):
				s = v.strip()
				if s and s not in out:
					out.append(s)
		return out[:2]
	def _fmt(c):
		return f"{c.get('full_name')}, {c.get('position')}"
	if t == "exact":
		base = _fmt(cands[0])
		emails = _emails(cands[0])
		if emails:
			base += "\nВозможные email:\n" + "\n".join(emails)
		return f"Точное совпадение: {base}"
	if t == "alternative":
		lines = "\n".join(_fmt(c) for c in cands)
		emails = _emails(cands[0])
		if emails:
			lines += "\n\nВозможные email:\n" + "\n".join(emails)
		return f"Точное совпадение не найдено, альтернатива:\n{lines}"
	return "\n".join(_fmt(c) for c in cands)
