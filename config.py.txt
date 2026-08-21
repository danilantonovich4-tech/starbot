import os

BOT_TOKEN = os.environ[BOT_TOKEN]
ADMIN_IDS = [int(x) for x in os.environ.get(ADMIN_IDS, ).split(,) if x]
DB_PATH = os.environ.get(DB_PATH, subscriptions.db)
MIN_STARS = int(os.environ.get(MIN_STARS, 1))
RUB_PER_STAR = float(os.environ.get(RUB_PER_STAR, 2.0))

PLANS = {
    basic {
        title Базовый,
        description Доступ на 30 дней,
        stars 100,
        days 30,
        channel_id int(os.environ[CHANNEL_ID]),
    },
    # добавьте остальные тарифы
}