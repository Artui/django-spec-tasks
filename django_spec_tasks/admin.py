from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models.constants import LOOKUP_SEP
from django.http import HttpRequest

from django_spec_tasks.models.spec_run import SpecRun

_PAYLOADS = frozenset({"params", "carried", "output"})


@admin.register(SpecRun)
class SpecRunAdmin(admin.ModelAdmin):
    """Runs, to look at and never to change.

    Nothing here adds, changes or deletes a row, and there are no actions: an admin action
    skips the change permission, so an action that deleted would delete for a user who
    could not. ``params``, ``carried`` and ``output`` hold what the call was given and what
    it returned, so they are hidden, and refused as URL lookups, since a count of results
    would answer a guess; a project that wants them in its admin sets ``show_payloads`` on a
    subclass. The ``traceback`` shows, because triage needs it.
    """

    show_payloads = False

    list_display = (
        "id",
        "state",
        "spec_name",
        "binding",
        "actor_pk",
        "attempts",
        "created_at",
        "claimed_at",
        "finished_at",
    )
    list_filter = ("state", "binding")
    search_fields = ("spec_name", "=actor_pk")
    ordering = ("-created_at",)
    show_full_result_count = False
    actions = None

    def get_fields(self, request: HttpRequest, obj: Any = None) -> list[Any]:
        names = [field.name for field in SpecRun._meta.concrete_fields]
        return [name for name in names if self.show_payloads or name not in _PAYLOADS]

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        return self.get_fields(request, obj)

    def lookup_allowed(self, lookup: str, *args: Any, **kwargs: Any) -> bool:
        # The rest of the signature differs across the Django versions this package supports.
        if not self.show_payloads and lookup.split(LOOKUP_SEP)[0] in _PAYLOADS:
            return False
        return super().lookup_allowed(lookup, *args, **kwargs)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
