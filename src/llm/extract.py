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
		Верни JSON-объект строго такого вида:
		{{
			"type": "exact" | "alternative" | "none",
			"candidates": [{{"full_name": string, "position": string, "email": string | null}}]
		}}
		Правила:
		- "exact" для точного совпадения; "alternative" если точного нет, но есть близкие должности; "none" если данных нет.
		- "candidates" может содержать несколько объектов. Email указывай если есть, иначе null.
		- Не добавляй пояснений, текста вне JSON и не нарушай структуру.
		Текст:\n\n
	""")
	trimmed_text, _ = _trim_to_token_limit(control_prompt, text, config.TOKEN_LIMIT, config.SAFETY_TOKENS)
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
			response_format={"type": "json_object"},
		)
		raw = (resp.choices[0].message.content or "").strip()
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
					email = (c.get("email") or None)
					email = (email or "").strip() or None
					if full_name and position:
						norm.append({"full_name": full_name, "position": position, "email": email})
				data["candidates"] = norm
			else:
				data["candidates"] = []
			return data
		except Exception:
			logging.warning("groq returned non-JSON")
			return {}
	except Exception:
		logging.exception("groq extract failed")
		return {}

def format_extracted_name(data) -> str:
	if not isinstance(data, dict):
		return ""
	t = (data.get("type") or "none").lower()
	cands = data.get("candidates") or []
	if t == "none" or not cands:
		return ""
	def _fmt(c):
		p = f"{c.get('full_name')}, {c.get('position')}"
		if c.get("email"):
			p += f", {c.get('email')}"
		return p
	if t == "exact":
		return f"Точное совпадение: {_fmt(cands[0])}"
	if t == "alternative":
		lines = "\n".join(_fmt(c) for c in cands)
		return f"Точное совпадение не найдено, альтернатива:\n{lines}"
	return "\n".join(_fmt(c) for c in cands)
