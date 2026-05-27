# NetBox Prometheus HTTP SD Export Templates

Two NetBox export templates that emit Prometheus `http_sd_config` JSON:

| File | Bind to | Purpose |
|---|---|---|
| [device-prometheus-sd.j2](device-prometheus-sd.j2) | `dcim.device` | One target per device that has a `prometheus-plugin-prometheus-sd` config context entry |
| [service-prometheus-sd.j2](service-prometheus-sd.j2) | `ipam.service` | One target per (service, port) pair, with labels inherited from the parent device/VM |

See [CLAUDE.md](CLAUDE.md) for the full data model, label reference, exporter-routing rules, and Jinja gotchas.

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
    "prometheus-plugin-prometheus-sd": {
        "port": 9100,
        "metrics_path": "/metrics",
        "scheme": "http"
    }
}
```

Devices without this key are silently skipped, so it's safe to scope the context broadly.

For exporter-routed scrapes (snmp_exporter, fortigate_exporter, blackbox-style probes via a translating exporter), add `exporter`, `target_scheme`, and `params` — full reference in [CLAUDE.md](CLAUDE.md#exporter-routing-snmp_exporter-fortigate_exporter-etc).

## Per-device overrides

Set a custom field named `prometheus_exporter_<param>` on the device to override (or add) a single param without forking the config context. Multi-select custom fields are CSV-joined automatically.
