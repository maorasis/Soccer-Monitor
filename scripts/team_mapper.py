"""
Team Name Mapper — Polymarket ↔ API-Football

Builds and maintains a JSON mapping file that translates team names as they
appear on Polymarket event titles to their canonical API-Football equivalents.

Workflow:
    1. Fetch all team rosters from API-Football for every supported league.
    2. Scrape current Polymarket soccer events and extract team names.
    3. Auto-match where possible (exact / normalized), then prompt the user
       interactively for any remaining unmatched teams.
    4. Persist results to ``team_mapping.json``.

Usage:
    python team_mapper.py
"""

import os
import json
import logging
import asyncio
import re
import difflib
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, List

import aiohttp
from dotenv import load_dotenv
from config_manager import RuntimeConfig, SUPPORTED_LEAGUES

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TeamMapper")

MAPPING_FILE = "team_mapping.json"
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
API_FOOTBALL_URL = "https://v3.football.api-sports.io/"
GAMMA_API_URL = "https://gamma-api.polymarket.com/events"

# Collect ALL supported league IDs and tags (not just active ones)
# so the mapper builds a comprehensive dictionary for every league we might monitor.
ALL_SUPPORTED_API_IDS = {v["api_id"] for v in SUPPORTED_LEAGUES.values()}
ALL_SUPPORTED_POLY_TAGS = set()
for _league_data in SUPPORTED_LEAGUES.values():
    ALL_SUPPORTED_POLY_TAGS.update(_league_data["polymarket_tags"])

def normalize_name(name: str) -> str:
    """Normalizes a team name for comparison.
    Only strips generic suffixes/prefixes at word boundaries, preserving
    distinguishing words like 'City', 'United', etc."""
    name = name.lower().strip()
    # Only remove generic suffixes at the END of the name
    for suffix in [' fc', ' cf', ' afc', ' sc', ' fk', ' bk']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    # Only remove generic prefixes at the START
    for prefix in ['fc ', 'cf ']:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip()

def auto_match(poly_team: str, api_teams: Set[str]) -> str | None:
    """Attempts to match a Polymarket team to an API-Football team.
    Uses only high-confidence matching to avoid false positives.
    Unmatched teams are left for manual resolution."""
    # 1. Exact case-insensitive match
    poly_lower = poly_team.lower().strip()
    for api_team in api_teams:
        if poly_lower == api_team.lower().strip():
            return api_team

    # 2. Exact normalized match
    poly_norm = normalize_name(poly_team)
    for api_team in api_teams:
        if poly_norm == normalize_name(api_team):
            return api_team

    # No fuzzy matching — unmatched teams go to manual prompt
    return None


async def fetch_api_football_teams(session: aiohttp.ClientSession, config: RuntimeConfig) -> Set[str]:
    """Fetch teams from every supported league using the hard-coded 2024 season.

    Requests are spaced out and an HTTP 429 is retried once. The config argument
    does not restrict this roster to currently enabled leagues.
    """
    if not API_FOOTBALL_KEY:
        logger.error("API_FOOTBALL_KEY is required.")
        return set()

    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    # Free plan only allows seasons 2022-2024. Team names are stable across seasons,
    # so we use the most recent available (2024) to build a comprehensive roster.
    season = 2024
    teams = set()

    for idx, (league_name, league_data) in enumerate(SUPPORTED_LEAGUES.items()):
        league_id = league_data["api_id"]
        params = {'league': str(league_id), 'season': str(season)}
        logger.info(f"Fetching teams for {league_name} (id={league_id}, season={season})...")

        # Rate limit: free plan allows ~10 requests/minute
        if idx > 0:
            await asyncio.sleep(7)

        async with session.get(f"{API_FOOTBALL_URL}teams", headers=headers, params=params) as resp:
            if resp.status == 429:
                logger.warning(f"Rate limited on {league_name}. Waiting 60s and retrying...")
                await asyncio.sleep(60)
                async with session.get(f"{API_FOOTBALL_URL}teams", headers=headers, params=params) as retry_resp:
                    if retry_resp.status != 200:
                        body = await retry_resp.text()
                        logger.warning(f"Retry failed for {league_name}: {retry_resp.status} {body[:200]}")
                        continue
                    data = await retry_resp.json()
            elif resp.status != 200:
                body = await resp.text()
                logger.warning(f"API-Football returned {resp.status} for {league_name}: {body[:200]}")
                continue
            else:
                data = await resp.json()

            api_errors = data.get("errors", {})
            if api_errors:
                logger.warning(f"API-Football errors for {league_name}: {api_errors}")
                continue

            league_count = 0
            for item in data.get("response", []):
                name = item.get("team", {}).get("name")
                if name:
                    teams.add(name)
                    league_count += 1
            logger.info(f"  → {league_count} teams for {league_name}.")

    logger.info(f"Found {len(teams)} teams across all supported leagues from API-Football.")
    return teams


