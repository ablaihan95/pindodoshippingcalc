"""Telegram bot that compares cargo and postal shipping costs."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)

CALCULATE_BUTTON = "Рассчитать"
CURRENCY_BUTTON = "Изменить валюту цены"

POSTAL_RATE = Decimal("0.22")
CARGO_RATE_UP_TO_10_KG = Decimal("3.9")
CARGO_RATE_OVER_10_KG = Decimal("2.9")
RATE_FALLBACK = Decimal(os.getenv("USD_RATE_FALLBACK", "450"))
CNY_RATE_FALLBACK = Decimal(os.getenv("CNY_RATE_FALLBACK", "60"))
# The workbook uses today's USD rate for choosing the shipping method, but
# the total-price formula uses this fixed exchange rate for cargo.
CARGO_TOTAL_USD_RATE = Decimal(os.getenv("CARGO_TOTAL_USD_RATE", "444.76"))
RATE_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={date}"
TIMEZONE = ZoneInfo("Asia/Almaty")


@dataclass(frozen=True)
class Quote:
    option: str
    total: Decimal
    shipping_cost: Decimal
    postal_cost: Decimal
    cargo_cost_for_choice: Decimal
    usd_rate: Decimal
    rate_is_fallback: bool


def get_currency_rate(currency: str, fallback: Decimal) -> tuple[Decimal, bool]:
    """Fetch a currency rate from the National Bank, with a fallback value."""
    date = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    try:
        response = requests.get(RATE_URL.format(date=date), timeout=8)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip().upper()
            if title == currency:
                raw_rate = item.findtext("description", "").strip()
                rate = Decimal(raw_rate.replace(",", "."))
                if rate > 0:
                    return rate, False
        raise ValueError(f"{currency} rate was not present in the response")
    except (requests.RequestException, ET.ParseError, InvalidOperation, ValueError) as exc:
        logger.warning("Could not fetch %s rate; using %s: %s", currency, fallback, exc)
        return fallback, True


def get_usd_rate() -> tuple[Decimal, bool]:
    """Fetch the USD rate used by the spreadsheet's shipping comparison."""
    return get_currency_rate("USD", RATE_FALLBACK)


def calculate(price: Decimal, weight: Decimal) -> Quote:
    """Apply the workbook's comparison and total-price formulas."""
    usd_rate, is_fallback = get_usd_rate()
    cargo_rate = CARGO_RATE_OVER_10_KG if weight > 10 else CARGO_RATE_UP_TO_10_KG
    postal_cost = price * POSTAL_RATE
    cargo_cost_for_choice = weight * cargo_rate * usd_rate

    if postal_cost > cargo_cost_for_choice:
        option = "КАРГО"
        shipping_cost = weight * cargo_rate * CARGO_TOTAL_USD_RATE
        total = price + shipping_cost
    else:
        option = "ПОЧТА"
        shipping_cost = postal_cost
        total = price + postal_cost

    return Quote(
        option=option,
        total=total,
        shipping_cost=shipping_cost,
        postal_cost=postal_cost,
        cargo_cost_for_choice=cargo_cost_for_choice,
        usd_rate=usd_rate,
        rate_is_fallback=is_fallback,
    )


def parse_amount(text: str) -> Decimal | None:
    """Accept positive numbers with either a dot or comma decimal separator."""
    normalized = text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return None
    try:
        amount = Decimal(normalized)
        return amount if amount > 0 else None
    except InvalidOperation:
        return None


def money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}".replace(",", " ")


def calculate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(CALCULATE_BUTTON, callback_data="calculate")],
            [InlineKeyboardButton(CURRENCY_BUTTON, callback_data="choose_currency")],
        ]
    )


