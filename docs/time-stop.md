# Time stop

`time_stop_at` is the date when a trade thesis expires and a fresh Forseti
analysis is required. It is not broker order expiry and does not represent
TradingView GTD or another time-in-force setting.

For version one, a trade recommendation is assumed to be executed immediately.
The recommendation date is day zero, and `time_stop_at` is the 40th XNYS
trading session strictly after that date. Weekends and full-day NYSE holidays
are skipped; early-close sessions count.

Trade recommendations contain the calculated date. `watchlist` and `no_trade`
recommendations contain `null`, because no position should be opened.
Reaching the date means reassess with a fresh recommendation; it does not
automatically sell or otherwise manage a broker position.

Future work may calibrate the duration from historical outcomes or incorporate
other strategy information. Those adjustments are outside this version.
