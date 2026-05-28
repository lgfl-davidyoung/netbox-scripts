# NetBox Prometheus HTTP SD Export Templates

This folder holds NetBox export templates that emit Prometheus HTTP Service Discovery (`http_sd_config`) JSON. Two templates: one bound to `dcim.device`, one bound to `ipam.service`. Each is dropped into NetBox's Customization → Export Templates UI (or managed via the NetBox API / `netbox-plugin-prometheus-sd` config) and consumed by Prometheus via its render URL.

## Architecture

```
┌─────────────┐         ┌──────────────────────┐         ┌──────────────┐
│   NetBox    │ ──HTTP──▶ Export Template      │ ──JSON──▶  Prometheus  │
│  (devices,  │         │ (this repo)          │         │ http_sd      │
│  services)  │         │ renders queryset     │         │  config      │
└─────────────┘         └──────────────────────┘         └──────────────┘
                                                                │
                                                                ▼
                                                         ┌──────────────┐
                                                         │   Exporter   │
                                                         │ (node, snmp, │
                                                         │  fortigate)  │
                                                         └──────────────┘
```

Two scrape jobs in `prometheus.yml`, one per template:

```yaml
scrape_configs:
  - job_name: netbox-sd-devices
    http_sd_configs:
      - url: https://netbox.internal.lgfl.net/api/extras/export-templates/<device-template-id>/render/
  - job_name: netbox-sd-services
    http_sd_configs:
      - url: https://netbox.internal.lgfl.net/api/extras/export-templates/<service-template-id>/render/
```

## Data model

### Device config context

Devices opt into discovery by having a `prometheus-export-template` key in their config context. Without it, the device is silently skipped (so unrelated devices in the queryset are fine).

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "metrics_path": "/metrics",
        "scheme": "http"
    }
}
```

The template reads `port`, `metrics_path`, `exporter`, `target_scheme`, `params`, `scrape_interval`, `scrape_timeout`, and the optional `oob` sub-block when present (see "Exporter routing", "Extra params", "Scrape interval / timeout", and "OOB IP routing" below). `scheme` is informational only — Prometheus determines scheme from the scrape job, not from per-target labels.

The primary-IP target is emitted only when `port` is set. Omit `port` if you only want OOB emission for this device.

### Exporter routing (snmp_exporter, fortigate_exporter, etc.)

For devices scraped *via* an exporter rather than directly, set `exporter` in the config context. The exporter address becomes the scrape target and the device's own address moves to `__param_target` (the Prometheus convention).

```json
{
    "prometheus-export-template": {
        "port": 161,
        "exporter": "snmp-exporter.internal.lgfl.net:9116",
        "params": {
            "module": ["if_mib", "juniper_mib"],
            "auth": "public_v2"
        }
    }
}
```

Result: scrape target is `snmp-exporter.internal.lgfl.net:9116`, with labels including `__param_target=<device-ip>:161`, `__param_module=if_mib,juniper_mib`, `__param_auth=public_v2`. Prometheus turns the `__param_*` labels into URL parameters at scrape time.

### Target scheme prefix (fortigate_exporter)

Some exporters (notably fortigate_exporter) want the probe target as a full URL, not just `host:port`. Set `target_scheme` to prepend a scheme to `__param_target`:

```json
{
    "prometheus-export-template": {
        "port": 443,
        "exporter": "fortigate-exporter.internal.lgfl.net:9710",
        "target_scheme": "https",
        "params": {
            "profile": "fortigate"
        }
    }
}
```

Result: `__param_target=https://<device-ip>:443`. IPv6 stays bracket-wrapped: `https://[2001:db8::1]:443`.

### Extra params

`params` is a flat dict mapped 1:1 to `__param_<key>` labels. Values can be:

- Strings: passed through as-is
- Lists: CSV-joined (e.g. `["if_mib", "juniper_mib"]` → `if_mib,juniper_mib`). snmp_exporter and similar accept multi-value params this way.
- Empty / None: skipped

### Scrape interval / timeout

Set `scrape_interval` and/or `scrape_timeout` in the config context to override the job-level defaults on a per-device basis. Values are Prometheus duration strings (`30s`, `1m`, `2m30s`):

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "scrape_interval": "30s",
        "scrape_timeout": "10s"
    }
}
```

These are emitted as the Prometheus meta-labels `__scrape_interval__` and `__scrape_timeout__`, which Prometheus consumes natively — no relabel rule needed. When unset, the per-target labels are omitted and Prometheus falls back to the scrape job's defaults.

For services, the override is pulled from the *parent device's* config context (same as `exporter` / `target_scheme` / `params`), so every (service, port) row inherits it.

### OOB IP routing (iDRAC, BMCs)

A single physical server often needs two scrape targets — node_exporter on the primary IP and an iDRAC/BMC exporter on the OOB IP — with different exporters and ports. Express that with an `oob` sub-block in the config context. Its shape is identical to the top-level block (`port`, `exporter`, `target_scheme`, `params`, `metrics_path`, `scrape_interval`, `scrape_timeout` all supported); when present, the template emits a second target rooted at `device.oob_ip`.

Example — node_exporter on primary + iDRAC on OOB:

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "metrics_path": "/metrics",
        "oob": {
            "port": 443,
            "exporter": "idrac-exporter.internal.lgfl.net:9348",
            "target_scheme": "https"
        }
    }
}
```

