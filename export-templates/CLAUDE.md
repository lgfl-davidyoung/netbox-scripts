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

The template reads `port`, `exporter_type`, `scheme`, `metrics_path`, `exporter`, `params`, `scrape_interval`, and `scrape_timeout` when present (see "Exporter routing", "Scheme", "Extra params", and "Scrape interval / timeout" below). No field inside the block is required by the Config Context Profile schema — the block's presence alone opts the device in. `port` is optional: when omitted the target is emitted without a `:port` suffix and Prometheus falls back to the scheme's default port. `exporter_type` is also optional — when omitted, no `exporter_type` label is emitted. OOB monitoring is configured via a separate top-level `prometheus-export-template-oob` context (see "OOB IP routing" below).

Services are **not** driven by config context at all — the service template is a pure direct-scrape with an optional per-service URL override (see "Service-level scrapes" below).

The primary-IP target is emitted whenever `prometheus-export-template` is present and the device has a primary_ip (`port` is optional). Drop the `prometheus-export-template` context entirely on devices that only need OOB or service-level monitoring.

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

These apply to **device** rows only — the service template does not emit per-target interval/timeout (services are direct-scrape and use the job defaults).

### OOB IP routing (iDRAC, BMCs)

A single physical server often needs two scrape targets — node_exporter (or windows_exporter) on the primary IP, and an iDRAC/BMC exporter on the OOB IP — with different exporters, ports, and ownership. The software side varies by OS (Linux → node_exporter:9100, Windows → windows_exporter:9182), but the hardware side is identical across all servers from the same vendor.

To keep those two concerns independent, OOB monitoring uses a **separate top-level config-context key**, `prometheus-export-template-oob`, rendered by a **separate export template** — [device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2) (the primary key is rendered by [device-prometheus-sd.j2](device-prometheus-sd.j2)). Both bind to `dcim.device` and are scraped by their own job (`?export=prometheus-device` and `?export=prometheus-device-oob`). The OOB schema mirrors `prometheus-export-template` (`port`, `exporter_type`, `scheme`, `exporter`, `params`, `metrics_path`, `scrape_interval`, `scrape_timeout` all supported). Scope it to a manufacturer (e.g. Dell) in NetBox; the OS-specific `prometheus-export-template` stays scoped to a role.

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

**Emission rules** (each template renders its own queryset pass independently):

- The **device template** emits a primary target iff `prometheus-export-template` is set and `device.primary_ip` exists. `port` is optional (omitted → portless target).
- The **OOB template** emits an OOB target iff `prometheus-export-template-oob` is set and `device.oob_ip` exists. `port` is optional (omitted → portless target).
- The two are independent templates with independent scrape jobs, so a device appears in 0, 1, or 2 of the SD outputs.

**Shared vs. per-target labels:** the info labels (`target_name`, `site`, `dc`, `cluster`, `tenant`, `device_role`, `platform`, `manufacturer`, `device_type`, `location`, `rack`, `description`) are identical on both rows — each template derives them the same way from the device. The per-target labels — `__address__` (`"targets"` value), `exporter_type`, `__param_target`, `__param_*`, `__metrics_path__`, `__scheme__`, `__scrape_interval__`, `__scrape_timeout__` — come from each context independently. Notably `exporter_type` differs between the rows (e.g. `node_exporter` on primary, `idrac_exporter` on OOB), which is what lets dashboards filter to the right exporter family.

**Custom-field overrides apply to the primary (device) template only.** The OOB template reads no `prometheus_exporter_*` CFs and no `prometheus_exporter_scrape_interval`/`_timeout` — to override an OOB param, edit the config context, not a custom field. (The one CF the OOB template does read is `monitoring_disable_alerts`, which is a shared info label, not a param.) This avoids the ambiguity of "which target does this CF apply to?" and means iDRAC-style scrape config can't be accidentally broken by a stray `prometheus_exporter_*` CF on the device.

### Service-level scrapes

The service template is a **pure direct-scrape**: it emits one row per (service, port) pair for every Service in the queryset whose parent has a `primary_ip` and is `active`. The scrape target is `<parent primary IP>:<port>`. It reads **nothing** from config context — no `prometheus-export-template-services` block, no `get_config_context()` call. Each row carries only the info labels (`target_name`, `service_name`, `service_protocol`, `site`, etc.) and inherits Prometheus' job-level scheme / `metrics_path`.

A Service with multiple ports emits one row per port. Inactive parents and parents with no `primary_ip` are skipped.

#### Target override — `prometheus_scrape_url` custom field

For the cases where "poll the parent IP on the service port" isn't right (a metrics endpoint on a different port, a fixed hostname, a full URL), set the **text custom field `prometheus_scrape_url` on `ipam.service`**. Its value is fed **verbatim** into the target — the template does no parsing. When set, the Service's `ports` field is ignored and exactly one row is emitted.

