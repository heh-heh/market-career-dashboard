# Toss 1-minute historical data

The EC2 server can backfill historical 1-minute OHLCV candles directly from the Toss Securities Open API. Credentials remain in server_secrets.json and are never committed.

## Run on EC2

From /home/ubuntu/market-career-dashboard:

sudo -u root /home/ubuntu/market-career-dashboard/.venv/bin/python scripts/collect_toss_1m.py --symbols NVDA,AMD,INTC,SOXL,SOXS,TQQQ --since 2021-01-01T00:00:00+00:00

Data is stored outside Git history at data/toss_1m/*.csv.gz.

The collector follows Toss nextBefore pagination and requests at most 200 1-minute candles per page. It is safe to rerun: timestamps are deduplicated.

A missing/limited history response is reported by the number of saved rows; the collector does not fabricate older candles.
