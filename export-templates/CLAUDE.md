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

The template reads `port`, `exporter_type`, `scheme`, `metrics_path`, `exporter`, `params`, `scrape_interval`, and `scrape_timeout` when present (see "Exporter routing", "Scheme", "Extra params", and "Scrape interval / timeout" below). `port` and `exporter_type` are required by the Config Context Profile schema. `target_scheme` is accepted as a deprecated alias for `scheme`. OOB monitoring is configured via a separate top-level `prometheus-export-template-oob` context (see "OOB IP routing" below). Service-level monitoring (typically SNMP for app-level scrapes) uses a separate top-level `prometheus-export-template-services` context (see "Service-level scrapes" below).

The primary-IP target is emitted only when both `prometheus-export-template` is present and `port` is set. Drop the `prometheus-export-template` context entirely on devices that only need OOB or service-level monitoring.

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

### Scheme

`scheme` describes the protocol (`http`, `https`) used to reach the device. It is interpreted differently depending on whether exporter routing is in use:

- **With `exporter`** — scheme prefixes `__param_target`, so the exporter knows what protocol to probe. Some exporters (notably fortigate_exporter, blackbox-style probes) require this — they want the probe target as a full URL, not just `host:port`.
- **Without `exporter`** — scheme is emitted as the Prometheus meta-label `__scheme__`, which Prometheus uses to override the scrape job's default scheme on a per-target basis.

Example with an exporter:

```json
{
    "prometheus-export-template": {
        "port": 443,
        "scheme": "https",
        "exporter": "fortigate-exporter.internal.lgfl.net:9710",
        "params": {
            "profile": "fortigate"
        }
    }
}
```

Result: `__param_target=https://<device-ip>:443`. IPv6 stays bracket-wrapped: `https://[2001:db8::1]:443`. `__scheme__` is **not** emitted (the scheme is baked into `__param_target`).

Example without an exporter — direct HTTPS scrape:

```json
{
    "prometheus-export-template": {
        "port": 9100,
        "scheme": "https"
    }
}
```

Result: `__scheme__=https`. Prometheus scrapes the target over HTTPS regardless of the scrape job's default.

`target_scheme` is accepted as a legacy alias for `scheme` — existing config contexts that use it continue to work, but new contexts should use `scheme`.

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

For services, the override is pulled from the *parent device's* config context (same as `exporter` / `scheme` / `params`), so every (service, port) row inherits it.

### OOB IP routing (iDRAC, BMCs)

A single physical server often needs two scrape targets — node_exporter (or windows_exporter) on the primary IP, and an iDRAC/BMC exporter on the OOB IP — with different exporters, ports, and ownership. The software side varies by OS (Linux → node_exporter:9100, Windows → windows_exporter:9182), but the hardware side is identical across all servers from the same vendor.

To keep those two concerns independent, OOB monitoring uses a **separate top-level config-context key**: `prometheus-export-template-oob`. The schema mirrors `prometheus-export-template` (`port`, `exporter_type`, `scheme`, `exporter`, `params`, `metrics_path`, `scrape_interval`, `scrape_timeout` all supported). Scope it to a manufacturer (e.g. Dell) in NetBox; the OS-specific `prometheus-export-template` stays scoped to a role.

Example — a Dell server running Linux gets both contexts merged:

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

In NetBox you'd configure each separately:
- A "Linux Server" role context with just the `prometheus-export-template` block (9100/http)
- A "Windows Server" role context with `prometheus-export-template` set differently (9182/http, windows_exporter)
- A "Dell" manufacturer context with just the `prometheus-export-template-oob` block (443/https/iDRAC)

NetBox merges all applicable contexts onto each device, so Linux-on-Dell, Windows-on-Dell, and "OOB-only" appliances each get exactly what they need without overlap.

**Emission rules:**

- Primary target is emitted iff `prometheus-export-template` is set, `device.primary_ip` exists, and the context has `port`.
- OOB target is emitted iff `prometheus-export-template-oob` is set, `device.oob_ip` exists, and the context has `port`.
- Both checks are independent: a device emits 0, 1, or 2 targets.

