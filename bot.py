import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    LabeledPrice,
    PreCheckoutQuery,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

import db
from config import BOT_TOKEN, PLANS, ADMIN_IDS, MIN_STARS, RUB_PER_STAR

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

router = Router()


class ReviewStates(StatesGroup):
    waiting_text = State()


def review_rating_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="⭐" * n, callback_data=f"rate:{plan_id}:{n}")
        for n in range(1, 6)
    ]]
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data="rate:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for plan_id, plan in PLANS.items():
        rub_price = round(plan["stars"] * RUB_PER_STAR, 2)
        rows.append([
            InlineKeyboardButton(
                text=(
                    f"{plan['title']} — {plan['stars']} ⭐ "
                    f"(~{rub_price} ₽) / {plan['days']} дн."
                ),
                callback_data=f"buy:{plan_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await db.ensure_user(message.from_user.id, message.from_user.username)
    text = (
        "Привет! Здесь можно оформить доступ к закрытому каналу.\n\n"
        "Выберите тариф ниже — оплата через Telegram Stars, прямо внутри Telegram."
    )
    await message.answer(text, reply_markup=plans_keyboard())


@router.message(F.text == "/status")
async def cmd_status(message: Message):
    sub = await db.get_active_subscription(message.from_user.id)
    if sub:
        expires = datetime.fromisoformat(sub["expires_at"])
        await message.answer(
            f"Ваша подписка активна до {expires.strftime('%Y-%m-%d %H:%M')} (UTC)."
        )
    else:
        await message.answer("У вас нет активной подписки. Используйте /start, чтобы выбрать тариф.")


@router.callback_query(F.data.startswith("buy:"))
async def on_buy(callback: CallbackQuery, bot: Bot):
    plan_id = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_id)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    if plan["stars"] < MIN_STARS:
        await callback.answer(
            f"Минимальная покупка — {MIN_STARS} ⭐", show_alert=True
        )
        return

    prices = [LabeledPrice(label=plan["title"], amount=plan["stars"])]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=plan["title"],
        description=plan["description"],
        payload=f"sub:{plan_id}:{callback.from_user.id}",
        currency="XTR",  # Telegram Stars
        prices=prices,
        provider_token="",  # не требуется для Stars
    )
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_q: PreCheckoutQuery, bot: Bot):
    # Здесь можно провалидировать payload / доступность тарифа
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot):
    payload = message.successful_payment.invoice_payload
    _, plan_id, user_id_str = payload.split(":")
    plan = PLANS[plan_id]
    user_id = int(user_id_str)

    expires_at = await db.extend_subscription(user_id, plan["days"])

    # Создаём одноразовую инвайт-ссылку в закрытый канал
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=plan["channel_id"],
            member_limit=1,
            expire_date=datetime.utcnow() + timedelta(hours=1),
        )
        link_text = f"\n\nВаша персональная ссылка (действует 1 час, для 1 входа):\n{invite.invite_link}"
    except TelegramBadRequest as e:
        log.error("Не удалось создать инвайт-ссылку: %s", e)
        link_text = "\n\n⚠️ Не удалось создать ссылку автоматически, свяжитесь с администратором."

    await message.answer(
        f"Оплата получена ✅\nПодписка «{plan['title']}» активна до "
        f"{expires_at.strftime('%Y-%m-%d %H:%M')} (UTC).{link_text}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 Новая оплата: user_id={user_id}, план={plan['title']}, "
                f"{plan['stars']} ⭐",
            )
        except Exception:
            pass

    await message.answer(
        "Как вам сервис? Поставьте оценку от 1 до 5 ⭐ — это поможет другим и нам:",
        reply_markup=review_rating_keyboard(plan_id),
    )


@router.callback_query(F.data == "rate:skip")
async def on_review_skip(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Хорошо, спасибо!")


@router.callback_query(F.data.startswith("rate:"))
async def on_review_rating(callback: CallbackQuery, state: FSMContext):
    _, plan_id, rating_str = callback.data.split(":")
    rating = int(rating_str)

    await state.update_data(plan_id=plan_id, rating=rating)
    await state.set_state(ReviewStates.waiting_text)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Спасибо! Хотите добавить пару слов текстом? "
        "Напишите отзыв сообщением или отправьте /skip, чтобы пропустить."
    )
    await callback.answer()


@router.message(ReviewStates.waiting_text, F.text == "/skip")
async def on_review_text_skip(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await _finalize_review(message, state, bot, data["plan_id"], data["rating"], None)


@router.message(ReviewStates.waiting_text)
async def on_review_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await _finalize_review(message, state, bot, data["plan_id"], data["rating"], message.text)


async def _finalize_review(message: Message, state: FSMContext, bot: Bot, plan_id: str, rating: int, text: str | None):
    await db.save_review(message.from_user.id, plan_id, rating, text)
    await state.clear()
    await message.answer("Спасибо за отзыв! 🙌")

    stars_str = "⭐" * rating
    review_line = f"📝 Отзыв от user_id={message.from_user.id}: {stars_str}"
    if text:
        review_line += f"\n«{text}»"

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, review_line)
        except Exception:
            pass


async def subscription_watcher(bot: Bot):
    """Фоновая задача: раз в час проверяет истёкшие подписки и кикает пользователей."""
    while True:
        try:
            expired = await db.pop_expired_subscriptions()
            for row in expired:
                user_id = row["user_id"]
                channel_id = row["channel_id"]
                try:
                    await bot.ban_chat_member(channel_id, user_id)
                    await bot.unban_chat_member(channel_id, user_id)  # ban+unban = кик без блокировки навсегда
                    await bot.send_message(
                        user_id,
                        "Ваша подписка истекла, доступ к каналу закрыт. "
                        "Чтобы продлить — используйте /start.",
                    )
                except Exception as e:
                    log.warning("Не удалось кикнуть user_id=%s: %s", user_id, e)
        except Exception as e:
            log.exception("Ошибка в subscription_watcher: %s", e)

        await asyncio.sleep(3600)  # раз в час


async def main():
    await db.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    asyncio.create_task(subscription_watcher(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
