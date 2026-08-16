#!/bin/sh
# Keep crawling until a wall-clock deadline, restarting if the crawl dies.
#
# The first attempt at this file simply chained a second window behind the
# first. That was not enough: a ReadTimeout to americas killed a run 83
# minutes in and the night went idle. riot.py now waits out a dropped
# connection, but a crawl that runs for sixteen hours will find new ways to
# fall over, and none of them are worth losing a night of a patch that
# cannot be re-crawled later.
#
# So: restart on any early exit, and tell apart the two reasons a crawl
# stops early. A crash lasts minutes and is worth retrying. An expired API
# key fails in seconds, every time, and no amount of retrying will fix it -
# a dev key lasts 24 hours and only a human can paste a new one. Three
# instant failures in a row is that, so stop and say so.
#
#   sh overnight.sh <hours>

HOURS="${1:-12}"
cd "$(dirname "$0")" || exit 1

END=$(( $(date +%s) + $(printf '%.0f' "$(echo "$HOURS * 3600" | bc)") ))
INSTANT=0

while [ "$(date +%s)" -lt "$END" ]; do
    LEFT=$(( END - $(date +%s) ))
    STARTED=$(date +%s)

    .venv/bin/python crawl.py --hours "$(echo "scale=3; $LEFT / 3600" | bc)" \
        >> corpus/crawl.log 2>&1
    RAN=$(( $(date +%s) - STARTED ))

    [ "$(date +%s)" -ge "$END" ] && break

    if [ "$RAN" -lt 60 ]; then
        INSTANT=$(( INSTANT + 1 ))
        if [ "$INSTANT" -ge 3 ]; then
            echo "--- gave up at $(date '+%H:%M'): three instant failures, the" \
                 "API key has almost certainly expired. Paste a fresh one into" \
                 ".riot_key and run: sh overnight.sh 8 ---" >> corpus/crawl.log
            exit 1
        fi
    else
        INSTANT=0
    fi

    echo "--- crawl stopped after ${RAN}s, restarting at $(date '+%H:%M') ---" \
        >> corpus/crawl.log
    sleep 10
done

echo "--- reached the deadline at $(date '+%H:%M') ---" >> corpus/crawl.log
