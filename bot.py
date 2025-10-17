# bot.py
import os
import asyncio
import logging
from pathlib import Path
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardButton,
    FSInputFile,
    BotCommand,
    InputMediaPhoto,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router
from PIL import Image, ImageOps
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer  # для Render keep_alive

# -----------------------
# Налаштування
# -----------------------

CONTACT_TEXT = "Зв'язок: @stEgno_lamps"

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не знайдено! Додай його у Variables на сайті Railway або Render.")

CATALOG = [
    {
        "id": "prod1",
        "name": "Місяць фосфорисцентний (світиться в темряві) зелений",
        "price": 600,
        "photos": ["images/moon_green.jpg", "images/moon_green_2.jpg"],
    },
    {
        "id": "prod2",
        "name": "Місяць фосфорисцентний (світиться в темряві) синій",
        "price": 600,
        "photos": ["images/moon_blue_1.jpg", "images/moon_blue_2.jpg"],
    },
    {
        "id": "prod3",
        "name": "Місяць звичайний білий",
        "price": 550,
        "photos": ["images/moon_white_1.jpg", "images/moon_white_2.jpg"],
    },
    {
        "id": "prod4",
        "name": "Фоторамка",
        "price": "ціна договірна",
        "photos": ["images/20250916_213350.jpg"],
    },
]

BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# -----------------------
# Ініціалізація
# -----------------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()

# -----------------------
# Клавіатури
# -----------------------

def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Каталог", callback_data="catalog")
    kb.button(text="📞 Контакти", callback_data="contacts")
    kb.adjust(1)
    return kb.as_markup()

def catalog_kb():
    kb = InlineKeyboardBuilder()
    for item in CATALOG:
        kb.button(text=item["name"], callback_data=f"show:{item['id']}")
    kb.adjust(1)
    return kb.as_markup()

def photo_nav_kb(prod_id: str, current_idx: int, total: int):
    prev_idx = (current_idx - 1) % total
    next_idx = (current_idx + 1) % total
    kb = InlineKeyboardBuilder()
    kb.button(text="◀", callback_data=f"p:{prod_id}:{prev_idx}")
    kb.button(text=f"{current_idx+1}/{total}", callback_data="noop")
    kb.button(text="▶", callback_data=f"p:{prod_id}:{next_idx}")
    kb.adjust(3)
    kb.button(text="⬅️ Назад у каталог", callback_data="catalog")
    kb.adjust(3, 1)
    return kb.as_markup()

# -----------------------
# Обробка фото
# -----------------------

MAX_DIM = 4096
MAX_BYTES = 9_500_000

def prepare_photo_for_telegram(local_path: Path) -> FSInputFile:
    img = Image.open(local_path)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)

    out_path = TMP_DIR / f"{local_path.stem}_tg.jpg"
    for q in (90, 85, 80, 75, 70, 65, 60):
        img.save(out_path, format="JPEG", quality=q, optimize=True, progressive=True)
        if out_path.stat().st_size <= MAX_BYTES:
            break
    return FSInputFile(out_path)

def to_list_photos(photos_field) -> list[str]:
    if photos_field is None:
        return []
    if isinstance(photos_field, str):
        return [photos_field]
    if isinstance(photos_field, list):
        return photos_field
    return [str(photos_field)]

def resolve_photo_source(photo_field: str):
    p = (BASE_DIR / Path(photo_field)).resolve()
    if p.exists() and p.is_file():
        return prepare_photo_for_telegram(p)
    return photo_field

def get_product(prod_id: str):
    return next((p for p in CATALOG if p["id"] == prod_id), None)

# -----------------------
# Обробники
# -----------------------

@router.message(CommandStart())
async def on_start(message: Message):
    await message.answer("Вітаю! Оберіть дію 👇", reply_markup=main_menu())

@router.message(Command("catalog"))
async def on_catalog_cmd(message: Message):
    await message.answer("Наші товари 👇", reply_markup=catalog_kb())

@router.callback_query(F.data == "contacts")
async def on_contacts(callback: types.CallbackQuery):
    await callback.message.answer(CONTACT_TEXT)
    await callback.answer()

@router.callback_query(F.data == "catalog")
async def on_catalog(callback: types.CallbackQuery):
    await callback.message.answer("Наші товари 👇", reply_markup=catalog_kb())
    await callback.answer()

@router.callback_query(F.data.startswith("show:"))
async def on_show_product(callback: types.CallbackQuery):
    prod_id = callback.data.split(":", 1)[1]
    product = get_product(prod_id)
    if not product:
        await callback.message.answer("Товар не знайдено.")
        await callback.answer()
        return

    name = product["name"]
    price = product["price"]
    photos = to_list_photos(product.get("photos"))
    text = f"<b>{name}</b>\nЦіна: {price}"

    if photos:
        try:
            photo_obj = resolve_photo_source(photos[0])
            await callback.message.answer_photo(
                photo=photo_obj,
                caption=text,
                reply_markup=photo_nav_kb(prod_id, 0, len(photos)),
            )
        except Exception as e:
            logging.exception(f"Не вдалось відправити фото для '{name}': {e}")
            await callback.message.answer(text, reply_markup=photo_nav_kb(prod_id, 0, len(photos)))
    else:
        await callback.message.answer(text)
    await callback.answer()

@router.callback_query(F.data.startswith("p:"))
async def on_photo_nav(callback: types.CallbackQuery):
    try:
        _, prod_id, idx_str = callback.data.split(":")
        target_idx = int(idx_str)
    except Exception:
        await callback.answer()
        return

    product = get_product(prod_id)
    if not product:
        await callback.answer("Товар не знайдено")
        return

    name = product["name"]
    price = product["price"]
    photos = to_list_photos(product.get("photos"))
    total = len(photos)
    if total == 0:
        await callback.answer("Немає фото")
        return

    target_idx %= total
    text = f"<b>{name}</b>\nЦіна: {price}"

    try:
        photo_obj = resolve_photo_source(photos[target_idx])
        media = InputMediaPhoto(media=photo_obj, caption=text, parse_mode="HTML")
        await callback.message.edit_media(media=media, reply_markup=photo_nav_kb(prod_id, target_idx, total))
    except Exception as e:
        logging.exception(f"Не вдалось перемкнути фото для '{name}': {e}")
        await callback.message.answer_photo(
            photo=resolve_photo_source(photos[target_idx]),
            caption=text,
            reply_markup=photo_nav_kb(prod_id, target_idx, total),
        )

    await callback.answer(f"Фото {target_idx+1}/{total}")

@router.message()
async def on_any_message(message: Message):
    await message.answer("Оберіть дію 👇", reply_markup=main_menu())

# -----------------------
# Keep-alive для Render
# -----------------------

def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever).start()

# -----------------------
# Запуск
# -----------------------

async def main():
    keep_alive()  # <-- для Render Free plan, не впливає на Railway
    await bot.set_my_commands([
        BotCommand(command="start", description="Почати"),
        BotCommand(command="catalog", description="Показати каталог"),
    ])
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
