"""Telegram control panel shared by the websocket and live-Under daemons.

Handlers use module-level objects initialized by start_bot_polling. Under-specific
menus are enabled by RuntimeConfig.under_totals; edits use aiogram conversation
state and authorization is enforced on both messages and callback queries.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal, InvalidOperation
from typing import Optional, Any, Callable, Awaitable

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from config_manager import (
    RuntimeConfig,
    MAX_ALERT_COOLDOWN_MINUTES,
    MIN_ALERT_COOLDOWN_MINUTES,
    SUPPORTED_LEAGUES,
    UNDER_ACTIVE_END_HOUR,
    UNDER_ACTIVE_START_HOUR,
    UNDER_DEFAULT_THRESHOLDS,
)

logger = logging.getLogger("BotUI")

router = Router()
config: RuntimeConfig = None
# Set by start_bot_polling, like config. Typed loosely because soccer_monitor imports
# this module, so importing ApiHealth from it here would be circular.
health: Any = None
allowed_chat_id: Optional[int] = None
market_snapshot: Any = None
test_sender: Any = None
market_fetcher: Callable[[], Awaitable[dict]] | None = None
rearm_alert: Callable[[str, datetime], int | None] | None = None

# Price thresholds on Polymarket are between 0 and 1
THRESHOLD_MIN = 0.01
THRESHOLD_MAX = 1.0


class AuthMiddleware(BaseMiddleware):
    """Silently ignores all events from unauthorized chat IDs."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = None
        if isinstance(event, Message):
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message else None

        if allowed_chat_id is None or chat_id != allowed_chat_id:
            return  # silently ignore unauthorized users

        return await handler(event, data)


class BotStates(StatesGroup):
    """Input awaited after an authorized user selects a menu action."""
    waiting_for_global_threshold = State()
    waiting_for_league_threshold = State()
    waiting_for_under_threshold = State()
    waiting_for_schedule = State()
    waiting_for_alert_cooldown = State()

