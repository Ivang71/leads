import time, logging, hashlib
from aiohttp import web
from ..core.processor import process_query
from ..stats import record_request_stat
from .. import config
from ..storage.users import mark_subscription_paid

async def health(_: web.Request) -> web.Response:
	return web.Response(text="ok")

async def test(request: web.Request) -> web.Response:
	t0 = time.monotonic()
	ok = False
	q = (request.query.get("q") or "").strip()
	if not q:
		resp = web.json_response({"error": "q missing"}, status=400)
		await record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": ok, "q_len": 0})
		return resp
	try:
		res = await process_query(q)
	except Exception as e:
		logging.exception("test failed")
		return web.json_response({"error": "processing failed", "reason": str(e)}, status=500)
	final_text = (res.get("final_text") or "").strip()
	ms_len = int(res.get("ms_len", 0))
	ok = bool(res.get("ok", False))
	if not final_text:
		await record_request_stat({"ts": int(time.time()), "source": "http_test", "dur": round(max(0.0, time.monotonic() - t0), 1), "ok": False, "q_len": len(q), "ms_len": ms_len, "out_len": 0})
		return web.json_response({"error": "no answer produced"}, status=502)
	resp = web.Response(text=final_text)
	await record_request_stat({
		"ts": int(time.time()),
		"source": "http_test",
		"dur": round(max(0.0, time.monotonic() - t0), 1),
		"ok": ok,
		"q_len": len(q),
		"ms_len": ms_len,
		"out_len": len(final_text),
	})
	return resp

async def freekassa_notify(request: web.Request) -> web.Response:
	data = await (request.post() if request.method == "POST" else request.query)
	m_id = (data.get("MERCHANT_ID") or "").strip()
	amount = (data.get("AMOUNT") or "").strip()
	order_id = (data.get("MERCHANT_ORDER_ID") or "").strip()
	sign = (data.get("SIGN") or "").strip()
	uid = (data.get("us_uid") or "").strip()
	if not (config.FREEKASSA_SECRET2 and m_id and amount and order_id and sign and uid):
		return web.Response(text="bad", status=400)
	raw = f"{m_id}:{amount}:{order_id}:{config.FREEKASSA_SECRET2}"
	expected = hashlib.md5(raw.encode("utf-8")).hexdigest()
	if expected.lower() != sign.lower():
		logging.warning("freekassa bad sign")
		return web.Response(text="bad sign", status=400)
	try:
		await mark_subscription_paid(uid, 30)
	except Exception:
		logging.exception("freekassa notify failed to mark subscription")
	return web.Response(text="OK")

async def freekassa_success(_: web.Request) -> web.Response:
	return web.Response(text="Payment successful. You can close this tab and return to Telegram.", content_type="text/html; charset=utf-8")

async def freekassa_fail(_: web.Request) -> web.Response:
	return web.Response(text="Payment failed or was cancelled. You can close this tab and try again from Telegram.", content_type="text/html; charset=utf-8")

def register_routes(app: web.Application) -> None:
	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
	app.router.add_route("*", "/tg/pay/freekassa/notify", freekassa_notify)
	app.router.add_get("/tg/pay/success", freekassa_success)
	app.router.add_get("/tg/pay/fail", freekassa_fail)
