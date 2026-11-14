from __future__ import annotations

from django.db import models


class Order(models.Model):
    """A target: a row a service mutates, with a ``Decimal`` for a presenter to render."""

    reference = models.CharField(max_length=64)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=32, default="open")

    def __str__(self) -> str:
        return self.reference


class Note(models.Model):
    """A plain row, for asserting what committed and what rolled back."""

    text = models.CharField(max_length=255)

    def __str__(self) -> str:
        return self.text


class Document(models.Model):
    """A target whose primary key is longer than any 255-character column could hold."""

    key = models.CharField(max_length=400, primary_key=True)
    title = models.CharField(max_length=64, default="")

    def __str__(self) -> str:
        return self.title
