# NetBox Prometheus HTTP SD Export Templates

NetBox export templates that emit Prometheus `http_sd_config` JSON: a device primary-IP template and a device OOB template (both exporter-routable, node_exporter / snmp_exporter / idrac_exporter / etc.), a service template (direct scrape with a per-service target override), and one consolidated blackbox-probe template (ICMP, TCP, HTTP, DNS, ...).

| File | Bind to | Purpose |
|---|---|---|
| [device-prometheus-sd.j2](device-prometheus-sd.j2) | `dcim.device` | One target per active device that has a `prometheus-export-template` config context entry and a primary IP |
| [device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2) | `dcim.device` | One target per active device that has a `prometheus-export-template-oob` config context entry and an OOB IP (iDRAC/iLO/BMC). Independent of the primary template. |
| [service-prometheus-sd.j2](service-prometheus-sd.j2) | `ipam.service` | One direct-scrape target per (service, port) pair (`parent IP:port`), with labels from the parent device/VM. Per-service `prometheus_scrape_url` custom field overrides the target verbatim. |
| [blackbox-prometheus-sd.j2](blackbox-prometheus-sd.j2) | `dcim.device` + `virtualization.virtualmachine` + `ipam.service` + `ipam.ipaddress` | One blackbox row per (object, module). Per-row dispatch picks the probe target: `primary_ip` for devices/VMs, the IP itself for IP addresses, `parent.primary_ip:ports[0]` (first port only) for services. |

The three files [blackbox-device-vm-sd.j2](blackbox-device-vm-sd.j2), [blackbox-service-sd.j2](blackbox-service-sd.j2), and [blackbox-ipaddress-sd.j2](blackbox-ipaddress-sd.j2) are deprecated — they were the first iteration of blackbox support and have been replaced by the single consolidated template above. They remain in the tree temporarily and can be removed once the consolidated template is verified working.

See [CLAUDE.md](CLAUDE.md) for the full data model, label reference, exporter-routing rules, and Jinja gotchas. The two device-level config-context keys each have a NetBox-compatible Config Context Profile — [profile-prometheus-export-template.json](profile-prometheus-export-template.json) and [profile-prometheus-export-template-oob.json](profile-prometheus-export-template-oob.json). Upload each as a Config Context Profile (Extras → Config Context Profiles → Add) and assign it to the ConfigContexts that set the matching key, to get validation in the NetBox UI. (Services are not config-context-driven — there is no service profile.)

## Install

1. NetBox → Customization → Export Templates → **Add**.
2. **Name:** `prometheus-sd-devices` (or `-services`).
3. **Content type:** `dcim | device` (or `ipam | service`).
4. **MIME type:** `application/json`.
5. **Template code:** paste the contents of the matching `.j2` file.
6. Save. Grab the numeric template ID from the URL or the API.

Repeat for the OOB device template ([device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2), also content type `dcim | device`) and the service template.

## Wire up Prometheus

See [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) for an example fragment. Substitute the template IDs you got at install time.

## Opt a device in

Add a config context (device, role, site, tenant, etc.) containing:

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "exporter_type": "node_exporter",
        "metrics_path": "/metrics",
        "scheme": "http"
    }
}
```

`port` and `exporter_type` are both optional. Omit `port` to emit a portless target (Prometheus uses the scheme's default port). When set, `exporter_type` becomes a label on every emitted target so queries can filter by exporter family (`up{exporter_type="node_exporter"}`); when omitted, no such label is emitted.

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
        "exporter_type": "node_exporter",
        "metrics_path": "/metrics",
        "scheme": "http"
    },
    "prometheus-export-template-oob": {
        "port": 443,
        "exporter_type": "idrac_exporter",
        "scheme": "https",
        "exporter": "idrac-exporter.internal.lgfl.net:9348",
        "metrics_path": "/metrics"
    }
}
```

The two keys are rendered by **two separate export templates**, each bound to `dcim.device` and scraped by its own job: [device-prometheus-sd.j2](device-prometheus-sd.j2) (`?export=prometheus-device`) reads `prometheus-export-template`, and [device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2) (`?export=prometheus-device-oob`) reads `prometheus-export-template-oob`. Install the OOB template the same way as the primary one (content type `dcim | device`, MIME `application/json`) and wire up the `netbox-sd-devices-oob` job in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml).

