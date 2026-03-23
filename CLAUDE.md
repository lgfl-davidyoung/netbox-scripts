# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

NetBox custom scripts and reports for London Grid for Learning Trust.
Target platform: **NetBox 4.5.x** (currently 4.5.4).

## Repository Layout

```
scripts/    # NetBox custom scripts — drop files here
reports/    # NetBox custom reports — drop files here
```

Place new scripts in `scripts/` and new reports in `reports/`. Do not create subdirectories inside these folders.

## NetBox 4.5 API Notes

### Service model (ipam.models.Service)
In NetBox 4.5 the `Service` model uses a **GenericForeignKey** — there are no longer separate `device` and `virtual_machine` FK fields. Use:

```python
from django.contrib.contenttypes.models import ContentType

ct = ContentType.objects.get_for_model(target)
service = Service(
    parent_object_type=ct,
    parent_object_id=target.pk,
    ...
)
```

Filter existing services the same way:
```python
Service.objects.filter(parent_object_type=ct, parent_object_id=target.pk, name=...)
```

### Tags
Tags are a ManyToManyField — assign **after** `save()`:
```python
service.save()
service.tags.set(tags)
```

### Script base class
```python
from extras.scripts import Script, ObjectVar, MultiObjectVar, BooleanVar
```

Always call `full_clean()` before `save()` inside a `if commit:` block.

## Code Style

- Keep scripts self-contained in a single file.
- Use `fieldsets` in the `Meta` class to group form fields logically.
- Log every action with the appropriate level (`log_success`, `log_info`, `log_warning`, `log_failure`).
- Print a summary line at the end of `run()` with created/updated/skipped counts.
- Use `from __future__ import annotations` only if needed; avoid unnecessary imports.
