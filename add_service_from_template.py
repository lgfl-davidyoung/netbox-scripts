from django.contrib.contenttypes.models import ContentType

from extras.scripts import Script, MultiObjectVar, ObjectVar, BooleanVar
from ipam.models import Service, ServiceTemplate
from dcim.models import Device
from virtualization.models import VirtualMachine


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
            ("Options", ("overwrite",)),
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
        overwrite: bool = data["overwrite"]

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
                    if commit:
                        existing.full_clean()
                        existing.save()
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

            if commit:
                service.full_clean()
                service.save()

            self.log_success(
                f"Created service **{template.name}** on {target} "
                f"({target.__class__.__name__})."
            )
            created += 1

        self.log_info(
            f"Done — created: {created}, updated: {updated}, skipped: {skipped}."
        )
