from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from django_spec_tasks.sweep.prune_runs import prune_runs


class Command(BaseCommand):
    help = (
        "Delete runs that finished more than --older-than days ago. A run that is still "
        "pending or running is never deleted."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        # No default: how long to keep a run is the project's decision, not the package's.
        parser.add_argument("--older-than", type=int, required=True, metavar="DAYS")

    def handle(self, *args: Any, **options: Any) -> None:
        days: int = options["older_than"]
        if days < 0:
            raise CommandError("--older-than must not be negative.")
        deleted = prune_runs(older_than=timedelta(days=days))
        self.stdout.write(f"Deleted {deleted} {'run' if deleted == 1 else 'runs'}.")