```
10.0.0.5:9443                          →  targets: ["10.0.0.5:9443"]
https://10.0.0.5:9443/custom/metrics   →  targets: ["https://10.0.0.5:9443/custom/metrics"]
```

What you put in the CF is what Prometheus receives as the target, so make it match what the scrape job expects. A bare `host:port` works as-is; if you put a full URL there, your scrape job's `relabel_configs` are responsible for splitting it into `__scheme__` / `__address__` / `__metrics_path__`. The template emits no `__scheme__` / `__metrics_path__` / `__param_*` of its own for the override — that's deliberate, to keep it a dumb passthrough.

#### Multi-target exporters (snmp_exporter, etc.)

The old "service via exporter" model (a `prometheus-export-template-services` config context that routed every service on a device through one shared exporter address via `__param_target`) has been **removed**. It didn't generalise: a single exporter block forced one set of params on every service/port on the device, and per-service variation (e.g. a different SNMP `module` per app) wasn't expressible.

Shared-exporter routing (snmp_exporter, fortigate_exporter, blackbox-style probes) is now handled **entirely on the Prometheus side** — the export template just emits the plain `host:port` (or a `prometheus_scrape_url` override) and the scrape job's `relabel_configs` move that address to `__param_target` and rewrite `__address__` to the exporter. This is the same pattern the blackbox jobs already use (see [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml)). Narrow the scrape job to the relevant services with a tag filter on the SD URL (`&tag=snmp`); drive any per-service params (e.g. the SNMP `module` / `auth`) from custom fields surfaced as labels, or from static config in the scrape job.

To support per-service params, the service template surfaces two custom fields as meta labels: `prometheus_exporter_auth` → `__meta_prometheus_exporter_auth` and `prometheus_exporter_modules` → `__meta_prometheus_exporter_modules` (multi-select CSV-joined). Like all `__`-prefixed labels these are available during relabeling and dropped before the scrape, so a scrape job can lift them into `__param_auth` / `__param_module`. Empty CFs emit no label. Both CFs must be assigned to the `ipam.service` content type for the values to appear (`service.cf.get(...)` is None-safe if not).

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

### Alert suppression (`monitoring_disable_alerts`)

A separate boolean custom field `monitoring_disable_alerts` (note the `monitoring_` prefix — it is **not** part of the `prometheus_exporter_*` param family) emits the `disable_alerts` label on every row for that object. When the CF is set/truthy the label is emitted as `disable_alerts="true"`; when unset or false the label is omitted entirely (alert rules should treat "absent" as "alert normally"). All live templates honour it:

- **Device templates** (primary and OOB) — read from the device; emitted on both the primary row and the OOB row (shared info label). It is the only custom field the OOB template reads.
- **Service template** — read from the Service object itself (not the parent device), so suppression is per-service.
- **Blackbox template** — read from the iterated object (device/VM/IP/service), one `disable_alerts` value per object applied to every module row.

Consume it in Alertmanager/alert rules by matching the label, e.g. `... unless on(instance) up{disable_alerts="true"}` or an inhibition rule keyed on `disable_alerts`.

### Services

Services have a `ports` field (a list). The service template emits one direct-scrape target per (service, port) pair at the parent's primary IP, with info labels inherited from the parent device or VM (NetBox 4.5+ exposes both via the `service.parent` GenericForeignKey accessor). Additional labels: `service_name`, `service_protocol`.

Service emission is independent of all `prometheus-export-template*` config-context keys — those drive device emission only. Services read nothing from config context; per-service customisation is the `prometheus_scrape_url` custom-field override (see "Service-level scrapes" above), and shared-exporter routing is done in the Prometheus scrape job's `relabel_configs`, not in the template.

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
| `disable_alerts`   | `"true"` when the `monitoring_disable_alerts` custom field is set on the device (services: on the service); omitted otherwise |

Per-target labels — these are emitted by the **device templates** only (the primary [device-prometheus-sd.j2](device-prometheus-sd.j2) and the OOB [device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2); each derives them from its own context block). The service template emits **none** of these: it only sets the target (either `parent IP:port` or the verbatim `prometheus_scrape_url` value), the shared info labels, and two `__meta_*` passthrough labels (`__meta_prometheus_exporter_auth`, `__meta_prometheus_exporter_modules`) sourced from the matching service custom fields. It never emits `exporter_type`, `__param_*`, `__param_target`, `instance`, `__scheme__`, or `__metrics_path__` — for shared-exporter routing on a service row, handle it in your scrape job's `relabel_configs` (lifting the `__meta_*` labels into `__param_*` as needed).

