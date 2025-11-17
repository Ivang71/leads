import time, logging
from aiohttp import web
from ..core.processor import process_query
from ..stats import record_request_stat

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

def register_routes(app: web.Application) -> None:
	app.router.add_get("/_health", health)
	app.router.add_get("/test", test)
