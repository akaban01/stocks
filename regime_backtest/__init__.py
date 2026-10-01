"""Market-regime backtest: do common "get out of the market" rules beat buy-and-hold
on the S&P 500 after costs?

Self-contained and run on demand (``python -m regime_backtest.run``). Nothing in the
scanner imports it and it imports nothing from the scanner except the retry helper
in ``spread_scanner.net``. Downloads are cached in ``regime_backtest/data/`` and the
report lands in ``regime_backtest/output/``; both are gitignored, and the credit
spread data in particular must never be committed (ICE licence).
"""
