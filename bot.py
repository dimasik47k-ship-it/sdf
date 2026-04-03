import asyncio
import aiosqlite
import os
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError
from aiogram.enums import ContentType, ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
import html

# Включаем логирование, чтобы видеть возможные ошибки в консоли
logging.basicConfig(level=logging.INFO)

# 🔐 Безопасная загрузка конфига
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("❌ Переменная окружения TOKEN не найдена!")

ADMINS_STR = os.getenv("ADMINS", "")
ADMINS = {int(x.strip()) for x in ADMINS_STR.split(",") if x.strip().isdigit()}
if not ADMINS:
    raise RuntimeError("❌ Переменная окружения ADMINS не найдена! Формат: 123456789")

DB = "users.db"
# Добавляем DefaultBotProperties для парсинга HTML по умолчанию
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class Broadcast(StatesGroup):
    wait_msg = State()

# ────────────── БД ──────────────
async def db_init():
    async with aiosqlite.connect(DB) as db:
        # Создаем таблицу, если ее нет
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subs (
                uid INTEGER PRIMARY KEY
            )
        """)
        # Автоматическое обновление структуры БД (если осталась старая версия базы)
        try:
            await db.execute("ALTER TABLE subs ADD COLUMN first_name TEXT DEFAULT ''")
            await db.execute("ALTER TABLE subs ADD COLUMN last_name TEXT DEFAULT ''")
            await db.execute("ALTER TABLE subs ADD COLUMN username TEXT DEFAULT ''")
            await db.execute("ALTER TABLE subs ADD COLUMN language_code TEXT DEFAULT ''")
            await db.execute("ALTER TABLE subs ADD COLUMN is_premium BOOLEAN DEFAULT 0")
            await db.execute("ALTER TABLE subs ADD COLUMN is_bot BOOLEAN DEFAULT 0")
        except Exception:
            pass  # Если колонки уже есть, ошибка игнорируется
            
        await db.commit()

async def db_add_user(user: types.User):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT 1 FROM subs WHERE uid = ?", (user.id,))
        is_new = not await cur.fetchone()
        
        # Вставляем или обновляем данные пользователя
        await db.execute("""
            INSERT INTO subs (uid, first_name, last_name, username, language_code, is_premium, is_bot)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                username=excluded.username,
                language_code=excluded.language_code,
                is_premium=excluded.is_premium
        """, (
            user.id, user.first_name or "", user.last_name or "", 
            user.username or "", user.language_code or "", 
            user.is_premium or False, user.is_bot or False
        ))
        await db.commit()
        return is_new

async def db_get_users():
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM subs") as cur:
            return [dict(row) for row in await cur.fetchall()]

async def db_remove(uid: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM subs WHERE uid = ?", (uid,))
        await db.commit()

# ────────────── Утилиты для текста ──────────────
def build_placeholders(user_info: dict) -> dict:
    first_name = user_info.get("first_name", "Пользователь")
    last_name = user_info.get("last_name", "")
    username = user_info.get("username", "")
    uid = user_info.get("uid", 0)
    is_premium = bool(user_info.get("is_premium", False))
    is_bot = bool(user_info.get("is_bot", False))
    lang = user_info.get("language_code", "")
    full_name = f"{first_name} {last_name}".strip() if last_name else first_name
    
    if username:
        mention = f"@{username}"
    else:
        mention = f'<a href="tg://user?id={uid}">{html.escape(first_name)}</a>'
    
    return {
        "{name}": first_name,
        "{first_name}": first_name,
        "{name:lower}": first_name.lower(),
        "{last_name}": last_name,
        "{full_name}": full_name,
        "{username}": username,
        "{username_at}": f"@{username}" if username else "",
        "{id}": str(uid),
        "{chat_id}": str(uid),
        "{premium}": str(is_premium),
        "{premium_emoji}": "⭐" if is_premium else "☆",
        "{is_bot}": str(is_bot),
        "{mention}": mention,
        "{lang}": lang,
    }

def personalize_text(text: str, placeholders: dict) -> str:
    if not text:
        return text
    
    sorted_placeholders = sorted(placeholders.items(), key=lambda x: len(x[0]), reverse=True)
    for placeholder, value in sorted_placeholders:
        if placeholder == "{mention}":
            text = text.replace(placeholder, str(value))
        else:
            safe_value = html.escape(str(value))
            text = text.replace(placeholder, safe_value)
    return text

ALL_PLACEHOLDERS = ["{name}", "{first_name}", "{last_name}", "{full_name}", "{username}", 
                    "{username_at}", "{id}", "{chat_id}", "{premium}", "{premium_emoji}", 
                    "{is_bot}", "{mention}", "{lang}"]

def has_placeholders(text: str) -> bool:
    if not text: return False
    return any(ph in text for ph in ALL_PLACEHOLDERS)


# ────────────── Хендлеры ──────────────
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    is_new = await db_add_user(m.from_user)
    if is_new:
        await m.answer(
            "✨ <b>Добро пожаловать!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📩 Вы подписаны на рассылку.\n"
            "🔔 Уведомления будут приходить сюда."
        )
    else:
        await m.answer("✅ Данные обновлены. Вы уже подписаны на рассылку.")

@dp.message(Command("ms"))
async def cmd_ms(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await m.answer(
        "📤 <b>Режим рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте сообщение боту."
    )
    await state.set_state(Broadcast.wait_msg)

@dp.message(Broadcast.wait_msg)
async def handle_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await state.clear()

    users = await db_get_users()
    if not users:
        return await m.answer("❌ <b>Нет подписчиков.</b> Отправьте /start чтобы подписаться.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data="bc_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel")]
    ])

    await m.answer(
        f"📋 <b>Предпросмотр рассылки</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 Подписчиков: <code>{len(users)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Подтвердите отправку:",
        reply_markup=kb
    )
    
    # 💡 НОВЫЙ МЕХАНИЗМ: Сохраняем все нужные данные сразу, без пересылки
    original_html_text = m.html_text or ""
    needs_personalization = has_placeholders(original_html_text)
    
    # Извлекаем ID файла медиа, если оно есть
    file_id = None
    if m.photo: file_id = m.photo[-1].file_id
    elif m.video: file_id = m.video.file_id
    elif m.document: file_id = m.document.file_id
    elif m.animation: file_id = m.animation.file_id
    elif m.audio: file_id = m.audio.file_id

    await state.update_data(
        msg_id=m.message_id,
        chat_id=m.chat.id,
        users=users,
        needs_personalization=needs_personalization,
        original_html_text=original_html_text,
        content_type=m.content_type.value if hasattr(m.content_type, 'value') else m.content_type,
        file_id=file_id
    )

@dp.callback_query(F.data.startswith("bc_"))
async def process_callback(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()
    
    if c.data == "bc_cancel":
        await c.message.edit_text("🛑 <b>Рассылка отменена</b>")
        await state.clear()
        return

    msg_id = data.get("msg_id")
    chat_id = data.get("chat_id")
    users = data.get("users")
    needs_personalization = data.get("needs_personalization")
    original_html_text = data.get("original_html_text")
    content_type = data.get("content_type")
    file_id = data.get("file_id")

    if not users:
        await c.message.edit_text("❌ Ошибка: данные утеряны, начните заново.")
        return

    await c.message.edit_text("🚀 <b>Запуск...</b>\n━━━━━━━━━━━━━━━━━━\n⏳ Не закрывайте окно.")

    ok = block = fail = 0
    
    for user in users:
        uid = user["uid"]
        try:
            # 1. Если нет плейсхолдеров, просто копируем 1 в 1
            if not needs_personalization:
                await bot.copy_message(chat_id=uid, from_chat_id=chat_id, message_id=msg_id)
            
            # 2. Если есть переменные — подставляем и отправляем конкретный тип медиа
            else:
                placeholders = build_placeholders(user)
                final_text = personalize_text(original_html_text, placeholders)

                if content_type == ContentType.TEXT:
                    await bot.send_message(uid, final_text)
                elif content_type == ContentType.PHOTO:
                    await bot.send_photo(uid, file_id, caption=final_text)
                elif content_type == ContentType.VIDEO:
                    await bot.send_video(uid, file_id, caption=final_text)
                elif content_type == ContentType.DOCUMENT:
                    await bot.send_document(uid, file_id, caption=final_text)
                elif content_type == ContentType.ANIMATION:
                    await bot.send_animation(uid, file_id, caption=final_text)
                elif content_type == ContentType.AUDIO:
                    await bot.send_audio(uid, file_id, caption=final_text)
                else:
                    # Фоллбэк (кружки/голосовые не поддерживают текст с переменными)
                    await bot.copy_message(chat_id=uid, from_chat_id=chat_id, message_id=msg_id)

            ok += 1
            await asyncio.sleep(0.04) # Безопасная пауза от блокировки
            
        except TelegramForbiddenError:
            block += 1
            await db_remove(uid)
        except Exception as e:
            logging.error(f"Ошибка отправки {uid}: {e}")
            fail += 1

    await c.message.edit_text(
        "✨ <b>Рассылка завершена</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"✅ Доставлено: <code>{ok}</code>\n"
        f"🚫 Заблокировали бота: <code>{block}</code>\n"
        f"⚠️ Ошибки: <code>{fail}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Готово к следующей рассылке</i>"
    )
    await state.clear()

# ────────────── Webhook и запуск (Render-Ready) ──────────────
async def health_handler(request):
    return web.Response(text="OK")

async def webhook_handler(request: web.Request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data, context={"bot": bot})
        await dp.feed_webhook_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"❌ Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def on_startup(app):
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        logging.error("❌ RENDER_EXTERNAL_HOSTNAME не найден!")
        return
    
    webhook_url = f"https://{hostname}/webhook"
    
    await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types()
    )
    logging.info(f"✅ Webhook set: {webhook_url}")

async def on_shutdown(app):
    await bot.delete_webhook()
    logging.info("✅ Webhook deleted")

async def main():
    await db_init()

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_shutdown)
    
    app.router.add_get("/health", health_handler)      
    app.router.add_post("/webhook", webhook_handler)   

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    logging.info(f"🚀 Bot started on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