**Shared vs. per-target labels:** the info labels (`target_name`, `site`, `dc`, `cluster`, `tenant`, `device_role`, `platform`, `manufacturer`, `device_type`, `location`, `rack`, `description`) are identical on both rows. The per-target labels — `__address__` (`"targets"` value), `exporter_type`, `__param_target`, `__param_*`, `__metrics_path__`, `__scheme__`, `__scrape_interval__`, `__scrape_timeout__` — come from each context independently. Notably `exporter_type` differs between the rows (e.g. `node_exporter` on primary, `idrac_exporter` on OOB), which is what lets dashboards filter to the right exporter family.

**Custom-field overrides apply to the primary emission only.** The OOB context is self-contained — to override an OOB param, edit the config context, not a custom field. This avoids the ambiguity of "which target does this CF apply to?" and means iDRAC-style scrape config can't be accidentally broken by a stray `prometheus_exporter_*` CF on the device.

### Service-level scrapes

Many devices need application-level monitoring on top of (or instead of) the host scrape — for example a server running several SNMP-monitored applications on different ports (`161` for system MIBs, `1161` for App A, `2161` for App B). All these scrapes typically share one `snmp_exporter` address and authentication but cover different ports, which is exactly what NetBox **Services** model (a Service has a name, protocol, and one or more ports on a parent device or VM).

The service template emits one row per (service, port) pair for every Service in the queryset whose parent has a `primary_ip` and is `active`. It is independent of any device-level context — services emit even if the parent has no Prometheus config context at all. Without context, the row is a bare direct-scrape with just the info labels (`target_name`, `service_name`, `service_protocol`, `site`, etc.).

To add exporter routing, scheme, params, or `exporter_type` to service rows, set a `prometheus-export-template-services` top-level context on the parent. The schema mirrors the other two blocks except `port` is not required (services contribute their own ports). Example — a server with SNMP-monitored applications on three ports:

```json
{
    "prometheus-export-template-services": {
        "exporter_type": "snmp_exporter",
        "exporter": "snmp-exporter.internal.lgfl.net:9116",
        "scheme": "http",
        "params": {"auth": "public_v2"}
    }
}
```

Scope this where the SNMP exporter applies (per role, per site, or globally). The device-level `prometheus-export-template` and `prometheus-export-template-oob` contexts are independent and do not influence service emission.

Currently the SNMP `module` (or any other param that varies per service) must live in the device-level context, meaning all services on the same device share params. Per-service overrides via a JSON custom field on `ipam.service` is a planned follow-up.

### Custom field overrides

For per-device overrides without forking a config context, set a custom field named `prometheus_exporter_<param>` on the device. It wins over the config context value for that one param. Custom fields with this prefix that don't have a matching context key are also picked up as fresh params.

Examples:
- `prometheus_exporter_module` (multi-select or text) — overrides `module`
- `prometheus_exporter_auth` (text) — overrides `auth`
- `prometheus_exporter_scrape_interval` (text) — overrides `scrape_interval`
- `prometheus_exporter_scrape_timeout` (text) — overrides `scrape_timeout`

`scheme` is read from the config context only — there is no `prometheus_exporter_scheme` CF override. Per-device scheme variation should be expressed by forking the config context.

`prometheus_exporter_scrape_interval` and `prometheus_exporter_scrape_timeout` are handled out-of-band (they become `__scrape_interval__` / `__scrape_timeout__` meta-labels, not `__param_*` labels) and are reserved — they will not leak into `__param_*` even if no matching key exists in `params`.

Multi-select custom fields return lists, which the template CSV-joins automatically. Text custom fields containing commas pass through unchanged.

### Services

Services don't have config context; they have a `ports` field (a list). The service template emits one target per (service, port) pair, with labels inherited from the parent device or VM (NetBox 4.5+ exposes both via the `service.parent` GenericForeignKey accessor). Additional labels: `service_name`, `service_protocol`.

