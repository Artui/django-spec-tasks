from __future__ import annotations

from collections.abc import Callable
from inspect import signature

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import Client
from django.test.client import RequestFactory
from django.urls import reverse

from django_spec_tasks.admin import SpecRunAdmin
from django_spec_tasks.binding.task_binding import TaskBinding
from django_spec_tasks.models.spec_run import SpecRun
from tests.read.conftest import MARKER

pytestmark = pytest.mark.django_db

MakeRun = Callable[..., SpecRun]


@pytest.fixture
def run(make_binding: Callable[..., TaskBinding], make_run: MakeRun, owner: User) -> SpecRun:
    return make_run(
        make_binding(),
        owner,
        state="FAILED",
        output={"shown": MARKER},
        traceback="Traceback: triage-me",
    )


def _urls(run: SpecRun) -> dict[str, str]:
    return {
        "list": reverse("admin:django_spec_tasks_specrun_changelist"),
        "add": reverse("admin:django_spec_tasks_specrun_add"),
        "change": reverse("admin:django_spec_tasks_specrun_change", args=[run.pk]),
        "delete": reverse("admin:django_spec_tasks_specrun_delete", args=[run.pk]),
    }


def test_the_run_is_registered_with_this_admin() -> None:
    assert isinstance(admin.site._registry[SpecRun], SpecRunAdmin)


def test_even_a_superuser_may_not_add_change_or_delete(admin_client: Client, run: SpecRun) -> None:
    urls = _urls(run)

    assert admin_client.get(urls["add"]).status_code == 403
    assert admin_client.post(urls["add"], {}).status_code == 403
    assert admin_client.get(urls["delete"]).status_code == 403
    assert admin_client.post(urls["delete"], {"post": "yes"}).status_code == 403
    assert SpecRun.objects.filter(pk=run.pk).exists()


def test_the_list_shows_the_columns_and_filters_by_state_and_binding(
    admin_client: Client, run: SpecRun
) -> None:
    response = admin_client.get(_urls(run)["list"])
    model_admin = admin.site._registry[SpecRun]

    assert response.status_code == 200
    assert {"state", "spec_name", "binding", "actor_pk", "attempts"} <= set(
        model_admin.list_display
    )
    assert {"claimed_at", "finished_at", "created_at"} <= set(model_admin.list_display)
    assert set(model_admin.list_filter) == {"state", "binding"}
    for payload in ("params", "carried", "output"):
        assert payload not in model_admin.list_display


def test_the_change_view_holds_no_form_field_and_no_save(
    admin_client: Client, run: SpecRun
) -> None:
    response = admin_client.get(_urls(run)["change"])

    assert response.status_code == 200
    assert response.context["adminform"].form.fields == {}
    content = response.content.decode()
    for control in ("_save", "_continue", "_addanother"):
        assert f'name="{control}"' not in content
    assert "<textarea" not in content


def test_the_change_view_shows_the_traceback_and_hides_the_payloads(
    admin_client: Client, run: SpecRun
) -> None:
    content = admin_client.get(_urls(run)["change"]).content.decode()

    assert "triage-me" in content
    assert MARKER not in content


@pytest.mark.parametrize(
    "query",
    [
        {"params__card": "4111"},
        {"params__card__startswith": "4"},
        {"params": "{}"},
        {"carried__tenant": "acme"},
        {"output__shown__startswith": MARKER[:1]},
    ],
    ids=lambda query: next(iter(query)),
)
def test_a_hidden_payload_cannot_be_queried_through_the_url(
    admin_client: Client, run: SpecRun, query: dict[str, str]
) -> None:
    # A count of results answers a guess, so a hidden value leaks a character at a time.
    response = admin_client.get(_urls(run)["list"], query)

    assert response.status_code == 400


def test_a_column_that_shows_can_still_be_queried_through_the_url(
    admin_client: Client, run: SpecRun
) -> None:
    response = admin_client.get(_urls(run)["list"], {"state__exact": "FAILED"})

    assert response.status_code == 200
    assert str(run.pk) in response.content.decode()


def test_a_subclass_showing_the_payloads_may_query_them_too() -> None:
    class Showing(SpecRunAdmin):
        show_payloads = True

    # Django 4.2 takes the lookup and its value; later versions add the request.
    takes_request = "request" in signature(admin.ModelAdmin.lookup_allowed).parameters
    rest = ("x", None) if takes_request else ("x",)

    assert Showing(SpecRun, admin.site).lookup_allowed("output__shown", *rest)
    assert not SpecRunAdmin(SpecRun, admin.site).lookup_allowed("output__shown", *rest)


def test_a_change_post_changes_nothing(admin_client: Client, run: SpecRun) -> None:
    response = admin_client.post(_urls(run)["change"], {"state": "SUCCEEDED"})

    assert response.status_code == 403
    assert SpecRun.objects.get(pk=run.pk).state == "FAILED"


def test_the_list_offers_no_actions_and_an_action_post_deletes_nothing(
    admin_client: Client, run: SpecRun
) -> None:
    # An admin action skips the change permission, so there are none to offer.
    model_admin = admin.site._registry[SpecRun]
    request = RequestFactory().get("/")
    request.user = User(is_superuser=True, is_staff=True)

    admin_client.post(
        _urls(run)["list"],
        {"action": "delete_selected", "_selected_action": [str(run.pk)], "post": "yes", "index": 0},
    )

    assert model_admin.get_actions(request) == {}
    assert SpecRun.objects.filter(pk=run.pk).exists()


def test_a_subclass_may_show_the_payloads_and_still_changes_nothing(
    admin_client: Client, run: SpecRun
) -> None:
    class Payloads(SpecRunAdmin):
        show_payloads = True

    request = RequestFactory().get("/")
    shown = Payloads(SpecRun, admin.site)
    hidden = SpecRunAdmin(SpecRun, admin.site)

    assert {"params", "carried", "output"} <= set(shown.get_fields(request))
    assert {"params", "carried", "output"}.isdisjoint(hidden.get_fields(request))
    assert "traceback" in hidden.get_fields(request)
    assert set(shown.get_readonly_fields(request)) == set(shown.get_fields(request))
    assert not shown.has_change_permission(request)


def test_a_site_wide_action_is_not_offered_either(admin_client: Client, run: SpecRun) -> None:
    # An action on the site needs no permission to be listed; only actions = None keeps it off.
    def noop(modeladmin: object, request: object, queryset: object) -> None: ...

    admin.site.add_action(noop, "noop")
    try:
        request = RequestFactory().get("/")
        request.user = User(is_superuser=True, is_staff=True)
        assert admin.site._registry[SpecRun].get_actions(request) == {}
    finally:
        admin.site._actions.pop("noop")
