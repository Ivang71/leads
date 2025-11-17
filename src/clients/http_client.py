from aiohttp import ClientSession, ClientTimeout
from .. import config

_SESSION: ClientSession | None = None

def get_session() -> ClientSession:
	global _SESSION
	if _SESSION is None or _SESSION.closed:
		_SESSION = ClientSession(timeout=ClientTimeout(total=config.MS_TIMEOUT_SEC))
	return _SESSION

async def on_startup(_: any) -> None:
	get_session()

async def on_cleanup(_: any) -> None:
	global _SESSION
	if _SESSION and not _SESSION.closed:
		await _SESSION.close()
