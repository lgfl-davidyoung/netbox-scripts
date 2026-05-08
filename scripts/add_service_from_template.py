from django.contrib.contenttypes.models import ContentType

from extras.models import Tag
from extras.scripts import (
    Script,
    MultiObjectVar,
    ObjectVar,
    BooleanVar,
    StringVar,
    IntegerVar,
    TextVar,
    ChoiceVar,
    MultiChoiceVar,
)
from ipam.models import Service, ServiceTemplate
from dcim.models import Device
from virtualization.models import VirtualMachine


def _cf_value_for_storage(value):
    """Convert a script var value to what custom_field_data expects (PKs, not instances)."""
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (list, tuple)):
        return [v.pk if hasattr(v, "pk") else v for v in value]
    return value


class AddServiceFromTemplate(Script):
    class Meta:
        name = "Add Application Service from Template"
        description = (
            "Apply a service template to one or more devices and/or virtual machines, "
            "creating an application service on each. Skips targets that already have "
            "a service with the same name."
        )
        fieldsets = (
            ("Service Template", ("template",)),
            ("Targets", ("devices", "virtual_machines")),
            ("Options", ("tags", "overwrite")),
        )
        commit_default = True

    template = ObjectVar(
        model=ServiceTemplate,
        label="Service Template",
        description="The service template to apply.",
    )

    devices = MultiObjectVar(
        model=Device,
        label="Devices",
        required=False,
        description="Devices to add the service to.",
    )

    virtual_machines = MultiObjectVar(
        model=VirtualMachine,
        label="Virtual Machines",
        required=False,
        description="Virtual machines to add the service to.",
    )

    tags = MultiObjectVar(
        model=Tag,
        label="Tags",
        required=False,
        description="Tags to apply to each created or updated service.",
    )

    overwrite = BooleanVar(
        label="Overwrite existing",
        description=(
            "If a service with the same name already exists on a target, "
            "overwrite its ports, protocol, and description."
        ),
        default=False,
    )

    def run(self, data, commit):
        template: ServiceTemplate = data["template"]
        devices = list(data.get("devices") or [])
        vms = list(data.get("virtual_machines") or [])
        tags = list(data.get("tags") or [])
        overwrite: bool = data["overwrite"]

        cf_data = {
            key[3:]: _cf_value_for_storage(value)
            for key, value in data.items()
            if key.startswith("cf_") and value not in (None, "", [])
        }

        if not devices and not vms:
            self.log_failure("No devices or virtual machines selected.")
            return

        targets = list(devices) + list(vms)

        created = 0
        updated = 0
        skipped = 0

        for target in targets:
            ct = ContentType.objects.get_for_model(target)

            existing = Service.objects.filter(
                name=template.name,
                parent_object_type=ct,
                parent_object_id=target.pk,
            ).first()

            if existing:
                if overwrite:
                    existing.protocol = template.protocol
                    existing.ports = template.ports
                    existing.description = template.description
                    if cf_data:
                        existing.custom_field_data.update(cf_data)
                    if commit:
                        existing.full_clean()
                        existing.save()
                        if tags:
                            existing.tags.set(tags)
                    self.log_info(
                        f"Updated existing service **{template.name}** on {target} "
                        f"({target.__class__.__name__})."
                    )
                    updated += 1
                else:
                    self.log_warning(
                        f"Skipped {target} ({target.__class__.__name__}): service "
                        f"**{template.name}** already exists."
                    )
                    skipped += 1
                continue

            service = Service(
                name=template.name,
                protocol=template.protocol,
                ports=template.ports,
                description=template.description,
                parent_object_type=ct,
                parent_object_id=target.pk,
            )

            if cf_data:
                service.custom_field_data = cf_data

            if commit:
                service.full_clean()
                service.save()
                if tags:
                    service.tags.set(tags)

            self.log_success(
                f"Created service **{template.name}** on {target} "
                f"({target.__class__.__name__})."
            )
            created += 1

        self.log_info(
            f"Done — created: {created}, updated: {updated}, skipped: {skipped}."
        )


def _normalize_cf_choices(raw):
    """Return a flat list of (value, label) 2-tuples regardless of how NetBox stores them.

    cf.choices may come back as plain strings ['a', 'b'], 2-tuples [('a', 'A'), ...],
    or 3-tuples [('a', 'A', 'icon'), ...].  Wrapping non-tuple items naively produces
    nested tuples that Django's Select widget misreads as optgroups, eventually trying
    to unpack a string character-by-character and raising ValueError.
    """
    if not raw:
        return []
    result = []
    for c in raw:
        if isinstance(c, (list, tuple)):
            result.append((c[0], c[1] if len(c) > 1 else c[0]))
        else:
            result.append((c, c))
    return result


# Dynamically add a form var for each custom field defined on the Service model.
# This block runs at module import time so the fields appear in the script form.
try:
    from extras.models import CustomField as _CF

    _service_ct = ContentType.objects.get_for_model(Service)
    _cf_list = list(_CF.objects.filter(object_types=_service_ct).order_by("name"))
    _cf_var_names = []

    for _cf in _cf_list:
        try:
            _var_name = f"cf_{_cf.name}"
            _label = _cf.label or _cf.name.replace("_", " ").title()
            _common = dict(label=_label, description=_cf.description or "", required=_cf.required)
            _t = _cf.type

            if _t in ("text", "url", "date", "datetime"):
                _var = StringVar(**_common)
            elif _t == "longtext":
                _var = TextVar(**_common)
            elif _t in ("integer", "decimal"):
                _var = IntegerVar(**_common)
            elif _t == "boolean":
                # required=True on a checkbox means "must be ticked"; leave that to full_clean()
                _var = BooleanVar(label=_label, description=_common["description"])
            elif _t == "select":
                _var = ChoiceVar(choices=_normalize_cf_choices(_cf.choices), **_common)
            elif _t == "multiselect":
                _var = MultiChoiceVar(choices=_normalize_cf_choices(_cf.choices), **_common)
            elif _t in ("object", "multiobject"):
                _model = _cf.related_object_type.model_class()
                _var = (ObjectVar if _t == "object" else MultiObjectVar)(model=_model, **_common)
            elif _t == "json":
                _var = TextVar(**_common)
            else:
                _var = StringVar(**_common)

            setattr(AddServiceFromTemplate, _var_name, _var)
            _cf_var_names.append(_var_name)
        except Exception:
            pass  # skip any single CF that can't be represented as a form var

    if _cf_var_names:
        AddServiceFromTemplate.Meta.fieldsets = (
            *AddServiceFromTemplate.Meta.fieldsets,
            ("Custom Fields", tuple(_cf_var_names)),
        )
except Exception:
    pass
