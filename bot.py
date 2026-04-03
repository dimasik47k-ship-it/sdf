import asyncio
import aiosqlite
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.enums import ContentType
from aiohttp import web
import html

# 🔐 Безопасная загрузка конфига из переменных окружения
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("❌ Переменная окружения TOKEN не найдена!")

ADMINS_STR = os.getenv("ADMINS", "")
ADMINS = {int(x.strip()) for x in ADMINS_STR.split(",") if x.strip().isdigit()}
if not ADMINS:
    raise RuntimeError("❌ Переменная окружения ADMINS не найдена! Формат: 123456789")

DB = "users.db"
bot = Bot(token=TOKEN)
dp = Dispatcher()

class Broadcast(StatesGroup):
    wait_msg = State()

# ────────────── Утилиты ──────────────
def build_placeholders(chat_info: dict) -> dict:
    """
    Строит словарь всех плейсхолдеров из информации о пользователе.
    
    Доступные плейсхолдеры:
    {name} / {first_name} — имя пользователя (с заглавной первой буквой)
    {last_name} — фамилия (если есть)
    {full_name} — полное имя (имя + фамилия)
    {username} — юзернейм (без @)
    {username_at} — юзернейм с @
    {id} — Telegram ID пользователя
    {name:lower} / {first_name:lower} — имя строчными
    {name:upper} / {first_name:upper} — имя заглавными
    {last_name:lower} — фамилия строчными
    {last_name:upper} — фамилия заглавными
    {full_name:lower} — полное имя строчными
    {full_name:upper} — полное имя заглавными
    {premium} — True/False (подписка Telegram Premium)
    {premium_emoji} — ⭐ / ☆ (иконка премиума)
    {premium_badge} — 🌟 / "" (звёздочка для рассылки)
    {is_bot} — True/False (является ли ботом)
    {chat_id} — ID чата (совпадает с id для личных сообщений)
    {chat_type} — private / public
    {mention} — упоминание (через @ или имя)
    {lang} — языковой код пользователя
    """
    first_name = chat_info.get("first_name", "Пользователь")
    last_name = chat_info.get("last_name", "")
    username = chat_info.get("username", "")
    uid = chat_info.get("id", 0)
    is_premium = chat_info.get("is_premium", False)
    is_bot = chat_info.get("is_bot", False)
    lang = chat_info.get("language_code", "")
    full_name = f"{first_name} {last_name}".strip() if last_name else first_name
    
    # Определяем тип чата
    chat_type = chat_info.get("chat_type", "private")
    chat_type_display = "private" if chat_type == "private" else "public"
    
    # Mention
    if username:
        mention = f"@{username}"
    else:
        mention = first_name
    
    return {
        # Имя
        "{name}": first_name,
        "{first_name}": first_name,
        "{name:lower}": first_name.lower(),
        "{first_name:lower}": first_name.lower(),
        "{name:upper}": first_name.upper(),
        "{first_name:upper}": first_name.upper(),
        
        # Фамилия
        "{last_name}": last_name,
        "{last_name:lower}": last_name.lower(),
        "{last_name:upper}": last_name.upper(),
        
        # Полное имя
        "{full_name}": full_name,
        "{full_name:lower}": full_name.lower(),
        "{full_name:upper}": full_name.upper(),
        
        # Юзернейм
        "{username}": username,
        "{username_at}": f"@{username}" if username else "",
        
        # ID
        "{id}": str(uid),
        "{chat_id}": str(uid),
        
        # Премиум
        "{premium}": str(is_premium),
        "{premium_emoji}": "⭐" if is_premium else "☆",
        "{premium_badge}": "🌟" if is_premium else "",
        
        # Бот
        "{is_bot}": str(is_bot),
        
        # Тип чата
        "{chat_type}": chat_type_display,
        
        # Упоминание
        "{mention}": mention,
        
        # Язык
        "{lang}": lang,
    }




