# bin-collection-dates

Scrapes Sevenoaks District Council's "waste collection day" tool into an
`.ics` calendar feed of bin collections (general waste, recycling, garden
waste) that Google Calendar can subscribe to via "Add calendar > From URL".
A GitHub Actions workflow refreshes it daily
(`.github/workflows/update-bin-dates.yml`).

## Subscribe

```
https://raw.githubusercontent.com/oam100/bin-collection-dates/main/bin_dates.ics
```

## How it works

The council's tool only hands out short-lived, single-use schedule links,
so `bin_dates.py` replays the address-selection flow each run using the
council's internal property id (the `id=` value in a schedule link). See
the docstring in `bin_dates.py` for the request sequence.

The property id identifies a home address, so it's never committed: the
workflow reads it from the `BIN_PROPERTY_ID` repository secret. The
generated files contain only bin types and dates.

The council only publishes the *next* date per bin. Past collections are
carried over from the previous `bin_dates.json` so they stay in the
calendar; future dates are never estimated.

## Bin-night Alexa notification

At 19:30 UK time the workflow refreshes the dates and, if anything is
collected tomorrow, `notify_bins.py` sends an Alexa notification through
the [Notify Me](https://www.thomptronics.com/about/notify-me) skill, e.g.
"Bin night: put the rubbish and garden waste out tonight." The Echo chimes
and shows a yellow ring; "Alexa, what are my notifications?" reads it out.

Needs the `NOTIFYME_ACCESS_CODE` repository secret (the code Notify Me
emails you). Cron is UTC-only, so 18:30 and 19:30 UTC are both scheduled
and the workflow keeps whichever is 19:30 in the UK that day. GitHub may
start scheduled runs some minutes late.

To test: Actions > Update bin dates > Run workflow, tick "Also send the
bin-night notification" (only sends if tomorrow is a collection day), or
locally: `python notify_bins.py --dry-run --date YYYY-MM-DD`.

## Manual run

```
BIN_PROPERTY_ID=<id> python bin_dates.py -o bin_dates.json --ics bin_dates.ics
```

## Failure handling

If the property id is wrong, the flow changes, or any of the three dates
can't be found, the script exits non-zero without writing anything, so the
last good feed stays published and the workflow run shows as failed.
