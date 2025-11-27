import time, logging, hashlib
from aiogram import types
from aiogram.enums import ChatAction
from aiogram.types import (
	ReplyKeyboardMarkup,
	KeyboardButton,
	InlineKeyboardMarkup,
	InlineKeyboardButton,
)
from ..core.processor import process_query
from ..storage.greeted import GREETED_CHAT_IDS, save_greeted
from ..stats import record_request_stat
from ..utils.text import split_telegram_messages
from ..storage.users import get_or_create_uid_for_telegram, get_subscription_status
from .. import config

_CHAT_MODE: dict[int, str] = {}
_BUSY_CHATS: set[int] = set()
_MENU_MSG: dict[int, int] = {}

_REPLY_MENU = ReplyKeyboardMarkup(
	keyboard=[[KeyboardButton(text="Меню")]],
	resize_keyboard=True,
)

_INLINE_MAIN_MENU = InlineKeyboardMarkup(
	inline_keyboard=[
		[
			InlineKeyboardButton(text="Поиск", callback_data="menu_search"),
			InlineKeyboardButton(text="Мой профиль", callback_data="menu_profile"),
		]
	]
)

_INLINE_SEARCH_MENU = InlineKeyboardMarkup(
	inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu_back")]]
)


async def _send_main_menu(message: types.Message):
	chat_id = message.chat.id
	old_id = _MENU_MSG.get(chat_id)
	if old_id:
		try:
			await message.bot.delete_message(chat_id, old_id)
		except Exception:
			pass
	_CHAT_MODE[chat_id] = "main"
	text = (
		"Добро пожаловать в поисковую систему «Вектор».\n\n"
		"Мы помогаем превращать открытые источники в удобные данные для поиска и экспериментов.\n\n"
		"Выберите действие:"
	)
	sent = await message.answer(text, reply_markup=_INLINE_MAIN_MENU)
	_MENU_MSG[chat_id] = sent.message_id


async def _send_search_menu(message: types.Message, markdown_tip: bool = False):
	chat_id = message.chat.id
	old_id = _MENU_MSG.get(chat_id)
	if old_id:
		try:
			await message.bot.delete_message(chat_id, old_id)
		except Exception:
			pass
	_CHAT_MODE[chat_id] = "search"
	if markdown_tip:
		text = (
			"⬇️ Примеры запросов:\n\n"
			"👤 Поиск по должности\n"
			"├ Генеральный директор Газпрома\n"
			"├ Руководитель отдела продаж XYZ\n\n"
			"*Просто введите известные вам данные о человеке в похожем формате и отправьте их боту.*"
		)
		sent = await message.answer(
			text, reply_markup=_INLINE_SEARCH_MENU, parse_mode="Markdown"
		)
	else:
		text = (
			"⬇️ Примеры запросов:\n\n"
			"👤 Поиск по должности\n"
			"├ Генеральный директор Газпрома\n"
			"├ Руководитель отдела продаж XYZ\n\n"
			"Просто введите данные о человеке в похожем формате и отправьте их боту."
		)
		sent = await message.answer(text, reply_markup=_INLINE_SEARCH_MENU)
	_MENU_MSG[chat_id] = sent.message_id


async def cmd_start(message: types.Message):
	chat_id = message.chat.id
	if chat_id not in GREETED_CHAT_IDS:
		GREETED_CHAT_IDS.add(chat_id)
		try:
			await save_greeted(GREETED_CHAT_IDS)
		except Exception:
			pass
	_CHAT_MODE[chat_id] = "main"
	name = (message.from_user.full_name or "").strip() if message.from_user else ""
	if not name and message.from_user:
		name = (message.from_user.username or "").strip()
	await message.answer(
		"🔮 Постоянная ссылка на бота\n\n"
		"Актуальную ссылку на бота вы всегда найдёте на нашем сайте: https://example.com\n\n"
		"Сохраните её, чтобы не потерять доступ к боту даже в случае блокировок.",
		reply_markup=_REPLY_MENU,
	)
	greet = f"Привет, {name}!" if name else "Привет!"
	await message.answer(f"{greet}\n\nРады видеть вас в системе «Вектор».")
	await _send_main_menu(message)


async def cmd_help(message: types.Message):
	await _send_search_menu(message, markdown_tip=False)


async def cmd_id(message: types.Message):
	uid = await get_or_create_uid_for_telegram(message.chat.id)
	await message.answer(f"Ваш ID: `{uid}`", parse_mode="Markdown")


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


async def cb_menu_search(callback: types.CallbackQuery):
	if not callback.message:
		await callback.answer()
		return
	await _send_search_menu(callback.message, markdown_tip=True)
	await callback.answer()


async def cb_menu_profile(callback: types.CallbackQuery):
	if not callback.message:
		await callback.answer()
		return
	chat_id = callback.message.chat.id
	uid = await get_or_create_uid_for_telegram(chat_id)
	active, active_until = get_subscription_status(uid)
	if active_until > 0:
		ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(active_until))
	else:
		ts = "нет"
	if active:
		text = f"Ваш ID: `{uid}`\nПодписка активна до {ts}."
	else:
		text = f"Ваш ID: `{uid}`\nПодписка неактивна. Дата окончания: {ts}."
	await callback.message.answer(text, parse_mode="Markdown")
	await callback.answer()


async def cb_menu_back(callback: types.CallbackQuery):
	if not callback.message:
		await callback.answer()
		return
	await _send_main_menu(callback.message)
	await callback.answer()


async def _run_search(message: types.Message):
	t0 = time.monotonic()
	ok_flag = False
	ms_len = 0
	out_len = 0
	chat_id = message.chat.id
	if chat_id in _BUSY_CHATS:
		await message.answer(
			"Мы всё ещё обрабатываем ваш предыдущий запрос. Подождите немного и попробуйте снова."
		)
		return
	_BUSY_CHATS.add(chat_id)
	await message.bot.send_chat_action(chat_id, ChatAction.TYPING)
	status = await message.answer("Ищу")
	async def on_llm_start():
		try:
			await message.bot.send_chat_action(chat_id, ChatAction.TYPING)
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
		_BUSY_CHATS.discard(chat_id)
		try:
			await status.delete()
		except Exception:
			pass
	try:
		await record_request_stat({
			"ts": int(time.time()),
			"source": "tg",
			"chat_id": chat_id,
			"text_len": len(message.text or ""),
			"dur": round(max(0.0, time.monotonic() - t0), 1),
			"ok": ok_flag,
			"ms_len": ms_len,
			"tg_len": out_len,
		})
	except Exception:
		pass


async def handle_text(message: types.Message):
	chat_id = message.chat.id
	txt = (message.text or "").strip()
	if txt == "Меню":
		_CHAT_MODE[chat_id] = "main"
		await _send_main_menu(message)
		return
	if chat_id in _BUSY_CHATS:
		await message.answer(
			"Мы всё ещё обрабатываем ваш предыдущий запрос. Подождите немного и попробуйте снова."
		)
		return
	mode = _CHAT_MODE.get(chat_id) or "main"
	if mode == "search":
		await _run_search(message)
	else:
		if txt.startswith("/"):
			return
		await _send_main_menu(message)


