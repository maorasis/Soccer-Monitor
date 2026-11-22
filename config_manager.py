"""Persist shared league, threshold and live-Under schedule configuration.

The JSON file is shared by monitor components in a process. RuntimeConfig keeps an
in-memory copy; changing the file externally does not automatically reload it.
"""

import json
import os
import logging
import tempfile
from typing import Dict, List, Set
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("ConfigManager")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SUPPORTED_LEAGUES = {    # Top 5 European Leagues
    "Premier League": {"api_id": 39, "polymarket_tags": ["premier league", "epl"], "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "La Liga": {"api_id": 140, "polymarket_tags": ["la liga", "laliga"], "flag": "🇪🇸"},
    "Serie A": {"api_id": 135, "polymarket_tags": ["serie a", "sea"], "flag": "🇮🇹"},
    "Bundesliga": {"api_id": 78, "polymarket_tags": ["bundesliga"], "flag": "🇩🇪"},
    "Ligue 1": {"api_id": 61, "polymarket_tags": ["ligue 1"], "flag": "🇫🇷"},
    # International Tournaments
    "World Cup": {"api_id": 1, "polymarket_tags": ["fifa world cup", "world cup"], "flag": "🌍"},
    "Euro Championship": {"api_id": 4, "polymarket_tags": ["euro", "european championship", "uefa euro"], "flag": "🇪🇺"},
    "UEFA Nations League": {"api_id": 5, "polymarket_tags": ["uefa nations league", "unl"], "flag": "🇪🇺"},
    # UEFA Club Competitions
    "Champions League": {"api_id": 2, "polymarket_tags": ["champions league", "ucl"], "flag": "🇪🇺"},
    "Europa League": {"api_id": 3, "polymarket_tags": ["europa league", "uel"], "flag": "🇪🇺"},
    "Conference League": {"api_id": 848, "polymarket_tags": ["conference league", "europa conference league", "uecl"], "flag": "🇪🇺"},
    # Domestic Cups
    "FA Cup": {"api_id": 45, "polymarket_tags": ["fa cup"], "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "Carabao Cup": {"api_id": 48, "polymarket_tags": ["carabao cup", "efl cup", "league cup"], "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "Copa del Rey": {"api_id": 143, "polymarket_tags": ["copa del rey"], "flag": "🇪🇸"},
    "DFB Pokal": {"api_id": 81, "polymarket_tags": ["dfb pokal", "dfb-pokal", "german cup"], "flag": "🇩🇪"},
    "Coppa Italia": {"api_id": 137, "polymarket_tags": ["coppa italia", "italian cup"], "flag": "🇮🇹"},
}

DEFAULT_ACTIVE_LEAGUES = [
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "World Cup",
    "UEFA Nations League",
]
UNDER_DEFAULT_THRESHOLDS = {5.5: 0.80, 6.5: 0.85, 7.5: 0.85}
UNDER_ACTIVE_START_HOUR = 14
UNDER_ACTIVE_END_HOUR = 23

class RuntimeConfig:
    """Mutable runtime settings backed by an atomically replaced JSON file.

    Under limits are keyed by league and stringified goal line; absent entries use
    UNDER_DEFAULT_THRESHOLDS. Prices are dollar fractions, not cents.
    """
    def __init__(self, filepath=None, *, under_totals=False):
        if filepath is None:
            filepath = os.path.join(SCRIPT_DIR, "config_state.json")
        self.filepath = filepath
        self.under_totals = under_totals
        
        # Default settings
        self.global_threshold = float(os.getenv("ASK_PRICE_THRESHOLD", "0.10"))
        self.active_leagues: List[str] = list(DEFAULT_ACTIVE_LEAGUES)
        self.league_thresholds: Dict[str, float] = {} # e.g. {"La Liga": 0.08}
        self.under_thresholds: Dict[str, Dict[str, float]] = {}
        self.monitor_start = "14:00"
        self.monitor_end = "23:00"
        self.monitor_timezone = "Asia/Jerusalem"
        
        self.load()

    def load(self):
        """Load saved fields, retaining defaults for absent or invalid schedule fields."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                self.global_threshold = data.get("global_threshold", self.global_threshold)
                self.active_leagues = data.get("active_leagues", self.active_leagues)
                self.league_thresholds = data.get("league_thresholds", self.league_thresholds)
                self.under_thresholds = data.get("under_thresholds", {})
                schedule = data.get("monitor_schedule", {})
                try:
                    self._validate_schedule(schedule.get("start", "14:00"), schedule.get("end", "23:00"), schedule.get("timezone", "Asia/Jerusalem"))
                    self.monitor_start = schedule.get("start", "14:00")
                    self.monitor_end = schedule.get("end", "23:00")
                    self.monitor_timezone = schedule.get("timezone", "Asia/Jerusalem")
                except (ValueError, TypeError, ZoneInfoNotFoundError):
                    logger.warning("Invalid monitoring schedule; using defaults")
                logger.info(f"Loaded config from {self.filepath}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        else:
            self.save() # Create initial file

    def save(self):
        """Replace the config file atomically; return False on any write failure."""
        data = {
            "global_threshold": self.global_threshold,
            "active_leagues": self.active_leagues,
            "league_thresholds": self.league_thresholds,
            "monitor_schedule": {"start": getattr(self, "monitor_start", "14:00"), "end": getattr(self, "monitor_end", "23:00"), "timezone": getattr(self, "monitor_timezone", "Asia/Jerusalem")},
        }
        under_thresholds = getattr(self, "under_thresholds", {})
        if under_thresholds:
            data["under_thresholds"] = under_thresholds
        # Write a temporary file and rename it over the target. os.replace is
        # atomic, so a crash mid-save leaves the previous config intact instead of a
        # truncated file -- which load() would fail to parse and silently replace with
        # the default league list, turning a bad moment into changed trading behaviour.
        try:
            directory = os.path.dirname(self.filepath) or "."
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".config_state-",
                                            suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.filepath)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def get_under_threshold(self, league: str, line: float) -> float:
        """Resolve a league override or the built-in default for a supported line."""
        return self.under_thresholds.get(league, {}).get(str(line), UNDER_DEFAULT_THRESHOLDS[line])

    @staticmethod
    def _validate_schedule(start, end, timezone_name):
        for value in (start, end):
            if not isinstance(value, str) or len(value) != 5 or value[2] != ":" or not (value[:2] + value[3:]).isdigit():
                raise ValueError("Use HH:MM")
            hour, minute = map(int, value.split(":"))
            if hour > 23 or minute > 59:
                raise ValueError("Invalid time")
        if start == end:
            raise ValueError("Start and end must differ")
        ZoneInfo(timezone_name)

    def set_monitor_schedule(self, start, end, timezone_name):
        """Validate and persist a daily schedule; roll back memory if saving fails."""
        self._validate_schedule(start, end, timezone_name)
        previous = (self.monitor_start, self.monitor_end, self.monitor_timezone)
        self.monitor_start, self.monitor_end, self.monitor_timezone = start, end, timezone_name
        if self.save():
            return True
        self.monitor_start, self.monitor_end, self.monitor_timezone = previous
        return False

    def monitoring_active(self, now):
        """Check a timezone-aware instant against the half-open daily window.

        When start is later than end, the window crosses midnight.
        """
        local = now.astimezone(ZoneInfo(self.monitor_timezone))
        current = local.strftime("%H:%M")
        if self.monitor_start < self.monitor_end:
            return self.monitor_start <= current < self.monitor_end
        return current >= self.monitor_start or current < self.monitor_end

    def next_monitor_start(self, now):
        """Find the next active minute as a real instant, accounting for DST changes."""
        # Search real instants so skipped and repeated local times at DST changes
        # reflect when the monitor can actually become active.
        candidate = now.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(3 * 24 * 60):
            if self.monitoring_active(candidate):
                return candidate.astimezone(ZoneInfo(self.monitor_timezone))
            candidate += timedelta(minutes=1)
        raise ValueError("Could not find the next monitoring window")

    def set_under_threshold(self, league: str, line: float, price: float) -> bool:
        """Persist one validated override, restoring the prior state on save failure."""
        if league not in SUPPORTED_LEAGUES or line not in UNDER_DEFAULT_THRESHOLDS or not 0 < price <= 1:
            raise ValueError("Invalid league, goal line or price")
        previous = self.under_thresholds
        overrides = previous.get(league, {}) | {str(line): price}
        self.under_thresholds = previous | {league: overrides}
        if self.save():
            return True
        self.under_thresholds = previous
        return False

    def set_under_threshold_all_leagues(self, line: float, price: float) -> bool:
        """Replace one line for all supported leagues in one atomic config save."""
        if line not in UNDER_DEFAULT_THRESHOLDS or not 0 < price <= 1:
            raise ValueError("Invalid goal line or price")
        previous = self.under_thresholds
        self.under_thresholds = previous | {
            league: previous.get(league, {}) | {str(line): price}
            for league in SUPPORTED_LEAGUES
        }
        if self.save():
            return True
        self.under_thresholds = previous
        return False

    def get_threshold(self, league_name: str) -> float:
        # Match polymorphic league name string to our standardized supported leagues
        matched_league = self.match_league_name(league_name)
        if matched_league and matched_league in self.league_thresholds:
            return self.league_thresholds[matched_league]
        return self.global_threshold
        
    def set_global_threshold(self, threshold: float):
        self.global_threshold = threshold
        self.save()
        
    def set_league_threshold(self, league_name: str, threshold: float):
        if league_name in SUPPORTED_LEAGUES:
            self.league_thresholds[league_name] = threshold
            self.save()
            
    def remove_league_threshold(self, league_name: str):
        if league_name in self.league_thresholds:
            del self.league_thresholds[league_name]
            self.save()
            
    def toggle_league(self, league_name: str) -> bool:
        """Toggles a league's active state. Returns the new state (True if active)."""
        if league_name not in SUPPORTED_LEAGUES:
            return False
            
        if league_name in self.active_leagues:
            self.active_leagues.remove(league_name)
            self.save()
            return False
        else:
            self.active_leagues.append(league_name)
            self.save()
            return True

    def get_active_api_ids(self) -> Set[int]:
        ids = set()
        for l in self.active_leagues:
            if l in SUPPORTED_LEAGUES:
                ids.add(SUPPORTED_LEAGUES[l]["api_id"])
        return ids

    def get_active_polymarket_tags(self) -> Set[str]:
        tags = set()
        for l in self.active_leagues:
            if l in SUPPORTED_LEAGUES:
                tags.update(SUPPORTED_LEAGUES[l]["polymarket_tags"])
        return tags
        
    def match_league_name(self, polymarket_league_name: str) -> str:
        """Attempts to match a tag or name from polymarket to a supported league name."""
        lower_name = polymarket_league_name.lower()
        for name, data in SUPPORTED_LEAGUES.items():
            if lower_name == name.lower() or lower_name in data["polymarket_tags"]:
                return name
        return ""