Devices without an OOB IP populated skip the OOB row automatically. Devices that only need OOB monitoring can omit `prometheus-export-template` entirely (and vice versa). `prometheus_exporter_*` custom-field overrides apply to the primary template only — the OOB context is self-contained; only `monitoring_disable_alerts` is shared (it's an info label on both rows). Full rules in [CLAUDE.md](CLAUDE.md#oob-ip-routing-idrac-bmcs).

## Service-level scrapes

The service template is a pure **direct scrape**: one row per (service, port) pair for every active service whose parent has a primary IP, targeting `<parent primary IP>:<port>`. It reads no config context — rows carry the info labels plus `service_name` / `service_protocol`, and use Prometheus' job-level scheme / metrics_path.

To redirect a single service (different port, a fixed hostname, or a full URL), set the text custom field **`prometheus_scrape_url`** on the `ipam.service`. Its value is fed **verbatim** into the target — the template does no parsing:

```
10.0.0.5:9443                          →  targets: ["10.0.0.5:9443"]
https://10.0.0.5:9443/custom/metrics   →  targets: ["https://10.0.0.5:9443/custom/metrics"]  (split it in relabel_configs)
```

When set, the service's `ports` field is ignored and one row is emitted. Create the CF: Customization → Custom Fields → Add, name `prometheus_scrape_url`, type Text, content type `ipam | service`.

For the "many services through one shared exporter" case (snmp_exporter, etc.), do the routing in the Prometheus scrape job — a `relabel_configs` block that moves `__address__` to `__param_target` and rewrites `__address__` to the exporter, exactly like the blackbox jobs in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml). Narrow the SD URL to the relevant services with a `&tag=` filter. Full details in [CLAUDE.md](CLAUDE.md#service-level-scrapes).

To drive **per-service** exporter params, the service template surfaces two custom fields as relabel-only meta labels: `prometheus_exporter_auth` → `__meta_prometheus_exporter_auth` and `prometheus_exporter_modules` → `__meta_prometheus_exporter_modules` (multi-select CSV-joined). The `netbox-sd-services-snmp` job in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) shows lifting them into `__param_module` / `__param_auth` (falling back to static params when a service doesn't set them). Assign both CFs to the `ipam | service` content type for the values to appear.

## Per-device overrides

Set a custom field named `prometheus_exporter_<param>` on the device to override (or add) a single param without forking the config context. Multi-select custom fields are CSV-joined automatically.

`prometheus_exporter_scrape_interval` and `prometheus_exporter_scrape_timeout` are special-cased: they override the matching config-context keys and are emitted as meta-labels rather than `__param_*` labels.

## Opt in to blackbox probing

Blackbox probing is driven by a single custom field — **no config context required**. The template emits every active object with a usable address; you narrow at the SD URL with a tag filter (`&tag=blackbox-probe` or similar).

1. Create the CF: Customization → Custom Fields → Add. Name: `prometheus_exporter_modules`. Type: multi-select. Choices: the blackbox module names configured in your local `blackbox.yml` (e.g. `icmp`, `tcp_connect`, `http_2xx`, `https_2xx`, `dns_lookup`). Content types: `dcim.device`, `virtualization.virtualmachine`, `ipam.service`, `ipam.ipaddress`.

2. Install the blackbox template: Customization → Export Templates → Add. **Name:** `blackbox-prometheus-sd` (must match the `?export=` slug in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml)). **Content types:** tick all four (`dcim | device`, `virtualization | virtual machine`, `ipam | service`, `ipam | ip address`). **MIME type:** `application/json`. **Template code:** paste the contents of [blackbox-prometheus-sd.j2](blackbox-prometheus-sd.j2).

3. Wire up the four blackbox scrape jobs from [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) — one per content-type endpoint, all hitting the same template. They share one `relabel_configs` block (YAML anchor `&blackbox_relabel`) that swaps `__address__` to the blackbox exporter (`localhost:9115`) and lifts the `module` label into `__param_module`.

4. Leave `prometheus_exporter_modules` unset on most objects — the defaults handle the common case:
   - Devices, VMs, IPs → `icmp`
   - Services → `http_2xx`

   Override on individual objects (or via bulk edit) to probe HTTP, DNS, etc. Multi-value CFs explode into one row per module.

Full reference (label set per branch, port handling, edge cases) in [CLAUDE.md → Blackbox probes](CLAUDE.md#blackbox-probes).
