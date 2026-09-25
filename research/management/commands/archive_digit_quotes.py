from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from research.models import ProposalSnapshot
from research.services.deriv_client import DerivPublicClient
from research.services.cross_market import (
    synthetic_symbol_rows,
    quote_reference_grid,
)


class Command(BaseCommand):
    help = (
        "Archive public Deriv digit proposal prices into ProposalSnapshot. "
        "Use --interval for a continuous paid-worker collector."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbols",
            default="",
            help=(
                "Comma-separated symbol codes. If omitted, currently active "
                "Volatility 1s-style symbols are preferred dynamically."
            ),
        )
        parser.add_argument("--stake", type=float, default=1.0)
        parser.add_argument(
            "--interval",
            type=int,
            default=0,
            help=(
                "Seconds between archive rounds. 0 runs one round and exits. "
                "Minimum continuous interval is 60 seconds."
            ),
        )
        parser.add_argument(
            "--limit-symbols",
            type=int,
            default=5,
            help="Maximum dynamically discovered symbols when --symbols is omitted.",
        )

    def handle(self, *args, **opts):
        interval = int(opts["interval"] or 0)
        if interval and interval < 60:
            interval = 60

        while True:
            saved, failed = self.archive_round(
                symbols_arg=opts["symbols"],
                stake=float(opts["stake"]),
                limit_symbols=max(1, min(int(opts["limit_symbols"]), 20)),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Proposal archive round complete: {saved} saved, {failed} failed"
                )
            )

            if interval <= 0:
                return

            time.sleep(interval)

    def archive_round(self, *, symbols_arg, stake, limit_symbols):
        client = DerivPublicClient()

        if symbols_arg.strip():
            symbols = [
                value.strip()
                for value in symbols_arg.split(",")
                if value.strip()
            ]
        else:
            rows = synthetic_symbol_rows(client)
            preferred = [
                row["code"]
                for row in rows
                if row["code"].startswith("1HZ")
                and "volatility" in str(row["name"]).lower()
            ]
            fallback = [
                row["code"]
                for row in rows
                if row["code"] not in preferred
            ]
            symbols = (preferred + fallback)[:limit_symbols]

        saved = 0
        failed = 0

        for symbol in symbols:
            for quote in quote_reference_grid(client, symbol, stake=stake):
                if not quote["ok"]:
                    failed += 1
                    self.stderr.write(
                        f'{symbol} {quote["label"]}: {quote["error"]}'
                    )
                    continue

                ProposalSnapshot.objects.create(
                    symbol=symbol,
                    contract_type=quote["contract_type"],
                    barrier=quote["barrier"] or "",
                    stake=stake,
                    ask_price=quote["ask_price"],
                    payout=quote["payout"],
                    break_even_pct=quote["break_even_pct"],
                    model_probability_pct=0,
                    lower95_pct=0,
                    edge_pp=0,
                    sample_ticks=0,
                    decision="ARCHIVE V1.2.1",
                )
                saved += 1

        return saved, failed
