import time
from typing import Callable, Awaitable
from ..clients.alice import ask_alice
from ..llm.extract import extract_name_with_groq, format_extracted_name

async def process_query(user_text: str, on_progress: Callable[[], Awaitable[None]] | None = None) -> dict:
	"""
	Core flow: call Alice, extract with Groq, build final text.
	Returns: {"ok": bool, "ms_len": int, "final_text": str}
	"""
	ms_text = await ask_alice(user_text or "", on_progress)
	ms = (ms_text or "").strip()
	name = extract_name_with_groq(user_text or "", ms)
	final_text = (format_extracted_name(name) or ms or "нет ответа").strip()
	return {
		"ok": bool(ms),
		"ms_len": len(ms),
		"final_text": final_text,
	}

