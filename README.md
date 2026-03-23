# NetBox Scripts

Custom scripts and reports for [NetBox](https://netboxlabs.com/docs/netbox/) — the network source of truth platform used at London Grid for Learning Trust.

## Repository Structure

```
netbox-scripts/
├── scripts/          # Custom scripts (run via Customization → Scripts in NetBox UI)
│   └── add_service_from_template.py
├── reports/          # Custom reports (run via Customization → Reports in NetBox UI)
└── README.md
```

## Scripts

### `add_service_from_template.py` — Add Application Service from Template

Applies a NetBox **Service Template** to one or more devices and/or virtual machines, creating an application service on each target.

**Form fields**

| Field | Required | Description |
|---|---|---|
| Service Template | Yes | The template to apply (name, protocol, ports, and description are copied from it) |
| Devices | No | One or more devices to add the service to |
| Virtual Machines | No | One or more virtual machines to add the service to |
| Tags | No | Tags to apply to each created or updated service |
| Overwrite existing | No | If a service with the same name already exists on a target, update it instead of skipping |

**Behaviour**
- At least one device or virtual machine must be selected.
- Uses NetBox's dry-run (uncheck **Commit changes**) to preview actions without writing to the database.
- Logs each created, updated, or skipped service individually, plus a final summary.

## Installation

Copy scripts into the `scripts/` directory of your NetBox installation (or the path configured in `SCRIPTS_ROOT`), then navigate to **Customization → Scripts** in the NetBox UI and click **Sync** (or restart the worker).

## Requirements

- NetBox 4.5+
