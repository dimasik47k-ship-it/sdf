import os
import asyncio
import logging
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
import aiohttp

# 🔐 Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔐 Загрузка конфига из окружения
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("❌ TOKEN not found in environment variables!")

ADMINS_STR = os.getenv("ADMINS", "")
ADMINS = {int(x.strip()) for x in ADMINS_STR.split(",") if x.strip().isdigit()}
if not ADMINS:
    raise RuntimeError("❌ ADMINS not found! Format: 123456789")

DB = "users.db"

# 🔐 Настройка сессии с таймаутами (важно для Render)
timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
session = AiohttpSession(timeout=timeout)
bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ────────────── FSM ──────────────
class Broadcast(StatesGroup):
    wait_msg = State()

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
            "<i>Ожидайте важные обновления!</i>"
        )

@dp.message(Command("ms"))
async def cmd_ms(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await m.answer(
        "📤 <b>Режим рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Отправьте сообщение боту.\n"
        "✅ Поддерживается: текст, стикеры, GIF, видео,\n"
        "   голосовые, альбомы, премиум-эмодзи."
    )
    await state.set_state(Broadcast.wait_msg)

@dp.message(Broadcast.wait_msg)
async def handle_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id not in ADMINS: return
    await state.clear()

    users = await db_get()
    if not users:
        return await m.answer("❌ <b>Нет подписчиков</b>")

    msgs = [m]
    if m.media_group_id:
        history = await bot.get_chat_history(m.chat.id, limit=20)
        msgs = sorted([x for x in history if x.media_group_id == m.media_group_id], key=lambda x: x.date)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить всем", callback_data="bc_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="bc_cancel")]
    ])

    await m.answer(
        f"📋 <b>Предпросмотр</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 Подписчиков: <code>{len(users)}</code>\n"
        f"📦 Сообщений: <code>{len(msgs)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Подтвердите отправку:",
        reply_markup=kb
    )
    await state.update_data(msgs=msgs, users=users, chat_id=m.chat.id)

@dp.callback_query(lambda c: c.data.startswith("bc_"))
async def process_callback(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()
    
    if c.data == "bc_cancel":
        await c.message.edit_text("🛑 <b>Отменено</b>")
        await state.clear()
        return

    msgs, users, chat_id = data.get("msgs"), data.get("users"), data.get("chat_id")
    if not msgs: return

    await c.message.edit_text("🚀 <b>Запуск...</b>\n━━━━━━━━━━━━━━━━━━\n⏳ Не закрывайте окно.")

    ok = block = fail = 0
    for uid in users:
        try:
            for msg in msgs:
                await bot.copy_message(uid, chat_id, msg.message_id)
                await asyncio.sleep(0.035)
            ok += 1
        except TelegramForbiddenError:
            block += 1
            await db_remove(uid)
        except (TelegramBadRequest, Exception):
            fail += 1

    await c.message.edit_text(
        f"✨ <b>Готово</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ Доставлено: <code>{ok}</code>\n"
        f"🚫 Заблокировали: <code>{block}</code>\n"
        f"⚠️ Ошибки: <code>{fail}</code>"
    )
    await state.clear()

# ────────────── Webhook Handlers ──────────────
async def health_handler(request):
    return web.Response(text="OK")

async def webhook_handler(request):
    try:
        body = await request.text()
        update = types.Update.model_validate_json(body)
        await dp.feed_webhook_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def on_startup(app):
    """Устанавливаем вебхук при старте"""
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        logger.warning("⚠️ RENDER_EXTERNAL_HOSTNAME not set, using localhost")
        hostname = "localhost"
    
    webhook_url = f"https://{hostname}/webhook"
    
    try:
        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
        logger.info(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ Failed to set webhook: {e}")

async def on_shutdown(app):
    """Удаляем вебхук при остановке"""
    try:
        await bot.delete_webhook()
        logger.info("✅ Webhook deleted")
    except Exception as e:
        logger.error(f"❌ Failed to delete webhook: {e}")

# ────────────── Main ──────────────
async def main():
    logger.info("🚀 Starting bot...")
    
    await db_init()
    logger.info("✅ Database initialized")
    
    # 🔥 ЯВНАЯ установка вебхука ПЕРЕД запуском сервера
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        # Fallback: попробуем получить из Render-метаданных или используем дефолт
        logger.warning("⚠️ RENDER_EXTERNAL_HOSTNAME not set, trying fallback...")
        hostname = os.getenv("HOSTNAME", "localhost")
    
    webhook_url = f"https://{hostname}/webhook"
    logger.info(f"🔗 Setting webhook to: {webhook_url}")
    
    try:
        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
        logger.info(f"✅ Webhook SET successfully: {webhook_url}")
    except Exception as e:
        logger.error(f"❌ CRITICAL: Failed to set webhook: {e}")
        # Не выходим, пусть сервер запустится — может, переменная подгрузится позже
    
    # Регистрация хендлеров (для корректного shutdown)
    dp.startup.register(on_startup)  # Можно оставить, но теперь это дубль
    dp.shutdown.register(on_shutdown)
    
    # Запуск веб-сервера
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_post("/webhook", webhook_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    logger.info(f"✅ Server listening on port {port}")
    logger.info(f"✅ Health check: https://{hostname}/health")
    
    # Держим процесс живым
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