def currency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Тенге (₸)", callback_data="currency_kzt")],
            [InlineKeyboardButton("Юань (¥)", callback_data="currency_cny")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    currency = context.user_data.get("currency", "KZT")
    context.user_data.clear()
    context.user_data["currency"] = currency
    await update.message.reply_text(
        "Привет! Я помогу выбрать между доставкой карго и почтой.\n"
        "Нажмите кнопку, чтобы начать расчёт.",
        reply_markup=calculate_keyboard(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    currency = context.user_data.get("currency", "KZT")
    context.user_data.clear()
    context.user_data["currency"] = currency
    await update.message.reply_text(
        "Расчёт отменён.", reply_markup=calculate_keyboard()
    )


async def begin_calculation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    currency = context.user_data.get("currency", "KZT")
    context.user_data.clear()
    context.user_data["currency"] = currency
    context.user_data["awaiting"] = "price"
    currency_name = "юанях" if currency == "CNY" else "тенге"
    await query.message.reply_text(f"Введите цену товара в {currency_name}:")


async def choose_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    await query.message.reply_text("Выберите валюту цены товара:", reply_markup=currency_keyboard())


async def set_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    currency = "CNY" if query.data == "currency_cny" else "KZT"
    context.user_data.clear()
    context.user_data["currency"] = currency
    currency_name = "юань" if currency == "CNY" else "тенге"
    await query.message.reply_text(
        f"Валюта цены товара: {currency_name}.", reply_markup=calculate_keyboard()
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    stage = context.user_data.get("awaiting")
    if stage not in {"price", "weight"}:
        await update.message.reply_text(
            "Нажмите «Рассчитать», чтобы начать.", reply_markup=calculate_keyboard()
        )
        return

    amount = parse_amount(update.message.text)
    if amount is None:
        currency = context.user_data.get("currency", "KZT")
        label = (
            "цену товара в юанях"
            if stage == "price" and currency == "CNY"
            else "цену товара в тенге"
            if stage == "price"
            else "вес в килограммах"
        )
        await update.message.reply_text(
            f"Введите положительное число: {label}. Например: 12500 или 2,5."
        )
        return

    if stage == "price":
        context.user_data["price"] = str(amount)
        context.user_data["awaiting"] = "weight"
        await update.message.reply_text("Введите вес заказа в килограммах:")
        return

    entered_price = Decimal(context.user_data["price"])
    currency = context.user_data.get("currency", "KZT")
    weight = amount
    cny_rate = None
    cny_rate_is_fallback = False
    if currency == "CNY":
        cny_rate, cny_rate_is_fallback = get_currency_rate("CNY", CNY_RATE_FALLBACK)
        price = entered_price * cny_rate
    else:
        price = entered_price
    context.user_data.clear()
    context.user_data["currency"] = currency
    quote = calculate(price, weight)
    rate_note = " (резервный курс)" if quote.rate_is_fallback else ""
    entered_price_line = (
        f"Цена товара: {money(entered_price)} ¥\n"
        f"Цена в тенге: {money(price)} ₸\n"
        f"Курс CNY: {money(cny_rate)} ₸"
        f"{' (резервный курс)' if cny_rate_is_fallback else ''}\n"
        if currency == "CNY"
        else f"Цена товара: {money(price)} ₸\n"
    )
    await update.message.reply_text(
        f"Рекомендуемый вариант: {quote.option}\n"
        f"{entered_price_line}"
        f"Стоимость доставки: {money(quote.shipping_cost)} ₸\n"
        f"Итого: {money(quote.total)} ₸\n\n"
        f"Сравнение: почта — {money(quote.postal_cost)} ₸, "
        f"карго — {money(quote.cargo_cost_for_choice)} ₸\n"
        f"Курс USD для сравнения: {money(quote.usd_rate)} ₸{rate_note}",
        reply_markup=calculate_keyboard(),
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Перед запуском задайте переменную окружения TELEGRAM_BOT_TOKEN.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(begin_calculation, pattern="^calculate$"))
    application.add_handler(CallbackQueryHandler(choose_currency, pattern="^choose_currency$"))
    application.add_handler(CallbackQueryHandler(set_currency, pattern="^currency_(kzt|cny)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling()


if __name__ == "__main__":
    main()
