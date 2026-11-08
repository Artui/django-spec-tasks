from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from django_spec_tasks.sweep.sweep_runs import sweep_runs


class Command(BaseCommand):
    help = (
        "Ask the backend again for runs it seems to have lost, and end the runs whose "
        "worker did not come back. Run it on the project's own schedule."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="The most rows one sweep looks at for one binding.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = sweep_runs(limit=options["limit"])
        self.stdout.write(
            f"Swept spec runs: {report.requeued} requeued, {report.abandoned} abandoned, "
            f"{report.unavailable} spec unavailable, {report.reasked} asked again, "
            f"{report.errors} errors."
        )