Service emission is independent of `prometheus-export-template` and `prometheus-export-template-oob` — those contexts drive device emission only. Exporter routing for services is opt-in via the parent's `prometheus-export-template-services` context (see "Service-level scrapes" above).

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
| `manufacturer`     | device_type.manufacturer.name   |
| `device_type`      | device_type.model (display name, e.g. "FortiGate 3000D") |
| `location`         | location.name (devices only)    |
| `rack`             | rack.name (devices only)        |
| `description`     | description field               |

Per-target labels (differ between primary and OOB rows — each block has its own values):

| Label                 | Source                          |
|-----------------------|---------------------------------|
| `exporter_type`       | block's `exporter_type` key — identifies the exporter family (`node_exporter`, `snmp_exporter`, `idrac_exporter`, etc.). Required by the schema. |
| `__param_<name>`      | block's `params` dict, or `prometheus_exporter_*` custom fields (primary only) |
| `__param_target`      | device IP when `exporter` is set (with optional `scheme` prefix) |
| `__metrics_path__`    | block's `metrics_path` key      |
| `__scheme__`          | block's `scheme` key — emitted only when no `exporter` is set (otherwise scheme is baked into `__param_target`) |
| `__scrape_interval__` | block's `scrape_interval` key, or `prometheus_exporter_scrape_interval` CF (primary only) |
| `__scrape_timeout__`  | block's `scrape_timeout` key, or `prometheus_exporter_scrape_timeout` CF (primary only) |

