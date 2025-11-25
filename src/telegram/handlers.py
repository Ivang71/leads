import time, logging, hashlib
from aiogram import types
from aiogram.enums import ChatAction
from ..core.processor import process_query
from ..storage.greeted import GREETED_CHAT_IDS, save_greeted
from ..stats import record_request_stat
from ..utils.text import split_telegram_messages
from ..storage.users import get_or_create_uid_for_telegram, get_subscription_status
from .. import config

async def cmd_start(message: types.Message):
	chat_id = message.chat.id
	if chat_id not in GREETED_CHAT_IDS:
		GREETED_CHAT_IDS.add(chat_id)
		try:
			await save_greeted(GREETED_CHAT_IDS)
		except Exception:
			pass
	await message.answer("👋 Отправьте запрос вида: «CEO <компания>». Я поищу подходящих людей и контакты. Команды: /help")

async def cmd_help(message: types.Message):
	await message.answer("Примеры:\n- CEO Acme Corp\n- [alternative] Head of Sales Globex\nПросто отправьте текст — я поищу и извлеку имя/должность/почту.")


async def cmd_id(message: types.Message):
	uid = await get_or_create_uid_for_telegram(message.chat.id)
	await message.answer(f"Твой ID: `{uid}`", parse_mode="Markdown")


async def cmd_subscribe(message: types.Message):
	if not (config.FREEKASSA_MERCHANT_ID and config.FREEKASSA_SECRET1):
		await message.answer("Платежи пока не настроены.")
		return
	uid = await get_or_create_uid_for_telegram(message.chat.id)
	amount = "49"
	currency = "RUB"
	order_id = f"{uid}-{int(time.time())}"
	raw = f"{config.FREEKASSA_MERCHANT_ID}:{amount}:{config.FREEKASSA_SECRET1}:{currency}:{order_id}"
	sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
	url = (
		"https://pay.freekassa.ru/"
		f"?m={config.FREEKASSA_MERCHANT_ID}"
		f"&oa={amount}"
		f"&o={order_id}"
		f"&currency={currency}"
		f"&s={sign}"
		f"&us_uid={uid}"
	)
	active, active_until = get_subscription_status(uid)
	if active:
		await message.answer(f"У тебя уже есть активная подписка.\nСсылка на продление на 30 дней за 300₽:\n{url}")
	else:
		await message.answer(f"Подписка на 30 дней за 300₽:\n{url}")

async def handle_text(message: types.Message):
	t0 = time.monotonic()
	ok_flag = False
	ms_len = 0
	out_len = 0
	await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
	status = await message.answer("Ищу")
	async def on_llm_start():
		try:
			await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
		except Exception:
			pass
	final = ""
	try:
		res = await process_query(message.text or "", on_llm_start)
		ok_flag = res.get("ok", False)
		ms_len = int(res.get("ms_len", 0))
		final = (res.get("final_text") or "").strip()
		out_len = len(final)
		for chunk in split_telegram_messages(final):
			await message.answer(chunk)
	finally:
		try:
			await status.delete()
		except Exception:
			pass
	try:
		await record_request_stat({
			"ts": int(time.time()),
			"source": "tg",
			"chat_id": message.chat.id,
			"text_len": len(message.text or ""),
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok_flag,
			"ms_len": ms_len,
			"tg_len": out_len,
		})
	except Exception:
		pass
