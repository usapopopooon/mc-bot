from __future__ import annotations

import logging

from mc_bot.bot import MinecraftDiscordBot
from mc_bot.config import Config


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_environment()
    except ValueError as error:
        raise SystemExit(str(error)) from error

    bot = MinecraftDiscordBot(config)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    run()
