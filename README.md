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

## Manual run

```
BIN_PROPERTY_ID=<id> python bin_dates.py -o bin_dates.json --ics bin_dates.ics
```

## Failure handling

If the property id is wrong, the flow changes, or any of the three dates
can't be found, the script exits non-zero without writing anything, so the
last good feed stays published and the workflow run shows as failed.
