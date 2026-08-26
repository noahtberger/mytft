"""Talking to Riot: the API key, the HTTP calls, and downloading matches.

Everything that touches the network lives here so the report scripts stay
pure functions over files on disk. scout.py and serve.py both import it.

Nothing in here raises SystemExit - the caller decides whether a failure
should kill a CLI or become an error message on a web page. That is the
whole reason it is a module and not a copy inside each script.
"""

import json
import os
import time
from collections import deque
from pathlib import Path

import tftdata as tft

PLATFORM = "https://na1.api.riotgames.com"       # spectator lives on the platform host
REGIONAL = "https://americas.api.riotgames.com"  # account + match on the regional one
RANKED_QUEUE = 1100
# Seconds to wait before re-trying a 401. A key issued seconds ago is not
# live yet; this is the gap between pasting one and it working.
KEY_SETTLE = 5          # seconds between 401 retries
KEY_TRIES = 6           # so a new key gets about half a minute to propagate
SLEEP = 0.0           # pacing now lives in the limiter below, not in the callers

# Riot publishes the key's budget on every response: "100:120,20:1" is a
# hundred requests per two minutes and twenty per second. These are the dev
# key's, kept only as a starting guess - the first response replaces them.
LIMITS = [(20, 1), (100, 120)]
_SENT = deque()       # when we sent recent requests, for the sliding window

FORBIDDEN = object()  # sentinel: Riot said 403 and the caller wants to handle it


class RiotError(Exception):
    """A request failed in a way the caller should show to a human."""


def api_key():
    """The env var if set, else the .riot_key file next to this script.

    The file exists because dev keys expire every 24 hours: pasting the fresh
    one into a file once beats exporting it in every terminal you open that
    day. It lives next to the code rather than in the working directory so it
    is found no matter where you run from, and it is gitignored - a key in
    the repo would outlive its 24 hours as a public record.
    """
    key = os.environ.get("RIOT_API_KEY")
    if not key:
        try:
            key = Path(__file__).with_name(".riot_key").read_text().strip()
        except FileNotFoundError:
            pass
    if not key:
        raise RiotError(
            "No API key found. Get one at developer.riotgames.com, then\n"
            "either:  export RIOT_API_KEY='RGAPI-...'\n"
            "or paste it into a file called .riot_key next to riot.py\n"
            "(one line, nothing else - it's gitignored).")
    return key


def note_limits(header):
    """Adopt whatever budget Riot just said we have.

    A personal or production key has a bigger one than a dev key, and the
    difference is the difference between a night's crawl and a fortnight's.
    Reading it off the response means upgrading the key is the whole
    upgrade - no constant in here needs editing to match.
    """
    global LIMITS
    try:
        parsed = [(int(count), int(window)) for count, window in
                  (rule.split(":") for rule in header.split(","))]
    except (AttributeError, ValueError):
        return
    if parsed:
        LIMITS = parsed


def note_usage(header):
    """Believe Riot's count of what this key has spent, not our own.

    wait_for_slot only knows the requests this process made. Riot counts
    every caller on the key - the other terminal, a status check, a crawl
    that restarted a minute ago with an empty memory of its own history.
    Each of those is invisible locally, so the limiter thinks it has room it
    does not have, spends it, and is handed a Retry-After of up to two
    minutes. Observed on Set 18 launch morning: 111 second penalties while
    the local tally read nearly empty.

    The header arrives as "47:120,3:1" - what the server has counted in each
    window. Where it exceeds what we remember sending, the difference is
    somebody else's traffic, and padding our own record with it makes the
    next wait long enough. Only ever pads upwards; a server count lower than
    ours is a window that just rolled, and forgetting real sends there would
    walk straight back into the limit.
    """
    try:
        counts = {int(window): int(used) for used, window in
                  (rule.split(":") for rule in header.split(","))}
    except (AttributeError, ValueError):
        return
    now = time.monotonic()
    for _, window in LIMITS:
        theirs = counts.get(window)
        if theirs is None:
            continue
        ours = sum(1 for sent in _SENT if now - sent < window)
        _SENT.extend([now] * max(0, theirs - ours))


