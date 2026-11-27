"""
MANUAL smoke script -- not part of the automated suite.

Simulates a Premier League dip at $0.11 to verify the per-league threshold override
triggers an alert. It reads the live config_state.json, needs the Premier League
threshold already set to 0.13 via the bot menu, and **sends a real Telegram message**.
Run it deliberately, on the server:

    python tests/test_threshold_alert.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
import aiohttp

from config_manager import RuntimeConfig
from soccer_monitor import MarketInfo, AlertEngine

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ThresholdTest")


async def main():
    config = RuntimeConfig()

    # Show what the config thinks the Premier League threshold is
    pl_threshold = config.get_threshold("Premier League")
    logger.info(f"Premier League threshold from config: {pl_threshold}")
    logger.info(f"Global threshold: {config.global_threshold}")
    logger.info(f"League overrides: {config.league_thresholds}")

    test_price = 0.11

    if test_price >= pl_threshold:
        logger.error(
            f"TEST WOULD NOT TRIGGER: price {test_price} >= threshold {pl_threshold}. "
            f"Make sure you've set Premier League threshold to 0.13 via the bot menu."
        )
        return

    logger.info(f"Price {test_price} < threshold {pl_threshold} → alert SHOULD fire.")

    mock_market = MarketInfo(
        event_title="Arsenal vs Liverpool (TEST)",
        event_slug="arsenal-vs-liverpool-test",
        league="Premier League",
        market_question="Will there be under 2.5 total goals?",
        under_token_id="TEST_PL_TOKEN_001",
        fixture_id=None,
    )

    async with aiohttp.ClientSession() as session:
        alert_engine = AlertEngine(session)
        await alert_engine.send_alert(mock_market, test_price, "0-0 (TEST 25' 1H)")

    logger.info("Done. Check Telegram for the alert.")


if __name__ == "__main__":
    asyncio.run(main())
