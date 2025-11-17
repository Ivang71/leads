import logging
from aiohttp import web
from . import config
from .stats import on_startup as stats_startup, on_cleanup as stats_cleanup
from .clients.http_client import on_startup as http_startup, on_cleanup as http_cleanup
from .telegram.bot import create_bot_and_dispatcher, register_webhook_route
from .web.routes import register_routes
from aiogram.webhook.aiohttp_server import setup_application
from aiogram.types import BotCommand

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")

def create_app() -> web.Application:
	app = web.Application()
	# aiogram bot+dp
	bot, dp, secret = create_bot_and_dispatcher()
	register_webhook_route(app, bot, dp, secret)
	# http routes
	register_routes(app)
	# startup/cleanup
	app.on_startup.append(stats_startup)
	app.on_startup.append(http_startup)
	# set commands and webhook URL
	async def _bot_start(_: web.Application) -> None:
		try:
			await bot.set_my_commands([
				BotCommand(command="start", description="Начать"),
				BotCommand(command="help", description="Как пользоваться"),
			])
		except Exception:
			logging.exception("set_my_commands failed")
		if config.TG_WEBHOOK_URL:
			try:
				await bot.set_webhook(url=config.TG_WEBHOOK_URL, secret_token=(config.TG_WEBHOOK_SECRET or None), drop_pending_updates=True)
			except Exception:
				logging.exception("set_webhook failed")
	app.on_startup.append(_bot_start)
	app.on_cleanup.append(stats_cleanup)
	app.on_cleanup.append(http_cleanup)
	setup_application(app, dp, bot=bot)
	return app