def wait_for_slot():
    """Block until one more request cannot break any of the published limits.

    A fixed sleep between requests cannot do this job. Tuned to 1.25s it ran
    at 96 requests per 120 against a cap of 100 - 96% of the budget, no
    headroom - and the first time it tipped over, Riot answered 429 with a
    Retry-After of up to two minutes. Two minutes is worth ninety-six
    requests, so one trip cost more than the margin saved all hour, and an
    overnight crawl decayed from 2,022 matches an hour to 185.

    Keeping the send times and waiting for the oldest to age out of the
    window instead spends the budget almost exactly, and never trips.
    """
    while True:
        now = time.monotonic()
        longest = max(window for _, window in LIMITS)
        while _SENT and now - _SENT[0] > longest:
            _SENT.popleft()

        wait = 0.0
        for allowed, window in LIMITS:
            recent = [sent for sent in _SENT if now - sent < window]
            if len(recent) >= allowed:
                # The oldest of the last `allowed` has to fall out of the
                # window before there is room for another.
                wait = max(wait, window - (now - recent[-allowed]))

        if wait <= 0:
            _SENT.append(now)
            return
        time.sleep(min(wait, 5.0) + 0.01)


def riot_get(url, allow_404=False, allow_403=False, **params):
    """GET a Riot endpoint, retrying politely on a rate limit.

    The key goes in as a query parameter rather than the X-Riot-Token header:
    this Mac's sandbox mangles that header, which makes a good key look
    invalid. allow_404 returns None instead of raising, which is how "you are
    not currently in a game" arrives.

    401 and 403 are kept apart on purpose. 401 means the key is wrong. 403
    means Riot understood the key and refused anyway - either it expired, or
    that endpoint simply isn't in your key's permissions. Reporting the second
    as "your key is invalid" sends you off to generate a fresh key that will
    fail in exactly the same way.

    A 401 is retried once. A dev key expires daily, so pasting a fresh one and
    immediately using it is the single most common thing that happens here -
    and for a few seconds after it is issued Riot has not propagated it yet
    and answers 401. Failing straight to "check it copied whole" sends you
    hunting for a typo in a key that is perfectly fine and works on the next
    attempt. Verified: the same key 401'd, then returned 200 moments later.
    """
    import requests  # imported lazily so offline callers need no dependency

    params["api_key"] = api_key()
    tried_401 = 0
    dropped = 0
    while True:
        try:
            wait_for_slot()
            response = requests.get(url, params=params, timeout=15)
        except requests.exceptions.RequestException as blip:
            # The connection itself failed - a read timeout, a reset, a DNS
            # hiccup. Every status code Riot can return was handled below and
            # this was not, so one dropped packet in an eight-hour crawl took
            # the whole thing down and left the night idle. Observed: a
            # ReadTimeout to americas killed a run 83 minutes in.
            #
            # Transport failures are transient in a way a 403 is not, so they
            # are worth waiting out rather than reporting. Backs off 2, 4, 8,
            # 16, 32 seconds and only then admits defeat, because a machine
            # that has genuinely lost the network will not get it back inside
            # a minute either.
            dropped += 1
            if dropped > 5:
                raise RiotError(f"Lost the network to Riot, six times over "
                                f"a minute: {blip}") from blip
            time.sleep(2 ** dropped)
            continue

        note_limits(response.headers.get("X-App-Rate-Limit"))
        note_usage(response.headers.get("X-App-Rate-Limit-Count"))

        if response.status_code == 429:
            # Should now be rare enough to be worth noticing. If these show
            # up in a log, the limiter's idea of the budget is wrong.
            print(f"  (429 - waiting "
                  f"{response.headers.get('Retry-After', 5)}s)", flush=True)
            time.sleep(int(response.headers.get("Retry-After", 5)))
            continue

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code == 403 and allow_403:
            return FORBIDDEN

        if response.status_code == 401 and tried_401 < KEY_TRIES:
            # A freshly issued key is not live the instant it is issued, and
            # "a few seconds" was optimistic: on Set 18 launch day one took
            # roughly twelve, which one retry could not cover. The crawl gave
            # up, the supervisor saw three fast failures, called the key dead
            # and stopped - on a key that worked perfectly a minute later.
            tried_401 += 1
            time.sleep(KEY_SETTLE)
            continue
        if response.status_code == 401:
            raise RiotError(
                "Riot doesn't recognise that API key, twice in a row. Either "
                "it copied short, or it was regenerated again after you "
                "pasted it - the site invalidates the previous key each time "
                "you press REGENERATE.")
        if response.status_code == 403:
            raise RiotError("Riot understood your key and refused (403). "
                            "Either it expired - dev keys last 24 hours - or "
                            "this endpoint isn't in your key's permissions.")
        if response.status_code != 200:
            raise RiotError(f"Riot returned {response.status_code}: "
                            f"{response.text[:160]}")
        return response.json()