OOB-only (no node_exporter on primary) — omit top-level `port`:

```json
{
    "prometheus-export-template": {
        "oob": {
            "port": 443,
            "exporter": "idrac-exporter.internal.lgfl.net:9348",
            "target_scheme": "https"
        }
    }
}
```

**Emission rules:**

- Primary target is emitted iff `device.primary_ip` exists AND the top-level dict has `port` set.
- OOB target is emitted iff `device.oob_ip` exists AND the `oob` sub-block is present.
- Both checks are independent: a device emits 0, 1, or 2 targets.

**Shared vs. per-target labels:** the info labels (`target_name`, `site`, `dc`, `cluster`, `tenant`, `device_role`, `platform`, `model`, `device_type`, `location`, `rack`, `description`) are identical on both rows. The per-target labels — `__address__` (`"targets"` value), `__param_target`, `__param_*`, `__metrics_path__`, `__scrape_interval__`, `__scrape_timeout__` — come from each block independently.

**Custom-field overrides apply to the primary emission only.** The `oob` sub-dict is self-contained — to override an OOB param, edit the config context, not a custom field. This avoids the ambiguity of "which target does this CF apply to?" and means iDRAC-style scrape config can't be accidentally broken by a stray `prometheus_exporter_*` CF on the device.

For services with an `oob` block on the parent device, the template emits a full set of (service, port) rows from the OOB IP as well — one row per port from each IP source. VMs don't have OOB IPs and skip the OOB branch automatically.

### Custom field overrides

For per-device overrides without forking a config context, set a custom field named `prometheus_exporter_<param>` on the device. It wins over the config context value for that one param. Custom fields with this prefix that don't have a matching context key are also picked up as fresh params.

Examples:
- `prometheus_exporter_module` (multi-select or text) — overrides `module`
- `prometheus_exporter_auth` (text) — overrides `auth`
- `prometheus_exporter_target_scheme` (text) — overrides `target_scheme` (not yet wired into the current template — see TODO below)
- `prometheus_exporter_scrape_interval` (text) — overrides `scrape_interval`
- `prometheus_exporter_scrape_timeout` (text) — overrides `scrape_timeout`

`prometheus_exporter_scrape_interval` and `prometheus_exporter_scrape_timeout` are handled out-of-band (they become `__scrape_interval__` / `__scrape_timeout__` meta-labels, not `__param_*` labels) and are reserved — they will not leak into `__param_*` even if no matching key exists in `params`.

Multi-select custom fields return lists, which the template CSV-joins automatically. Text custom fields containing commas pass through unchanged.

### Services

Services don't have config context; they have a `ports` field (a list). The service template emits one target per (service, port) pair, with labels inherited from the parent device or VM (`service.device or service.virtual_machine`). Additional labels: `service_name`, `service_protocol`.

Exporter routing for services pulls `exporter` / `target_scheme` / `params` from the *parent device's* config context, using the service's port as the probe target.

## Labels emitted

Shared labels (identical on primary and OOB rows for the same device):

| Label              | Source                          |
|--------------------|---------------------------------|
| `target_name`      | device/parent name              |
| `service_name`     | service.name (services only)    |
| `service_protocol` | service.protocol (services only)|
| `site`             | site.name                       |
| `dc`               | site.slug                       |
| `cluster`          | cluster.name                    |
| `tenant`           | tenant.name                     |
| `device_role`      | role.name                       |
| `platform`         | platform.name                   |
| `model`            | device_type.model               |
| `device_type`      | device_type.slug                |
| `location`         | location.name (devices only)    |
| `rack`             | rack.name (devices only)        |
| `description`     | description field               |

Per-target labels (differ between primary and OOB rows — each block has its own values):

| Label                 | Source                          |
|-----------------------|---------------------------------|
| `__param_<name>`      | block's `params` dict, or `prometheus_exporter_*` custom fields (primary only) |
| `__param_target`      | device IP when `exporter` is set (with optional `target_scheme` prefix) |
| `__metrics_path__`    | block's `metrics_path` key      |
| `__scrape_interval__` | block's `scrape_interval` key, or `prometheus_exporter_scrape_interval` CF (primary only) |
| `__scrape_timeout__`  | block's `scrape_timeout` key, or `prometheus_exporter_scrape_timeout` CF (primary only) |

