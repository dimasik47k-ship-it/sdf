import asyncio
import aiosqlite
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiohttp import web

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

async def main():
    await db_init()

    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())