def personalize_text(text: str, placeholders: dict) -> str:
    """
    Заменяет все плейсхолдеры в тексте на значения.
    1. Сортируем ключи по длине (убывание), чтобы {name:lower} заменился раньше {name}.
    2. Экранируем значения для безопасной вставки в HTML.
    """
    if not text:
        return text
        
    # Сортировка: сначала длинные плейсхолдеры
    sorted_placeholders = sorted(placeholders.items(), key=lambda x: len(x[0]), reverse=True)
    
    for placeholder, value in sorted_placeholders:
        # Экранируем HTML-символы в значении, чтобы не сломать разметку
        safe_value = html.escape(str(value))
        text = text.replace(placeholder, safe_value)
    return text


async def get_user_info(bot: Bot, uid: int) -> dict:
    """Получает полнуюбую информацию о пользователе."""
    try:
        chat = await bot.get_chat(uid)
        return {
            "id": chat.id,
            "first_name": chat.first_name or "",
            "last_name": chat.last_name or "",
            "username": chat.username or "",
            "is_premium": chat.is_premium_user or False,
            "is_bot": chat.is_bot or False,
            "language_code": chat.language_code or "",
            "chat_type": chat.type or "private",
        }
    except Exception:
        return {
            "id": uid,
            "first_name": "Пользователь",
            "last_name": "",
            "username": "",
            "is_premium": False,
            "is_bot": False,
            "language_code": "",
            "chat_type": "private",
        }


ALL_PLACEHOLDERS = [
    "{name}", "{first_name}", "{name:lower}", "{first_name:lower}",
    "{name:upper}", "{first_name:upper}", "{last_name}", "{last_name:lower}",
    "{last_name:upper}", "{full_name}", "{full_name:lower}", "{full_name:upper}",
    "{username}", "{username_at}", "{id}", "{chat_id}", "{premium}",
    "{premium_emoji}", "{premium_badge}", "{is_bot}", "{chat_type}",
    "{mention}", "{lang}",
]


def has_placeholders(text: str) -> bool:
    """Проверяет, есть ли в тексте плейсхолдеры."""
    if not text:
        return False
    return any(ph in text for ph in ALL_PLACEHOLDERS)

# ────────────── БД ──────────────
async def db_init():
    async with aiosqlite.connect(DB) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS subs (uid INTEGER PRIMARY KEY)")
        await db.commit()

async def db_add_if_new(uid):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT 1 FROM subs WHERE uid = ?", (uid,))
        if await cur.fetchone(): return False
        await db.execute("INSERT INTO subs VALUES (?)", (uid,))
        await db.commit()
        return True

async def db_get():
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT uid FROM subs") as cur:
            return [r[0] for r in await cur.fetchall()]

async def db_remove(uid):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM subs WHERE uid = ?", (uid,))
        await db.commit()

# ────────────── Хендлеры ──────────────
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    if await db_add_if_new(m.from_user.id):
        await m.answer(
            "✨ <b>Добро пожаловать!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📩 Вы подписаны на рассылку.\n"
            "🔔 Уведомления будут приходить сюда.\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Ожидайте важные обновления!</i>",
            parse_mode="HTML"
        )