def resolve(riot_id):
    """'Name#NA1' -> (puuid, 'Name#NA1'). Raises RiotError if unknown."""
    if "#" not in riot_id:
        raise RiotError(f"'{riot_id}' needs the tag too, like Name#NA1")
    name, tag = riot_id.split("#", 1)
    account = riot_get(
        f"{REGIONAL}/riot/account/v1/accounts/by-riot-id/{name.strip()}/{tag.strip()}",
        allow_404=True)
    if account is None:
        raise RiotError(f"Riot has no account called {riot_id}")
    return account["puuid"], f"{account['gameName']}#{account['tagLine']}"


def current_rank(puuid, name=None, keep=True):
    """This player's ranked TFT standing right now, and write it down.

    Riot serves rank as a single current value with no history, so the only
    way a graph of it can ever exist is to keep every reading. `keep` writes
    a snapshot via rank.record(), which no-ops unless the rank has moved.

    Returns the raw entry (tier / rank / leaguePoints / wins / losses) or
    None for an unranked player. One request.
    """
    import rank as rank_log

    payload = riot_get(f"{PLATFORM}/tft/league/v1/by-puuid/{puuid}",
                       allow_404=True)
    entry = rank_log.entry_from(payload)
    if entry and keep:
        rank_log.record(puuid, entry, name=name)
    return entry


def saved_match_ids():
    """Every match id already on disk, so we never pay for one twice."""
    return {p.stem for p in tft.match_files()}


def backfill(puuid, want=50, set_number=None, progress=None, into=None):
    """Download this player's recent ranked games into the shared pool.

    Returns (games_on_disk_for_them, set_number). Stops as soon as the set
    stops matching - ids come back newest first, so once we are into the
    previous set everything below is older too.

    set_number=None means the newest game this player has defines the set,
    which is what lets a brand-new install work: the answer to "which set are
    we in" comes from the player being looked up, not from the operator.

    Downloads land in pool/, never in matches/. matches/ is exclusively your
    own history - the copy worth committing, because Riot deletes old games -
    and a lookup of some stranger shouldn't silently grow it. The pool is one
    shared folder rather than a folder per player because a match file holds
    all 8 boards: it is one player's game and seven other people's game at the
    same time, so sharing it is both correct and far cheaper than a copy each.
    Nothing already in matches/ is re-downloaded, so your games never duplicate.
    """
    into = into or tft.POOL_DIR
    into.mkdir(exist_ok=True)
    ids = riot_get(f"{REGIONAL}/tft/match/v1/matches/by-puuid/{puuid}/ids",
                   start=0, count=max(want, 1))
    known = saved_match_ids()
    have = 0

    for i, match_id in enumerate(ids, start=1):
        if progress:
            progress(i, len(ids), have)
        if match_id in known:
            have += 1
            continue

        match = riot_get(f"{REGIONAL}/tft/match/v1/matches/{match_id}")
        time.sleep(SLEEP)
        info = match["info"]
        game_set = info.get("tft_set_number") or 0
        if set_number is None:
            set_number = game_set
        if game_set != set_number:
            break
        if info.get("queue_id") != RANKED_QUEUE:
            continue

        (into / f"{match_id}.json").write_text(json.dumps(match))
        have += 1

    return have, set_number


def active_lobby(puuid):
    """Who else is in this player's live game, or None / FORBIDDEN.

    Tested Aug 7 2026: a personal key gets 403 here. Not "not in a game" -
    /featured-games is 403 too and it takes no player, while tft/league and
    tft/summoner on the same host and key return 200. Spectator simply isn't
    in a personal key's permissions, so this needs the production key.
    """
    game = riot_get(f"{PLATFORM}/lol/spectator/tft/v5/active-games/by-puuid/{puuid}",
                    allow_404=True, allow_403=True)
    if game is None or game is FORBIDDEN:
        return game
    return [p["puuid"] for p in game.get("participants", []) if p["puuid"] != puuid]
