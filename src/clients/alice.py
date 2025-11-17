import time, logging, asyncio
from aiohttp import ClientResponseError
from .. import config
from .http_client import get_session

async def ask_alice(query: str, on_start=None) -> str:
	base = (config.ALICE_URL or "").strip()
	if not base:
		logging.warning("ALICE_URL not set")
		return ""
	url = (base.rstrip("/") + "/search")
	q_text = (query or "").strip()
	search_q = f"найди {q_text} либо близкие должности в этой компании"
	if callable(on_start):
		try:
			await on_start()
		except Exception:
			pass
	t0 = time.monotonic()
	session = get_session()
	try:
		async with session.get(url, params={"q": search_q}) as resp:
			if resp.status >= 400:
				logging.warning("alice ms http=%s", resp.status)
				return ""
			body = await resp.text()
			logging.info("alice ms http=%s body_chars=%d", resp.status, len(body or ""))
			return body or ""
	except asyncio.TimeoutError:
		logging.warning("alice ms timeout: %s", url)
	except Exception:
		logging.exception("alice ms request failed")
	finally:
		logging.info("alice ms dur=%.2fs", time.monotonic() - t0)
	return ""
