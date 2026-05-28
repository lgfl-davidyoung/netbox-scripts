# NetBox Prometheus HTTP SD Export Templates

Two NetBox export templates that emit Prometheus `http_sd_config` JSON:

| File | Bind to | Purpose |
|---|---|---|
| [device-prometheus-sd.j2](device-prometheus-sd.j2) | `dcim.device` | One target per device that has a `prometheus-export-template` config context entry |
| [service-prometheus-sd.j2](service-prometheus-sd.j2) | `ipam.service` | One target per (service, port) pair, with labels inherited from the parent device/VM |

See [CLAUDE.md](CLAUDE.md) for the full data model, label reference, exporter-routing rules, and Jinja gotchas. A NetBox-compatible JSON Schema for the config-context shape lives at [prometheus-export-template.schema.json](prometheus-export-template.schema.json) — upload it as a Config Context Profile (Extras → Config Context Profiles → Add) to get validation in the NetBox UI.

## Install

1. NetBox → Customization → Export Templates → **Add**.
2. **Name:** `prometheus-sd-devices` (or `-services`).
3. **Content type:** `dcim | device` (or `ipam | service`).
4. **MIME type:** `application/json`.
5. **Template code:** paste the contents of the matching `.j2` file.
6. Save. Grab the numeric template ID from the URL or the API.

Repeat for the service template.

## Wire up Prometheus

See [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) for an example fragment. Substitute the template IDs you got at install time.

## Opt a device in

Add a config context (device, role, site, tenant, etc.) containing:

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "metrics_path": "/metrics",
        "scheme": "http"
    }
}
```

Devices without this key are silently skipped, so it's safe to scope the context broadly.

For exporter-routed scrapes (snmp_exporter, fortigate_exporter, blackbox-style probes via a translating exporter), add `exporter`, `scheme`, and `params` — full reference in [CLAUDE.md](CLAUDE.md#exporter-routing-snmp_exporter-fortigate_exporter-etc).

To override Prometheus' job-level scrape cadence on specific devices, add `scrape_interval` and/or `scrape_timeout` (Prometheus duration strings like `30s`, `1m`):

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "scrape_interval": "30s",
        "scrape_timeout": "10s"
    }
}
```

These become `__scrape_interval__` / `__scrape_timeout__` meta-labels and are consumed by Prometheus directly — no relabel rule needed.

## OOB IP routing (iDRAC, BMCs)

For physical servers that need both a software scrape (primary IP, varies by OS) and an iDRAC/BMC scrape (OOB IP, identical per hardware vendor), use a **separate** top-level config-context key — `prometheus-export-template-oob` — so the two concerns can be scoped independently in NetBox. Typical setup:

- A role-scoped context (e.g. "Linux Server", "Windows Server") supplies `prometheus-export-template` with the appropriate exporter port.
- A manufacturer-scoped context (e.g. "Dell") supplies `prometheus-export-template-oob` once for the whole fleet.

NetBox merges both onto each device:

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "metrics_path": "/metrics",
        "scheme": "http"
    },
    "prometheus-export-template-oob": {
        "port": 443,
        "scheme": "https",
        "exporter": "idrac-exporter.internal.lgfl.net:9348",
        "metrics_path": "/metrics"
    }
}
```

Devices without an OOB IP populated skip the OOB row automatically. Devices that only need OOB monitoring can omit `prometheus-export-template` entirely. Custom-field overrides apply to the primary target only — the OOB context is self-contained. Full rules in [CLAUDE.md](CLAUDE.md#oob-ip-routing-idrac-bmcs).

## Per-device overrides

Set a custom field named `prometheus_exporter_<param>` on the device to override (or add) a single param without forking the config context. Multi-select custom fields are CSV-joined automatically.

`prometheus_exporter_scrape_interval` and `prometheus_exporter_scrape_timeout` are special-cased: they override the matching config-context keys and are emitted as meta-labels rather than `__param_*` labels.
