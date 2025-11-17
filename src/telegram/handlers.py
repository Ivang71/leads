import time, logging
from aiogram import types
from aiogram.enums import ChatAction
from ..clients.alice import ask_alice
from ..llm.extract import extract_name_with_groq, format_extracted_name
from ..storage.greeted import GREETED_CHAT_IDS, save_greeted
from ..stats import record_request_stat
from ..utils.text import split_telegram_messages

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
	try:
		ms_text = await ask_alice(message.text or "", on_llm_start)
	except Exception:
		logging.exception("processing failed")
		ms_text = ""
	final = ""
	try:
		ms = (ms_text or "").strip()
		ms_len = len(ms)
		ok_flag = bool(ms)
		name = extract_name_with_groq(message.text or "", ms)
		final = (format_extracted_name(name) or ms or "нет ответа").strip()
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