| Label                 | Source                          |
|-----------------------|---------------------------------|
| `exporter_type`       | block's `exporter_type` key — identifies the exporter family (`node_exporter`, `snmp_exporter`, `idrac_exporter`, etc.). Optional; omitted when unset. |
| `__param_<name>`      | block's `params` dict, or `prometheus_exporter_*` custom fields (primary only) |
| `__param_target`      | device IP when `exporter` is set (with optional `scheme` prefix) |
| `instance`            | mirror of `__param_target` — emitted only when `exporter` is set, so targets behind a shared exporter don't collide on the default `instance` (=`__address__`=exporter). Omitted on direct-scrape rows (Prometheus' `__address__` default is already correct). |
| `__metrics_path__`    | block's `metrics_path` key      |
| `__scheme__`          | block's `scheme` key — emitted only when no `exporter` is set (otherwise scheme is baked into `__param_target`) |
| `__scrape_interval__` | block's `scrape_interval` key, or `prometheus_exporter_scrape_interval` CF (primary only) |
| `__scrape_timeout__`  | block's `scrape_timeout` key, or `prometheus_exporter_scrape_timeout` CF (primary only) |

`__param_*` labels are stripped by Prometheus after relabel (they're meta-labels). To preserve the probed address as a regular label on metrics, the device template emits `instance` directly (= `__param_target`) on exporter-routed rows, so the older "copy `__param_target` to `instance`" relabel rule is no longer required (keeping it is harmless — same value). `__metrics_path__`, `__scrape_interval__`, `__scrape_timeout__` are also Prometheus meta-labels consumed natively — no relabel rule needed.

## Blackbox probes

Separate from the exporter-routing templates above, this folder has a single template that emits Prometheus SD rows for **blackbox-exporter probing** (ICMP, TCP connect, HTTP, DNS, etc.) across four content types. The blackbox exporter runs on the local Prometheus host; the template emits `(probe-target, module)` pairs and Prometheus' `relabel_configs` swap the address to the blackbox endpoint.

The blackbox template is independent of the `prometheus-export-template[-oob]` config-context system — it's CF-driven, not config-context-driven.

### One template, four content types

[blackbox-prometheus-sd.j2](blackbox-prometheus-sd.j2) is bound to **all** of `dcim.device`, `virtualization.virtualmachine`, `ipam.service`, and `ipam.ipaddress`. NetBox renders it against whichever content-type queryset the SD URL implies; per-row dispatch inside the template picks the right derivation flow via `is defined` attribute checks:

- `obj.ports is defined` → Service branch
- `obj.dns_name is defined` → IPAddress branch
- `obj.primary_ip is defined` → Device or VM branch

Same template body handles all four — macros and module-CF handling are defined once and shared.

### Data model

Opt-in is **per object** via a single custom field `prometheus_exporter_modules` (multi-select or list-valued text). The CF value is one or more blackbox module names (`icmp`, `tcp_connect`, `http_2xx`, `dns_lookup`, etc.). Each module value emits its own SD row.

**Emission is unconditional** — every active object with a usable address appears in the SD output. To probe a subset, narrow at the SD URL with `&tag=<tag>` (NetBox tag filter). The template itself does no filtering.

**Defaults when the CF is unset:**

| Branch | Default module |
|---|---|
| Device / VM | `icmp` |
| IP address | `icmp` |
| Service | `http_2xx` (most services in the fleet are HTTP-facing) |

### Probe targets

| Branch | Skipped if | Probe target |
|---|---|---|
| Service | parent missing / parent inactive / parent has no `primary_ip` / no ports | `parent.primary_ip:ports[0]` (first port only; IPv6 bracket-wrapped) |
| IP address | IP inactive / no address | the IP itself (no port) |
| Device / VM | object inactive / no `primary_ip` | `primary_ip` (no port) |

Services with multiple ports emit one row per module against the **first** port. Multi-port probing would produce identical SD rows differing only in port, which the SD layer can't disambiguate — split into multiple Services if you need different ports probed differently.

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
| `disable_alerts` | ✓ | ✓ | ✓ (from the service object) |

IP addresses don't carry site/role labels — they aren't directly associated with those. If an IP is interface-assigned and you need site/role on the probe row, scrape via the device/VM endpoint instead.

### Prometheus relabel

The four blackbox jobs in [prometheus/scrape-configs.yml](prometheus/scrape-configs.yml) (devices, vms, services, ip-addresses) all hit the same template via `?export=blackbox-prometheus-sd` and share one relabel block (YAML anchor `&blackbox_relabel` / `*blackbox_relabel`):

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

### Worked example

Device `srv-app-01` (active, primary_ip `10.0.0.5`) with CF `prometheus_exporter_modules = ["icmp", "tcp_connect"]`. Hitting `/api/dcim/devices/?export=blackbox-prometheus-sd` emits:

```json
[
  {"targets": ["10.0.0.5"], "labels": {"module": "icmp", "target_name": "srv-app-01", ...}},
  {"targets": ["10.0.0.5"], "labels": {"module": "tcp_connect", "target_name": "srv-app-01", ...}}
]
```

After relabel, Prometheus scrapes `localhost:9115/probe?target=10.0.0.5&module=icmp` (and the same with `module=tcp_connect`). The `instance` label is `10.0.0.5` on both rows.

### Superseded three-template variant

A previous iteration shipped three separate templates ([blackbox-device-vm-sd.j2](blackbox-device-vm-sd.j2), [blackbox-service-sd.j2](blackbox-service-sd.j2), [blackbox-ipaddress-sd.j2](blackbox-ipaddress-sd.j2)) that have now been folded into the single template above. The three files remain in the tree for a transitional period and will be removed once the consolidated template is verified working in NetBox. New installs should use the consolidated template only.

## Templates

The live templates are the source of truth — copy from them when installing into NetBox:

- Device primary template (bind to `dcim.device`): [device-prometheus-sd.j2](device-prometheus-sd.j2)
- Device OOB template (bind to `dcim.device`): [device-oob-prometheus-sd.j2](device-oob-prometheus-sd.j2)
- Service template (bind to `ipam.service`): [service-prometheus-sd.j2](service-prometheus-sd.j2)
- Blackbox template (bind to `dcim.device` + `virtualization.virtualmachine` + `ipam.service` + `ipam.ipaddress`): [blackbox-prometheus-sd.j2](blackbox-prometheus-sd.j2)

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

Note: the templates emit these labels directly (not via `__meta_netbox_*`), so the relabel above is from the older `netbox-plugin-prometheus-sd` shape. With the current export templates the labels arrive pre-named and no relabel is strictly required.

`instance` is likewise emitted directly by the device and service templates on exporter-routed rows (= `__param_target`), so the probed address is preserved without any relabel. The equivalent rule is only needed if you're still on the older SD shape:

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
- **`get_config_context()` is expensive and uncached** — every call re-queries and merges all ConfigContexts (NetBox doesn't annotate the export-template queryset), so calling it per row is the dominant render cost. The **device** template pays this once per device. The **service** template no longer calls it at all (direct scrape needs only the parent's primary IP and info labels). The service template still memoizes the parent FK walk (site/role/platform/device_type/…) by the GenericForeignKey columns (`parent_object_type_id` / `parent_object_id`), so each parent is resolved once even with many service ports — and a cache hit skips resolving the `service.parent` GFK entirely. Keep this memo if editing. If you reintroduce `get_config_context()` into a per-service path, memoize it the same way or the template will crawl.

## TODO / known gaps

- No deduplication between device-emitted targets and service-emitted targets. If a device exposes port 9100 via both its config context and an IPAM Service entry, Prometheus will see two targets at the same `host:port`. Currently treated as harmless; relabel can dedupe by `instance` if it matters.
- Blackbox service rows only probe the first port of a multi-port `ipam.service`. Multi-port probing via blackbox would produce duplicate SD rows differing only by port, which the SD layer can't represent — split into multiple Services if that matters.
- No deduplication between the blackbox device/VM template and the blackbox IP address template. A device whose primary IP is also a managed IPAM record will appear in both SD outputs (with different label sets). Narrow via tag filtering on whichever endpoint is canonical for your environment.

## Repo layout suggestion

```
.
├── CLAUDE.md                          # this file
├── device-prometheus-sd.j2            # bind to dcim.device (primary IP — prometheus-export-template key)
├── device-oob-prometheus-sd.j2        # bind to dcim.device (OOB IP — prometheus-export-template-oob key)
├── service-prometheus-sd.j2           # bind to ipam.service (direct scrape + prometheus_scrape_url override)
├── blackbox-prometheus-sd.j2          # bind to dcim.device + virtualization.virtualmachine + ipam.service + ipam.ipaddress
├── blackbox-device-vm-sd.j2           # deprecated — superseded by blackbox-prometheus-sd.j2
├── blackbox-service-sd.j2             # deprecated — superseded by blackbox-prometheus-sd.j2
├── blackbox-ipaddress-sd.j2           # deprecated — superseded by blackbox-prometheus-sd.j2
├── profile-prometheus-export-template.json           # Config Context Profile for the primary key
├── profile-prometheus-export-template-oob.json       # Config Context Profile for the -oob key
├── prometheus/
│   └── scrape-configs.yml             # example prometheus.yml fragment
└── README.md                          # human-facing overview
```

Sync templates to NetBox via the API (`/api/extras/export-templates/`) or paste into the UI. There's no built-in NetBox-as-code workflow for export templates, but the API supports `PATCH` so a small sync script is straightforward.
