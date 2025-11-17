import os, asyncio, time, json, logging
from . import config

_REQUEST_STATS_BUFFER: list[dict] = []
_REQUEST_STATS_LOCK = asyncio.Lock()
_STATS_TASK: asyncio.Task | None = None

async def record_request_stat(event: dict) -> None:
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
			os.makedirs(os.path.dirname(config.STATS_PATH), exist_ok=True)
		except Exception:
			pass
		try:
			with open(config.STATS_PATH, "a", encoding="utf-8") as wf:
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

async def on_startup(_: any) -> None:
	global _STATS_TASK
	if _STATS_TASK is None or _STATS_TASK.done():
		_STATS_TASK = asyncio.create_task(_stats_flush_loop())

async def on_cleanup(_: any) -> None:
	await _flush_stats_now()
	if _STATS_TASK and not _STATS_TASK.done():
		_STATS_TASK.cancel()
		try:
			await _STATS_TASK
		except asyncio.CancelledError:
			pass
