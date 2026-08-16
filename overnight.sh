#!/bin/sh
# Wait for the running crawl to hit its own time limit, then start another
# window so the night is not spent idle. The first run saves its queue on the
# way out, so the second picks up mid-stride rather than reseeding.
#
# Both runs stop by themselves when the API key expires - a dead key raises,
# the crawler writes its state and exits, and this script ends. Nothing here
# needs to know when that happens.
#
#   sh overnight.sh <pid-of-running-crawl> <hours-for-the-second-run>

PID="$1"
HOURS="${2:-10}"
cd "$(dirname "$0")" || exit 1

while ps -p "$PID" > /dev/null 2>&1; do
    sleep 60
done

echo "--- first window done, starting $HOURS more at $(date '+%H:%M') ---" \
    >> corpus/crawl.log
.venv/bin/python crawl.py --hours "$HOURS" >> corpus/crawl.log 2>&1
echo "--- second window done at $(date '+%H:%M') ---" >> corpus/crawl.log