`__param_*` labels are stripped by Prometheus after relabel (they're meta-labels). To preserve the probed address as a regular label on metrics, the Prometheus relabel config should copy `__param_target` to `instance` or similar — see "Prometheus relabel config" below. `__metrics_path__`, `__scrape_interval__`, `__scrape_timeout__` are also Prometheus meta-labels consumed natively — no relabel rule needed.

## Blackbox probes

Separate from the exporter-routing templates above, this folder also has three templates that emit Prometheus SD rows for **blackbox-exporter probing** (ICMP, TCP connect, HTTP, DNS, etc.). The blackbox exporter runs on the local Prometheus host; the templates emit `(probe-target, module)` pairs and Prometheus' `relabel_configs` swap the address to the blackbox endpoint.

The three blackbox templates are independent of the `prometheus-export-template[-oob][-services]` config-context system — they're CF-driven, not config-context-driven.

### Data model

Opt-in is **per object** via a single custom field `prometheus_exporter_modules` (multi-select or list-valued text). The CF value is one or more blackbox module names (`icmp`, `tcp_connect`, `http_2xx`, `dns_lookup`, etc.). Each module value emits its own SD row.

**Emission is unconditional** — every active object with a usable address appears in the SD output. To probe a subset, narrow at the SD URL with `&tag=<tag>` (NetBox tag filter). The templates themselves do no filtering.

**Defaults when the CF is unset:**

| Content type | Default module |
|---|---|
| `dcim.device`, `virtualization.virtualmachine` | `icmp` |
| `ipam.ipaddress` | `icmp` |
| `ipam.service` | `tcp_connect` (services are port-specific; ICMP wouldn't use the port) |

### Probe targets

| Template | Bind to | Probe target |
|---|---|---|
| [blackbox-device-vm-sd.j2](blackbox-device-vm-sd.j2) | `dcim.device` **and** `virtualization.virtualmachine` (one template, multi-content-type) | `primary_ip` (no port) |
| [blackbox-ipaddress-sd.j2](blackbox-ipaddress-sd.j2) | `ipam.ipaddress` | the IP itself (no port) |
| [blackbox-service-sd.j2](blackbox-service-sd.j2) | `ipam.service` | `parent.primary_ip:ports[0]` (first port only; IPv6 bracket-wrapped) |

Services with multiple ports emit one row per module against the **first** port. Multi-port probing would produce identical SD rows differing only in port, which the SD layer can't disambiguate — pick the canonical port at the service level if you need different ports probed differently, split into multiple Services.

### Labels emitted

All blackbox rows carry `module` (regular label, swapped to `__param_module` by relabel). Beyond that:

| Label | Devices/VMs | IPs | Services |
|---|---|---|---|
| `module` | ✓ | ✓ | ✓ |
| `target_name` | device/VM name | best-effort from `assigned_object.device.name` / `.virtual_machine.name` / `.name` | parent device/VM name |
| `service_name`, `service_protocol` | — | — | ✓ |
| `dns_name` | — | ✓ | — |
| `site`, `dc`, `cluster`, `device_role`, `platform` | ✓ | — | ✓ (from parent) |
| `manufacturer`, `device_type`, `location`, `rack` | ✓ (devices only — guarded `is defined` for VMs) | — | ✓ (from parent, same guard) |
| `tenant`, `description` | ✓ | ✓ | ✓ |

IP addresses don't carry site/role labels — they aren't directly associated with those. If an IP is interface-assigned and you need site/role on the probe row, scrape via the device/VM template instead.

### Prometheus relabel

The blackbox jobs in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) all share the same relabel block:

```yaml
metrics_path: /probe
relabel_configs:
  - source_labels: [__address__]
    target_label: __param_target
  - source_labels: [__param_target]
    target_label: instance
  - source_labels: [module]
    target_label: __param_module
  - target_label: __address__
    replacement: localhost:9115
```

YAML anchor (`&blackbox_relabel` / `*blackbox_relabel`) is used in scrape-configs.yml so the four jobs (devices, vms, services, ip-addresses) share one definition.

### Worked example

Device `srv-app-01` (active, primary_ip `10.0.0.5`) with CF `prometheus_exporter_modules = ["icmp", "tcp_connect"]`. The device/VM template emits:

```json
[
  {"targets": ["10.0.0.5"], "labels": {"module": "icmp", "target_name": "srv-app-01", ...}},
  {"targets": ["10.0.0.5"], "labels": {"module": "tcp_connect", "target_name": "srv-app-01", ...}}
]
```

After relabel, Prometheus scrapes `localhost:9115/probe?target=10.0.0.5&module=icmp` (and the same with `module=tcp_connect`). The `instance` label is `10.0.0.5` on both rows.

## Templates

The live templates are the source of truth — copy from them when installing into NetBox:

- Device template (bind to `dcim.device`): [device-prometheus-sd.j2](device-prometheus-sd.j2)
- Service template (bind to `ipam.service`): [service-prometheus-sd.j2](service-prometheus-sd.j2)
- Blackbox device/VM template (bind to `dcim.device` + `virtualization.virtualmachine`): [blackbox-device-vm-sd.j2](blackbox-device-vm-sd.j2)
- Blackbox service template (bind to `ipam.service`): [blackbox-service-sd.j2](blackbox-service-sd.j2)
- Blackbox IP address template (bind to `ipam.ipaddress`): [blackbox-ipaddress-sd.j2](blackbox-ipaddress-sd.j2)

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

- No deduplication between device-emitted targets and service-emitted targets. If a device exposes port 9100 via both its config context and an IPAM Service entry, Prometheus will see two targets at the same `host:port`. Currently treated as harmless; relabel can dedupe by `instance` if it matters.
- Blackbox service rows only probe the first port of a multi-port `ipam.service`. Multi-port probing via blackbox would produce duplicate SD rows differing only by port, which the SD layer can't represent — split into multiple Services if that matters.
- No deduplication between the blackbox device/VM template and the blackbox IP address template. A device whose primary IP is also a managed IPAM record will appear in both SD outputs (with different label sets). Narrow via tag filtering on whichever endpoint is canonical for your environment.

## Repo layout suggestion

```
.
├── CLAUDE.md                          # this file
├── device-prometheus-sd.j2            # bind to dcim.device
├── service-prometheus-sd.j2           # bind to ipam.service
├── blackbox-device-vm-sd.j2           # bind to dcim.device + virtualization.virtualmachine
├── blackbox-service-sd.j2             # bind to ipam.service
├── blackbox-ipaddress-sd.j2           # bind to ipam.ipaddress
├── prometheus-export-template.schema.json   # Config Context Profile schema
├── prometheus/
│   └── scrape-configs.yml             # example prometheus.yml fragment
└── README.md                          # human-facing overview
```

Sync templates to NetBox via the API (`/api/extras/export-templates/`) or paste into the UI. There's no built-in NetBox-as-code workflow for export templates, but the API supports `PATCH` so a small sync script is straightforward.