@dp.message(Command("ms"))
async def cmd_ms(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await m.answer(
        "📤 <b>Режим рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте сообщение боту.\n"
        "✅ Поддерживается: текст, стикеры, GIF, видео,\n"
        "   голосовые, альбомы, премиум-эмодзи.",
        parse_mode="HTML"
    )
    await state.set_state(Broadcast.wait_msg)

@dp.message(Broadcast.wait_msg)
async def handle_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await state.clear()

    users = await db_get()
    if not users:
        return await m.answer("❌ <b>Нет подписчиков</b>", parse_mode="HTML")

    msgs = [m]
    if m.media_group_id:
        history = await bot.get_chat_history(m.chat.id, limit=20)
        msgs = sorted([x for x in history if x.media_group_id == m.media_group_id], key=lambda x: x.date)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data="bc_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel")]
    ])

    await m.answer(
        f"📋 <b>Предпросмотр рассылки</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 Подписчиков: <code>{len(users)}</code>\n"
        f"📦 Сообщений: <code>{len(msgs)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Подтвердите отправку:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.update_data(msgs=msgs, users=users, chat_id=m.chat.id)

@dp.callback_query(lambda c: c.data.startswith("bc_"))
async def process_callback(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()
    
    if c.data == "bc_cancel":
        await c.message.edit_text("🛑 <b>Рассылка отменена</b>", parse_mode="HTML")
        await state.clear()
        return

    msgs, users, chat_id = data.get("msgs"), data.get("users"), data.get("chat_id")
    if not msgs: return

    await c.message.edit_text(
        "🚀 <b>Запуск...</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⏳ Не закрывайте окно. Это займёт время."
    )

    ok = block = fail = 0
    
    # Проверяем, есть ли плейсхолдеры в сообщениях
    needs_personalization = any(
        has_placeholders(msg.text) or has_placeholders(msg.caption)
        for msg in msgs
    )
    
        # ... (код до цикла for uid in users) ...
    
    for uid in users:
        try:
            # Получаем инфо пользователя только если есть плейсхолдеры
            placeholders = {}
            if needs_personalization:
                user_info = await get_user_info(bot, uid)
                placeholders = build_placeholders(user_info)
            
            # Определяем, является ли текущая пачка сообщений медиа-группой
            is_media_group = len(msgs) > 1 and msgs[0].media_group_id
            
            # Если это альбом, готовим группу для отправки (aiogram 3.x)
            if is_media_group and needs_personalization:
                from aiogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio, InputMediaDocument
                media_group = []
                
                for msg in msgs:
                    original_text = msg.text or msg.caption or ""
                    new_caption = personalize_text(original_text, placeholders) if has_placeholders(original_text) else original_text
                    
                    # Формируем объекты InputMedia* с новыми подписями
                    if msg.content_type == ContentType.PHOTO:
                        media_group.append(InputMediaPhoto(media=msg.photo[-1].file_id, caption=new_caption, parse_mode="HTML"))
                    elif msg.content_type == ContentType.VIDEO:
                        media_group.append(InputMediaVideo(media=msg.video.file_id, caption=new_caption, parse_mode="HTML"))
                    elif msg.content_type == ContentType.DOCUMENT:
                        media_group.append(InputMediaDocument(media=msg.document.file_id, caption=new_caption, parse_mode="HTML"))
                    elif msg.content_type == ContentType.AUDIO:
                        media_group.append(InputMediaAudio(media=msg.audio.file_id, caption=new_caption, parse_mode="HTML"))
                    else:
                        # Если тип не поддерживается в альбоме, отправим отдельно ниже
                        media_group = None 
                        break
                
                if media_group:
                    try:
                        await bot.send_media_group(uid, media_group)
                        await asyncio.sleep(0.035)
                        continue # Переходим к следующему пользователю
                    except Exception as e:
                        print(f"Error sending media group to {uid}: {e}")
                        # Фоллбэк: отправлять по одному, если группа не прошла

            # --- Стандартная отправка (одиночные сообщения или фоллбэк) ---
            for msg in msgs:
                original_text = msg.text or msg.caption or ""
                
                # Определяем финальный текст и parse_mode
                if needs_personalization and has_placeholders(original_text):
                    final_text = personalize_text(original_text, placeholders)
                    parse_mode = "HTML"
                else:
                    # Если персонализация не нужна, копируем как есть
                    final_text = original_text
                    parse_mode = msg.parse_mode if hasattr(msg, 'parse_mode') else None

                try:
                    if msg.content_type == ContentType.TEXT:
                        await bot.send_message(uid, final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.PHOTO:
                        await bot.send_photo(uid, msg.photo[-1].file_id, caption=final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.VIDEO:
                        await bot.send_video(uid, msg.video.file_id, caption=final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.DOCUMENT:
                        await bot.send_document(uid, msg.document.file_id, caption=final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.AUDIO:
                        await bot.send_audio(uid, msg.audio.file_id, caption=final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.ANIMATION: # GIF
                        await bot.send_animation(uid, msg.animation.file_id, caption=final_text, parse_mode=parse_mode)
                    
                    elif msg.content_type == ContentType.VOICE:
                        # Голосовые не поддерживают подписи с HTML, отправляем как есть
                        await bot.send_voice(uid, msg.voice.file_id)
                    
                    elif msg.content_type == ContentType.VIDEO_NOTE:
                        await bot.send_video_note(uid, msg.video_note.file_id)
                    
                    elif msg.content_type == ContentType.STICKER:
                        await bot.send_sticker(uid, msg.sticker.file_id)
                    
                    else:
                        # Для всех остальных типов (контакты, геометрия и т.д.) 
                        # персонализация текста невозможна через API, используем copy
                        await bot.copy_message(uid, chat_id, msg.message_id)
                    
                    # Небольшая задержка, чтобы не словить лимиты (429)
                    await asyncio.sleep(0.035)
                    
                except TelegramBadRequest as e:
                    # Если HTML не валиден (редкий кейс), пробуем отправить без парсинга
                    if "can't parse" in str(e).lower() and parse_mode:
                        if msg.content_type == ContentType.PHOTO:
                            await bot.send_photo(uid, msg.photo[-1].file_id, caption=final_text)
                        elif msg.content_type == ContentType.VIDEO:
                            await bot.send_video(uid, msg.video.file_id, caption=final_text)
                        elif msg.content_type == ContentType.TEXT:
                            await bot.send_message(uid, final_text)
                        else:
                            await bot.copy_message(uid, chat_id, msg.message_id)
                    else:
                        raise
                        
            ok += 1
            
        except TelegramForbiddenError:
            block += 1
            await db_remove(uid)
        except Exception as e:
            print(f"Error sending to {uid}: {e}")
            fail += 1

    await c.message.edit_text(
        "✨ <b>Рассылка завершена</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"✅ Доставлено: <code>{ok}</code>\n"
        f"🚫 Заблокировали: <code>{block}</code>\n"
        f"⚠️ Ошибки: <code>{fail}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Готово к следующей рассылке</i>",
        parse_mode="HTML"
    )
    await state.clear()

# ────────────── Запуск (Render-Ready) ──────────────
async def health_handler(request):
    return web.Response(text="OK")

async def webhook_handler(request: web.Request):
    """Обработка входящих обновлений от Telegram"""
    try:
        # 1. Получаем данные как словарь (dict)
        data = await request.json()
        # 2. Валидируем в объект aiogram
        update = types.Update.model_validate(data)
        # 3. Передаём в диспетчер
        await dp.feed_webhook_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def on_startup(app):
    """Настройка вебхука при запуске"""
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        # ❗ Лучше упасть с ошибкой, чем ставить вебхук на localhost
        raise RuntimeError("❌ RENDER_EXTERNAL_HOSTNAME не найден! Бот не может настроить вебхук.")
    
    webhook_url = f"https://{hostname}/webhook"
    
    await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types()
    )
    print(f"✅ Webhook set: {webhook_url}")

async def on_shutdown(app):
    """Очистка вебхука при остановке"""
    await bot.delete_webhook()
    print("✅ Webhook deleted")

async def main():
    await db_init()

    # Регистрация хендлеров жизненного цикла
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Настройка aiohttp приложения
    app = web.Application()
    app.router.add_get("/health", health_handler)      # Для Render/UptimeRobot
    app.router.add_post("/webhook", webhook_handler)   # Для Telegram

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()

    print("🚀 Bot started (webhook mode)")
    
    # Держим процесс живым, пока не придёт сигнал остановки
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