def get_main_menu_kb():
    buttons = [
        [InlineKeyboardButton(text="📊 Status", callback_data="menu_status")],
        [InlineKeyboardButton(text="⚙️ Edit Thresholds", callback_data="menu_thresholds")],
        [InlineKeyboardButton(text="🏆 Manage Leagues", callback_data="menu_leagues")],
    ]
    if config and config.under_totals:
        buttons.extend([
            [InlineKeyboardButton(text="📋 View Thresholds", callback_data="view_thresholds:0")],
            [InlineKeyboardButton(text="⚽ Live Markets", callback_data="live_markets:0")],
            [InlineKeyboardButton(text="🕒 Monitoring Schedule", callback_data="menu_schedule")],
            [InlineKeyboardButton(text=f"🔁 Alert Cooldown · {config.alert_cooldown_minutes} min", callback_data="edit_alert_cooldown")],
            [InlineKeyboardButton(text="🔔 Send Test Notification", callback_data="test_notification")],
        ])
    buttons.append([InlineKeyboardButton(text="❌ Close", callback_data="menu_close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def paged_kb(prefix, page, count, page_size):
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"{prefix}:{page - 1}"))
    if (page + 1) * page_size < count:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{prefix}:{page + 1}"))
    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton(text="🏠 Main menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_thresholds_menu_kb():
    if config.under_totals:
        buttons = [
            [InlineKeyboardButton(text=f"{data['flag']} {league}", callback_data=f"under_league:{league}")]
            for league, data in SUPPORTED_LEAGUES.items()
        ]
        buttons.append([InlineKeyboardButton(text="🌍 All leagues", callback_data="under_all")])
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu_main")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    buttons = []
    
    # Global
    buttons.append([InlineKeyboardButton(text=f"🌍 Global Threshold: {config.global_threshold:.3f}", callback_data="edit_global_threshold")])
    
    # Leagues
    for league in config.active_leagues:
        t = config.league_thresholds.get(league)
        flag = SUPPORTED_LEAGUES.get(league, {}).get("flag", "")
        disp_name = f"{league} {flag}".strip()
        text = f"{disp_name}: {t:.3f}" if t is not None else f"{disp_name}: Default"
        buttons.append([
            InlineKeyboardButton(text=f"{text}", callback_data=f"edit_thresh_{league}"),
            InlineKeyboardButton(text="🗑 Reset", callback_data=f"reset_thresh_{league}")
        ])
        
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_under_lines_kb(league: str):
    buttons = [
        [InlineKeyboardButton(
            text=f"Under {line} · {config.get_under_threshold(league, line) * 100:g}¢",
            callback_data=f"under_edit:{line}:{league}",
        )]
        for line in UNDER_DEFAULT_THRESHOLDS
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Leagues", callback_data="menu_thresholds")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_under_all_lines_kb():
    buttons = [[InlineKeyboardButton(text=f"Under {line}", callback_data=f"under_all_edit:{line}")]
               for line in UNDER_DEFAULT_THRESHOLDS]
    buttons.append([InlineKeyboardButton(text="⬅️ Leagues", callback_data="menu_thresholds")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_leagues_menu_kb():
    buttons = []
    for league, data in SUPPORTED_LEAGUES.items():
        is_active = league in config.active_leagues
        status = "✅" if is_active else "❌"
        flag = data.get("flag", "")
        disp_name = f"{league}   {flag}".strip()
        buttons.append([InlineKeyboardButton(text=f"{status}   {disp_name}", callback_data=f"toggle_league_{league}")])
        
    buttons.append([InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def validate_threshold(value: float) -> str:
    """Returns an error message if the threshold is invalid, or empty string if valid."""
    if value < THRESHOLD_MIN or value > THRESHOLD_MAX:
        return f"Threshold must be between {THRESHOLD_MIN} and {THRESHOLD_MAX}. Please try again, or /cancel."
    return ""


@router.message(Command("menu", "start"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 *Soccer Monitor Control Panel*", reply_markup=get_main_menu_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "menu_main")
async def cb_menu_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠 *Soccer Monitor Control Panel*", reply_markup=get_main_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "menu_close")
async def cb_menu_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
    
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "menu_status")
async def cb_menu_status(callback: CallbackQuery):
    active_cnt = len(config.active_leagues)
    text = f"📊 *Status*\n\nActive Leagues: {active_cnt}\n"
    if config.under_totals:
        defaults = ", ".join(f"Under {line}: {price * 100:g}¢" for line, price in UNDER_DEFAULT_THRESHOLDS.items())
        overrides = sum(len(prices) for prices in config.under_thresholds.values())
        hours = f"{config.monitor_start}–{config.monitor_end} ({config.monitor_timezone})"
        text += f"Polling: every 15 seconds, {hours}\nDefault limits: {defaults}\nCustom limits: {overrides}"
    else:
        text += f"Global Threshold: {config.global_threshold:.3f}\nCustom Thresholds: {len(config.league_thresholds)}"
    if health is not None:
        # Recorded from calls the daemon already makes — opening this menu costs no
        # API call of its own, which matters for API-Football's 100/day.
        text += "\n\n🔌 *API Health*\n" + "\n".join(health.status_lines())
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="menu_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.regexp(r"^view_thresholds:\d+$"))
async def cb_view_thresholds(callback: CallbackQuery):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    leagues = list(SUPPORTED_LEAGUES)
    size = 5
    page = min(int(callback.data.split(":")[1]), (len(leagues) - 1) // size)
    lines = ["📋 Effective thresholds", "Custom values are marked with ✏️; other values are defaults.", ""]
    for league in leagues[page * size:(page + 1) * size]:
        status = "enabled" if league in config.active_leagues else "disabled"
        lines.append(f"{SUPPORTED_LEAGUES[league]['flag']} {league} ({status})")
        for line in UNDER_DEFAULT_THRESHOLDS:
            marker = " ✏️" if str(line) in config.under_thresholds.get(league, {}) else ""
            lines.append(f"  Under {line}: {config.get_under_threshold(league, line) * 100:g}¢{marker}")
        lines.append("")
    await callback.message.edit_text("\n".join(lines), reply_markup=paged_kb("view_thresholds", page, len(leagues), size))
    await callback.answer()

@router.callback_query(F.data.regexp(r"^live_markets:\d+$"))
async def cb_live_markets(callback: CallbackQuery):
    """Fetch and render current markets whenever the view is opened or paged."""
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    await callback.answer()
    try:
        if market_fetcher is None:
            raise RuntimeError("Live market fetching is unavailable")
        snapshot = await market_fetcher()
    except Exception:
        logger.exception("Could not refresh live markets")
        await callback.message.edit_text(
            "⚽ Live markets\nCould not fetch current markets. Please try again.",
            reply_markup=paged_kb("live_markets", 0, 0, 6),
        )
        return
    markets = [(market, price) for market, price in snapshot.get("markets", []) if market.league in config.active_leagues]
    markets.sort(key=lambda item: (item[0].league, item[0].title, item[0].line))
    size = 6
    page = min(int(callback.data.split(":")[1]), max(0, (len(markets) - 1) // size))
    at = snapshot["at"].astimezone(ZoneInfo(config.monitor_timezone)).strftime("%Y-%m-%d %H:%M %Z")
    text = f"⚽ Live markets\nFetched: {at}\nPrices may change at any time.\n"
    if not markets:
        text += "\nNo monitored live markets found."
    for market, price in markets[page * size:(page + 1) * size]:
        ask = f"{price * 100:g}¢" if price is not None else "No ask"
        limit = config.get_under_threshold(market.league, market.line) * 100
        text += f"\n\n{market.title}\n{market.league} · Under {market.line}\nAsk: {ask} · Current limit: {limit:g}¢"
        if market.score:
            text += f" · Score: {market.score}"
    text += "\n\nA qualifying price may already have alerted."
    await callback.message.edit_text(text, reply_markup=paged_kb("live_markets", page, len(markets), size))


@router.callback_query(F.data.regexp(r"^rearm_alert:[0-9a-f]{32}$"))
async def cb_rearm_alert(callback: CallbackQuery):
    if rearm_alert is None:
        await callback.answer("Rearming is unavailable.", show_alert=True)
        return
    button_id = callback.data.split(":", 1)[1]
    minutes = rearm_alert(button_id, datetime.now(timezone.utc))
    if minutes is None:
        await callback.answer("This alert is already rearmed or has been replaced.", show_alert=True)
        return
    await callback.answer(f"Rearmed. This line can alert again in {minutes} minutes.", show_alert=True)


@router.callback_query(F.data == "edit_alert_cooldown")
async def cb_edit_alert_cooldown(callback: CallbackQuery, state: FSMContext):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    await state.set_state(BotStates.waiting_for_alert_cooldown)
    await callback.message.edit_text(
        f"Alert cooldown is currently {config.alert_cooldown_minutes} minutes.\n"
        f"Enter a whole number from {MIN_ALERT_COOLDOWN_MINUTES} to {MAX_ALERT_COOLDOWN_MINUTES}, or /cancel.\n"
        "The new delay appears on future alerts."
    )
    await callback.answer()


@router.message(BotStates.waiting_for_alert_cooldown, ~F.text.startswith("/"))
async def process_alert_cooldown(message: Message, state: FSMContext):
    try:
        saved = config.set_alert_cooldown(int(message.text or ""))
    except ValueError:
        await message.answer(
            f"Enter a whole number from {MIN_ALERT_COOLDOWN_MINUTES} to {MAX_ALERT_COOLDOWN_MINUTES}, or /cancel."
        )
        return
    if not saved:
        await message.answer("Could not save the cooldown. Try again or use /cancel.")
        return
    await state.clear()
    await message.answer(
        f"Cooldown saved. Future alerts will offer a {config.alert_cooldown_minutes}-minute rearm.",
        reply_markup=get_main_menu_kb(),
    )


def schedule_text():
    now = datetime.now(timezone.utc)
    active = config.monitoring_active(now)
    text = f"🕒 Monitoring schedule\n{config.monitor_start}–{config.monitor_end} · {config.monitor_timezone}\nStatus: {'Active' if active else 'Outside monitoring hours'}"
    if not active:
        text += f"\nNext start: {config.next_monitor_start(now).strftime('%Y-%m-%d %H:%M %Z')}"
    return text

@router.callback_query(F.data == "menu_schedule")
async def cb_schedule(callback: CallbackQuery):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Change schedule", callback_data="edit_schedule")], [InlineKeyboardButton(text="🏠 Main menu", callback_data="menu_main")]])
    await callback.message.edit_text(schedule_text(), reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == "edit_schedule")
async def cb_edit_schedule(callback: CallbackQuery, state: FSMContext):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    await state.set_state(BotStates.waiting_for_schedule)
    await callback.message.edit_text("Send start, end and timezone on one line.\nExample: 14:00 23:00 Asia/Jerusalem\nUse 24-hour times and an IANA timezone such as Europe/London. Overnight windows are supported. Type /cancel to stop.")
    await callback.answer()

@router.message(BotStates.waiting_for_schedule)
async def process_schedule(message: Message, state: FSMContext):
    """Validate and save a complete start/end/timezone input or retain edit state."""
    parts = (message.text or "").strip().split()
    try:
        if len(parts) != 3:
            raise ValueError
        saved = config.set_monitor_schedule(*parts)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        await message.answer("Please use different valid 24-hour times and a valid timezone, for example: 14:00 23:00 Asia/Jerusalem. Or /cancel.")
        return
    if not saved:
        await message.answer("Could not save the schedule. Try again, or /cancel.")
        return
    await state.clear()
    await message.answer("✅ Schedule saved. It applies on the next poll.\n\n" + schedule_text(), reply_markup=get_main_menu_kb())

@router.callback_query(F.data == "test_notification")
async def cb_test_notification(callback: CallbackQuery):
    """Exercise the configured alert sender without claiming a market alert."""
    if not config.under_totals or test_sender is None:
        await callback.answer("Test notification is unavailable.", show_alert=True)
        return
    result = await test_sender("🔔 <b>Test notification</b>\nYour Soccer Monitor notification channel is working.")
    status = "Test notification sent." if result is True else ("Delivery was uncertain. Check your messages." if result is None else "Test notification failed. Check Telegram settings and status.")
    await callback.answer(status, show_alert=True)

@router.callback_query(F.data == "menu_thresholds")
async def cb_menu_thresholds(callback: CallbackQuery):
    prompt = "Choose a league or all leagues." if config.under_totals else "Select a threshold to modify:"
    await callback.message.edit_text(f"⚙️ *Edit Thresholds*\n{prompt}", reply_markup=get_thresholds_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "under_all")
async def cb_under_all(callback: CallbackQuery):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    await callback.message.edit_text("All supported leagues, including disabled leagues.\nChoose a full-game Under line.", reply_markup=get_under_all_lines_kb())
    await callback.answer()

@router.callback_query(F.data.regexp(r"^under_all_edit:(5\.5|6\.5|7\.5)$"))
async def cb_under_all_edit(callback: CallbackQuery, state: FSMContext):
    if not config.under_totals:
        await callback.answer("Open /menu to refresh.")
        return
    line_text = callback.data.split(":", 1)[1]
    await state.update_data(target_league=None, target_under_line=float(line_text))
    await state.set_state(BotStates.waiting_for_under_threshold)
    await callback.message.edit_text(
        f"All supported leagues · Under {line_text}\nEnter the price limit in cents, e.g. 80 or 85.5. This replaces existing limits for this line. Type /cancel to go back."
    )
    await callback.answer()

@router.callback_query(F.data.startswith("under_league:"))
async def cb_under_league(callback: CallbackQuery):
    league = callback.data.split(":", 1)[1]
    if not config.under_totals or league not in SUPPORTED_LEAGUES:
        await callback.answer("Open /menu to refresh.")
        return
    await callback.message.edit_text(f"{league}\nChoose a full-game Under line.", reply_markup=get_under_lines_kb(league))
    await callback.answer()

@router.callback_query(F.data.regexp(r"^under_edit:(5\.5|6\.5|7\.5):"))
async def cb_under_edit(callback: CallbackQuery, state: FSMContext):
    _, line_text, league = callback.data.split(":", 2)
    if not config.under_totals or league not in SUPPORTED_LEAGUES:
        await callback.answer("Open /menu to refresh.")
        return
    await state.update_data(target_league=league, target_under_line=float(line_text))
    await state.set_state(BotStates.waiting_for_under_threshold)
    await callback.message.edit_text(
        f"{league} · Under {line_text}\nEnter the price limit in cents, e.g. 80 or 85.5. Type /cancel to go back."
    )
    await callback.answer()

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("Cancelled. Type /menu to open panel again.")

@router.message(BotStates.waiting_for_under_threshold)
async def process_under_threshold(message: Message, state: FSMContext):
    """Convert cents to a price and save either one league or all supported leagues."""
    try:
        cents = Decimal((message.text or "").strip())
        if not cents.is_finite() or not 0 < cents <= 100:
            raise ValueError
        price = float(cents / 100)
        if not price:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Enter a price in cents, greater than 0 and at most 100, or /cancel.")
        return

    data = await state.get_data()
    league, line = data["target_league"], data["target_under_line"]
    saved = (config.set_under_threshold_all_leagues(line, price) if league is None
             else config.set_under_threshold(league, line, price))
    if not saved:
        await message.answer("Could not save the threshold. Try again, or /cancel.")
        return
    await state.clear()
    if league is None:
        await message.answer(f"All supported leagues · Under {line} limit saved at {price * 100:g}¢.", reply_markup=get_under_all_lines_kb())
    else:
        await message.answer(f"{league} · Under {line} limit saved at {price * 100:g}¢.", reply_markup=get_under_lines_kb(league))

@router.callback_query(F.data == "menu_leagues")
async def cb_menu_leagues(callback: CallbackQuery):
    await callback.message.edit_text("🏆 *Manage Leagues*\nTap to toggle monitoring:", reply_markup=get_leagues_menu_kb(), parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("toggle_league_"))
async def cb_toggle_league(callback: CallbackQuery):
    league = callback.data.replace("toggle_league_", "")
    config.toggle_league(league)
    await callback.message.edit_reply_markup(reply_markup=get_leagues_menu_kb())
    flag = SUPPORTED_LEAGUES.get(league, {}).get("flag", "")
    disp_name = f"{league} {flag}".strip()
    await callback.answer(f"Toggled {disp_name}")

@router.callback_query(F.data.regexp(r"^(edit_global_threshold$|edit_thresh_|reset_thresh_)"),
                       lambda _: config.under_totals)
async def cb_old_threshold_menu(callback: CallbackQuery):
    await callback.answer("Open /menu to refresh.")

@router.callback_query(F.data == "edit_global_threshold")
async def cb_edit_global_threshold(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"Enter the new global threshold ({THRESHOLD_MIN}–{THRESHOLD_MAX}).\nType /cancel to abort.",
        reply_markup=None
    )
    await state.set_state(BotStates.waiting_for_global_threshold)
    await callback.answer()

@router.callback_query(F.data.startswith("edit_thresh_"))
async def cb_edit_league_threshold(callback: CallbackQuery, state: FSMContext):
    league = callback.data.replace("edit_thresh_", "")
    await state.update_data(target_league=league)
    flag = SUPPORTED_LEAGUES.get(league, {}).get("flag", "")
    disp_name = f"{league} {flag}".strip()
    await callback.message.edit_text(
        f"Enter the new threshold for {disp_name} ({THRESHOLD_MIN}–{THRESHOLD_MAX}).\nType /cancel to abort.",
        reply_markup=None
    )
    await state.set_state(BotStates.waiting_for_league_threshold)
    await callback.answer()

@router.callback_query(F.data.startswith("reset_thresh_"))
async def cb_reset_league_threshold(callback: CallbackQuery):
    league = callback.data.replace("reset_thresh_", "")
    config.remove_league_threshold(league)
    try:
        await callback.message.edit_reply_markup(reply_markup=get_thresholds_menu_kb())
    except TelegramBadRequest:
        pass
    flag = SUPPORTED_LEAGUES.get(league, {}).get("flag", "")
    disp_name = f"{league} {flag}".strip()
    await callback.answer(f"Reset {disp_name} threshold to default.")

@router.message(BotStates.waiting_for_global_threshold)
async def process_global_threshold(message: Message, state: FSMContext):
    logger.info(f"process_global_threshold triggered, text={message.text!r}")
    if not message.text:
        await message.answer("Please send a number, or /cancel.")
        return
    try:
        val = float(message.text.strip())
        error = validate_threshold(val)
        if error:
            await message.answer(error)
            return
        config.set_global_threshold(val)
        await state.clear()
        await message.answer("✅ Global threshold updated.", reply_markup=get_thresholds_menu_kb())
    except ValueError:
        await message.answer("Invalid number. Please try again, or /cancel.")
    except Exception as e:
        logger.error(f"Error in process_global_threshold: {e}", exc_info=True)
        await message.answer(f"Unexpected error: {e}")

@router.message(BotStates.waiting_for_league_threshold)
async def process_league_threshold(message: Message, state: FSMContext):
    logger.info(f"process_league_threshold triggered, text={message.text!r}")
    if not message.text:
        await message.answer("Please send a number, or /cancel.")
        return
    try:
        val = float(message.text.strip())
        error = validate_threshold(val)
        if error:
            await message.answer(error)
            return
        data = await state.get_data()
        league = data.get("target_league")
        logger.info(f"Setting threshold for league={league!r} to {val}")
        if league:
            config.set_league_threshold(league, val)
            await state.clear()
            flag = SUPPORTED_LEAGUES.get(league, {}).get("flag", "")
            disp_name = f"{league} {flag}".strip()
            await message.answer(f"✅ {disp_name} threshold updated to {val:.3f}.", reply_markup=get_thresholds_menu_kb())
        else:
            await state.clear()
            await message.answer("Error: League not found. Type /menu to restart.")
    except ValueError:
        await message.answer("Invalid number. Please try again, or /cancel.")
    except Exception as e:
        logger.error(f"Error in process_league_threshold: {e}", exc_info=True)
        await message.answer(f"Unexpected error: {e}")


@router.message()
async def catch_all_message(message: Message, state: FSMContext):
    """Debug: catches any message that didn't match another handler."""
    current_state = await state.get_state()
    # from_user is None for channel posts and some forwards, and this handler catches
    # everything that fell through -- so it must not be the thing that raises.
    from_id = message.from_user.id if message.from_user else None
    logger.warning(
        f"catch_all_message: text={message.text!r}, state={current_state}, "
        f"chat_id={message.chat.id}, from_id={from_id}"
    )


async def start_bot_polling(bot_token: str, runtime_config: RuntimeConfig,
                            chat_id: Optional[str], api_health: Any = None,
                            snapshot: Any = None, notification_sender: Any = None,
                            live_market_fetcher: Callable[[], Awaitable[dict]] | None = None,
                            alert_rearmer: Callable[[str, datetime], int | None] | None = None):
    """Initialize shared UI dependencies and poll only when a valid chat ID is set."""
    global config, allowed_chat_id, health, market_snapshot, test_sender, market_fetcher, rearm_alert
    config = runtime_config
    health = api_health
    market_snapshot = snapshot
    test_sender = notification_sender
    market_fetcher = live_market_fetcher
    rearm_alert = alert_rearmer
    if chat_id:
        try:
            allowed_chat_id = int(chat_id)
        except ValueError:
            logger.error(f"TELEGRAM_CHAT_ID is not a number: {chat_id!r}")

    if allowed_chat_id is None:
        # Fail closed. Polling starts whenever the bot *token* alone is set, and this
        # control panel can change thresholds and toggle leagues, so an unset or
        # malformed chat id must stop the bot rather than open it to anyone who finds
        # it. Alerts have nowhere to go without a chat id in any case.
        logger.error(
            "TELEGRAM_CHAT_ID is unset or not a number — refusing to start the control "
            "bot, which would otherwise accept commands from any chat."
        )
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    # Register auth middleware on all handler types
    router.message.middleware(AuthMiddleware())
    router.callback_query.middleware(AuthMiddleware())

    dp.include_router(router)
    
    logger.info("Starting aiogram polling loop...")
    try:
        # handle_signals defaults to True, which makes aiogram install its own SIGTERM
        # handler -- replacing Python's default "terminate on SIGTERM". It stopped
        # polling and returned, but nothing cancelled the daemon's other loops, so
        # soccer_monitor hung until systemd SIGKILLed it 90s later on every restart.
        # main() owns the signal now and cancels everything.
        await dp.start_polling(bot, handle_signals=False)
    finally:
        logger.info("Shutting down aiogram bot session...")
        await bot.session.close()