def fetch_polymarket_teams_sync(events) -> Set[str]:
    """Synchronous variant that extracts team names from pre-fetched events.

    Not currently used — kept as a convenience stub for potential REPL or
    script usage where events have already been downloaded.
    """
    pass

async def fetch_polymarket_teams(session: aiohttp.ClientSession, config: RuntimeConfig) -> Set[str]:
    """Fetches soccer events from Polymarket (open and closed) and extracts team names.
    Only includes events tagged with a supported league."""
    logger.info("Fetching Polymarket active soccer events...")
    teams = set()
    
    offset = 0
    limit = 100
    while True:
        params = {
            'tag_slug': 'soccer',
            'limit': str(limit),
            'offset': str(offset)
        }
        async with session.get(GAMMA_API_URL, params=params) as resp:
            if resp.status != 200:
                break
            events = await resp.json()
            if not events:
                break
                
            for event in events:
                # Filter to only supported leagues
                tags = [tag.get('label', '').lower() for tag in event.get('tags', [])]
                if not any(t in ALL_SUPPORTED_POLY_TAGS for t in tags):
                    continue
                
                title = event.get('title', '')
                # Strip out side-market suffixes like " - Exact Score" or " - Halftime"
                # using space-hyphen-space to avoid breaking hyphenated teams like "Saint-Etienne"
                title = title.split(' - ')[0]
                
                # Extract teams by splitting " vs. " or " vs "
                parts = title.split(' vs. ')
                if len(parts) != 2:
                    parts = title.split(' vs ')
                
                if len(parts) == 2:
                    teams.add(parts[0].strip())
                    teams.add(parts[1].strip())
                    
            if len(events) < limit:
                break
            offset += limit
            
    logger.info(f"Extracted {len(teams)} team names from Polymarket event titles.")
    return teams

def load_existing_mapping() -> Dict[str, str]:
    """Load the persisted Polymarket→API-Football team mapping from disk.

    Returns an empty dict if the file doesn't exist or is malformed.
    """
    if os.path.exists(MAPPING_FILE):
        try:
            with open(MAPPING_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_mapping(mapping: Dict[str, str]):
    """Write the team mapping dict to ``team_mapping.json``."""
    with open(MAPPING_FILE, 'w') as f:
        json.dump(mapping, f, indent=4, sort_keys=True)
    logger.info(f"Saved {len(mapping)} mappings to {MAPPING_FILE}")

async def main():
    """Entry point: fetch teams from both sources, auto-match, and prompt for unresolved."""
    config = RuntimeConfig()
    
    async with aiohttp.ClientSession() as session:
        api_teams = await fetch_api_football_teams(session, config)
        poly_teams = await fetch_polymarket_teams(session, config)
        
        logger.info(f"API-Football teams: {len(api_teams)}, Polymarket teams: {len(poly_teams)}")
        if not api_teams:
            logger.warning("No teams fetched from API-Football. Aborting.")
            return
        if not poly_teams:
            logger.warning("No teams fetched from Polymarket. Aborting.")
            return
            
        mapping = load_existing_mapping()
        new_matches = 0
        
        unmatched_poly = []
        
        for p_team in poly_teams:
            if p_team in mapping:
                continue # Already mapped
                
            match = auto_match(p_team, api_teams)
            if match:
                logger.info(f"Auto-matched: '{p_team}' -> '{match}'")
                mapping[p_team] = match
                new_matches += 1
            else:
                unmatched_poly.append(p_team)
                
        if new_matches > 0:
            save_mapping(mapping)
            
        if unmatched_poly:
            print("\n--- UNMATCHED POLYMARKET TEAMS ---")
            print("The following teams could not be mapped automatically.")
            print("If you know the API-Football equivalent, you can map them now.")
            print("Press ENTER to skip a team.\n")
            
            api_teams_list = sorted(list(api_teams))
            
            for p_team in unmatched_poly:
                print(f"\nPolymarket Team: '{p_team}'")
                # Show some suggestions based on loose fuzzy match
                suggestions = []
                for a_team in api_teams_list:
                    if difflib.SequenceMatcher(None, p_team.lower(), a_team.lower()).ratio() > 0.4:
                        suggestions.append(a_team)
                        
                if suggestions:
                    print(f"Suggestions: {', '.join(suggestions[:5])}")
                    
                user_input = input(f"Enter API-Football name for '{p_team}' (or press ENTER to skip): ").strip()
                if user_input:
                    mapping[p_team] = user_input
                    save_mapping(mapping)
                    
        print("\nMapping process complete!")

if __name__ == "__main__":
    asyncio.run(main())
