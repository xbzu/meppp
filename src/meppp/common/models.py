import uuid

from django.core.exceptions import ValidationError
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicModel(TimeStampedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class AppendOnlyQuerySet(models.QuerySet):
    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts:
            raise ValidationError("Append-only records cannot be updated")
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def update(self, **kwargs):
        raise ValidationError("Append-only records cannot be updated")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Append-only records cannot be updated")

    def delete(self):
        raise ValidationError("Append-only records cannot be deleted")

    def _raw_delete(self, using):
        raise ValidationError("Append-only records cannot be deleted")


class AppendOnlyManager(models.Manager.from_queryset(AppendOnlyQuerySet)):
    pass


class AppendOnlyPublicModel(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        existing_primary_key = (
            self.pk is not None and type(self)._base_manager.filter(pk=self.pk).exists()
        )
        if not self._state.adding or existing_primary_key:
            raise ValidationError("Append-only records cannot be updated")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Append-only records cannot be deleted")
