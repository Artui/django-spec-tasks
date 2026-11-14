from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorSpec, ServiceSpec, SpecSource, file_output


def refuse_file_result(
    binding_name: str, spec_name: str, spec: ServiceSpec | SelectorSpec | SpecSource
) -> None:
    """Raise ``ImproperlyConfigured`` when ``spec`` presents a file, which a run cannot store."""
    try:
        presenter = file_output(spec)
    except ImproperlyConfigured as error:
        raise ImproperlyConfigured(
            f"TaskBinding {binding_name!r} cannot run spec {spec_name!r}: {error}"
        ) from error
    if presenter is not None:
        raise ImproperlyConfigured(
            f"TaskBinding {binding_name!r} cannot run spec {spec_name!r}: it presents a file "
            "through FilePresenter, and file results are not stored by runs. Serve it from a "
            "transport that answers with the file."
        )
