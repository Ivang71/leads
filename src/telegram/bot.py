from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from .. import config
from . import handlers

class BotCtx:
	bot: Bot | None = None
	dp: Dispatcher | None = None

BOT_CTX = BotCtx()

def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher, str | None]:
	if not config.TG_BOT_TOKEN:
		raise RuntimeError("TG_BOT_TOKEN missing")
	bot = Bot(token=config.TG_BOT_TOKEN)
	dp = Dispatcher()
	# register handlers
	dp.message.register(handlers.cmd_start, CommandStart())
	dp.message.register(handlers.cmd_help, Command("help"))
	dp.message.register(handlers.cmd_id, Command("id"))
	dp.message.register(handlers.cmd_subscribe, Command("subscribe"))
	dp.callback_query.register(handlers.cb_menu_search, F.data == "menu_search")
	dp.callback_query.register(handlers.cb_menu_profile, F.data == "menu_profile")
	dp.callback_query.register(handlers.cb_menu_back, F.data == "menu_back")
	dp.message.register(handlers.handle_text)
	BOT_CTX.bot = bot
	BOT_CTX.dp = dp
	secret = config.TG_WEBHOOK_SECRET or None
	return bot, dp, secret

def register_webhook_route(app, bot: Bot, dp: Dispatcher, secret: str | None) -> None:
	SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, f"/tg/{config.TG_BOT_TOKEN}")