`__param_*` labels are stripped by Prometheus after relabel (they're meta-labels). To preserve the probed address as a regular label on metrics, the Prometheus relabel config should copy `__param_target` to `instance` or similar — see "Prometheus relabel config" below. `__metrics_path__`, `__scrape_interval__`, `__scrape_timeout__` are also Prometheus meta-labels consumed natively — no relabel rule needed.

## Templates

The live templates are the source of truth — copy from them when installing into NetBox:

- Device template (bind to `dcim.device`): [device-prometheus-sd.j2](device-prometheus-sd.j2)
- Service template (bind to `ipam.service`): [service-prometheus-sd.j2](service-prometheus-sd.j2)

## Prometheus relabel config

The relabel rules consumers expect from this SD output:

```yaml
relabel_configs:
  - source_labels: [__meta_netbox_name]
    target_label: target_name
  - source_labels: [__meta_netbox_site]
    target_label: site
  - source_labels: [__meta_netbox_site_slug]
    target_label: dc
  - source_labels: [__meta_netbox_cluster]
    target_label: cluster
  - source_labels: [__meta_netbox_tenant]
    target_label: tenant
  - source_labels: [__meta_netbox_role]
    target_label: device_role
  - source_labels: [__meta_netbox_platform]
    target_label: platform
  - source_labels: [__meta_netbox_model]
    target_label: model
  - source_labels: [__meta_netbox_device_type]
    target_label: device_type
```

Note: the templates emit these labels directly (not via `__meta_netbox_*`), so the relabel above is from the older `netbox-plugin-prometheus-sd` shape. With the current export templates the labels arrive pre-named and no relabel is strictly required — but if you want to preserve `instance` as the probed device address when exporter routing is in use:

```yaml
relabel_configs:
  - source_labels: [__param_target]
    target_label: instance
    regex: (.+)
```

## Jinja gotchas hit while building this (in case of regressions)

These all bit during the original development and the templates are written around them. Worth knowing if you're modifying:

- **Hyphens in dict keys** — `{{ data.prometheus-export-template }}` is parsed as subtraction. Always use bracket subscript: `data['prometheus-export-template']`.
- **`combine` filter doesn't exist in NetBox's sandbox** — it's Ansible-specific. Build dicts/lists manually.
- **Macros render to strings, not Python objects** — you can't write a macro that "returns a list" and assign its result with `{% set x = mymacro() %}`. The macro's body is rendered to text. For data construction, use inline `{% set %}`.
- **`{% set %}` doesn't escape `{% if %}` scope** — assigning to a variable inside an `{% if %}` block doesn't propagate out. Use `namespace()` for cross-scope mutation: `{% set ns = namespace(x=[]) %}` then `{% set ns.x = ns.x + [item] %}`.
- **`loop.last` lies if you filter inside the loop** — if you skip iterations with `{% if %}`, `loop.last` still fires on the original last item. Pre-filter into a list first (using `namespace`), then iterate that list to render.
- **`None` stringifies to `"None"` inside `{{ ... }}`** — guard with `if value` or use `value or ''`. The `build_labels` macro skips falsy values entirely.
- **JSON escaping order** — escape backslashes first, then quotes, then control characters. Reverse order double-escapes the escapes you just added.
- **IPv6 needs brackets** — `[2001:db8::1]:9100`. `ip.version` reliably distinguishes (v4/v6 returns `4`/`6`).
- **`target.device_type` only exists on Devices, not VMs** — guard with `is defined and ...` when handling parents that could be either.
- **`target.role` vs `target.device_role`** — NetBox 4.x is `target.role`. Older versions used `device_role`.

## TODO / known gaps

- `prometheus_exporter_target_scheme` custom field override is documented above but not yet wired into the templates. Trivial addition — mirror the `prom.get('target_scheme')` access with a `target.cf.get('prometheus_exporter_target_scheme') or prom.get('target_scheme')` chain.
- No deduplication between device-emitted targets and service-emitted targets. If a device exposes port 9100 via both its config context and an IPAM Service entry, Prometheus will see two targets at the same `host:port`. Currently treated as harmless; relabel can dedupe by `instance` if it matters.
- Blackbox-style probes (ICMP, HTTP, TCP) are deliberately out of scope. The natural pattern is a separate scrape job with `static_configs` or its own export template that emits flat target lists, with `__address__` → `__param_target` → exporter swap handled in `relabel_configs`.

## Repo layout suggestion

```
.
├── CLAUDE.md                          # this file
├── templates/
│   ├── device-prometheus-sd.j2        # bind to dcim.device
│   └── service-prometheus-sd.j2       # bind to ipam.service
├── prometheus/
│   └── scrape-configs.yml             # example prometheus.yml fragment
└── README.md                          # human-facing overview
```

Sync templates to NetBox via the API (`/api/extras/export-templates/`) or paste into the UI. There's no built-in NetBox-as-code workflow for export templates, but the API supports `PATCH` so a small sync script is straightforward.
