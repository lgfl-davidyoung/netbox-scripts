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

Devices opt into discovery by having a `prometheus-plugin-prometheus-sd` key in their config context. Without it, the device is silently skipped (so unrelated devices in the queryset are fine).

```json
{
    "prometheus-plugin-prometheus-sd": {
        "port": 9100,
        "metrics_path": "/metrics",
        "scheme": "http"
    }
}
```

`port` is the only field the template currently reads from this block directly — `metrics_path` and `scheme` are passed through via relabel config on the Prometheus side if needed (see relabel config below). The template also reads `exporter`, `target_scheme`, and `params` when present (see "Exporter routing" and "Extra params" below).

### Exporter routing (snmp_exporter, fortigate_exporter, etc.)

For devices scraped *via* an exporter rather than directly, set `exporter` in the config context. The exporter address becomes the scrape target and the device's own address moves to `__param_target` (the Prometheus convention).

```json
{
    "prometheus-plugin-prometheus-sd": {
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
    "prometheus-plugin-prometheus-sd": {
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

### Custom field overrides

For per-device overrides without forking a config context, set a custom field named `prometheus_exporter_<param>` on the device. It wins over the config context value for that one param. Custom fields with this prefix that don't have a matching context key are also picked up as fresh params.

Examples:
- `prometheus_exporter_module` (multi-select or text) — overrides `module`
- `prometheus_exporter_auth` (text) — overrides `auth`
- `prometheus_exporter_target_scheme` (text) — overrides `target_scheme` (not yet wired into the current template — see TODO below)

Multi-select custom fields return lists, which the template CSV-joins automatically. Text custom fields containing commas pass through unchanged.

### Services

Services don't have config context; they have a `ports` field (a list). The service template emits one target per (service, port) pair, with labels inherited from the parent device or VM (`service.device or service.virtual_machine`). Additional labels: `service_name`, `service_protocol`.

Exporter routing for services pulls `exporter` / `target_scheme` / `params` from the *parent device's* config context, using the service's port as the probe target.

## Labels emitted

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
| `__param_<name>`   | from config context `params` or `prometheus_exporter_*` custom fields |
| `__param_target`   | device address when `exporter` is set (with optional scheme prefix) |

`__param_*` labels are stripped by Prometheus after relabel (they're meta-labels). To preserve the probed address as a regular label on metrics, the Prometheus relabel config should copy `__param_target` to `instance` or similar — see "Prometheus relabel config" below.

## Templates

### Device template (`dcim.device`)

```jinja
{#-
  Prometheus HTTP SD export template — Devices.
  Bind to: dcim.device
-#}

{%- macro jstr(s) -%}
{{ s|string|replace('\\', '\\\\')|replace('"', '\\"')|replace('\n', '\\n')|replace('\r', '\\r')|replace('\t', '\\t') }}
{%- endmacro -%}

{%- macro build_labels(fields) -%}
  {%- set lbl = namespace(parts=[]) -%}
  {%- for name, value in fields -%}
    {%- if value -%}
      {%- set lbl.parts = lbl.parts + ['"' ~ name ~ '": "' ~ jstr(value)|trim ~ '"'] -%}
    {%- endif -%}
  {%- endfor -%}
  { {{ lbl.parts | join(', ') }} }
{%- endmacro -%}

{%- macro target_addr(ip, port) -%}
  {%- if ip.version == 6 -%}
    [{{ ip }}]:{{ port }}
  {%- else -%}
    {{ ip }}:{{ port }}
  {%- endif -%}
{%- endmacro -%}

{%- macro param_value(v) -%}
{%- if v is none or v == '' -%}
{%- elif v is string -%}
{{ v }}
{%- elif v is iterable -%}
{{ v | select | map('string') | join(',') }}
{%- else -%}
{{ v }}
{%- endif -%}
{%- endmacro -%}

{%- set ns = namespace(rows=[]) -%}
{%- for target in queryset -%}
  {%- if target.status and target.primary_ip -%}
    {%- set data = target.get_config_context() -%}
    {%- if data.get('prometheus-plugin-prometheus-sd') -%}
      {%- set prom = data['prometheus-plugin-prometheus-sd'] -%}
      {%- set device_addr = target_addr(target.primary_ip.address.ip, prom.port)|trim -%}

      {#- Exporter routing. -#}
      {%- if prom.get('exporter') -%}
        {%- set scrape_target = prom.exporter -%}
        {%- if prom.get('target_scheme') -%}
          {%- set param_target = prom.target_scheme ~ '://' ~ device_addr -%}
        {%- else -%}
          {%- set param_target = device_addr -%}
        {%- endif -%}
      {%- else -%}
        {%- set scrape_target = device_addr -%}
        {%- set param_target = None -%}
      {%- endif -%}

      {#- Params with custom field override. -#}
      {%- set params_ns = namespace(parts=[]) -%}
      {%- set ctx_params = prom.get('params') or {} -%}
      {%- for k, v in ctx_params.items() -%}
        {%- set cf_value = target.cf.get('prometheus_exporter_' ~ k) -%}
        {%- set raw = cf_value if cf_value else v -%}
        {%- set final = param_value(raw)|trim -%}
        {%- set params_ns.parts = params_ns.parts + [('__param_' ~ k, final)] -%}
      {%- endfor -%}

      {#- Custom fields not covered by config context params. -#}
      {%- set seen_keys = ctx_params.keys() | list -%}
      {%- for cf_name, cf_value in target.cf.items() -%}
        {%- if cf_name.startswith('prometheus_exporter_') and cf_value -%}
          {%- set param_key = cf_name[20:] -%}
          {%- if param_key not in seen_keys -%}
            {%- set final = param_value(cf_value)|trim -%}
            {%- set params_ns.parts = params_ns.parts + [('__param_' ~ param_key, final)] -%}
          {%- endif -%}
        {%- endif -%}
      {%- endfor -%}

      {%- set fields = [
        ('__param_target', param_target),
      ] + params_ns.parts + [
        ('target_name', target.name),
        ('site', target.site.name if target.site else None),
        ('dc', target.site.slug if target.site else None),
        ('cluster', target.cluster.name if target.cluster else None),
        ('tenant', target.tenant.name if target.tenant else None),
        ('device_role', target.role.name if target.role else None),
        ('platform', target.platform.name if target.platform else None),
        ('model', target.device_type.model if target.device_type else None),
        ('device_type', target.device_type.slug if target.device_type else None),
      ] -%}
      {%- set ns.rows = ns.rows + [(scrape_target, fields)] -%}
    {%- endif -%}
  {%- endif -%}
{%- endfor -%}
[
{%- for scrape_target, fields in ns.rows %}
    {
        "targets": ["{{ scrape_target }}"],
        "labels": {{ build_labels(fields) }}
    }{% if not loop.last %},{% endif %}
{%- endfor %}
]
```

### Service template (`ipam.service`)

```jinja
{#-
  Prometheus HTTP SD export template — Services.
  Bind to: ipam.service
-#}

{%- macro jstr(s) -%}
{{ s|string|replace('\\', '\\\\')|replace('"', '\\"')|replace('\n', '\\n')|replace('\r', '\\r')|replace('\t', '\\t') }}
{%- endmacro -%}

{%- macro build_labels(fields) -%}
  {%- set lbl = namespace(parts=[]) -%}
  {%- for name, value in fields -%}
    {%- if value -%}
      {%- set lbl.parts = lbl.parts + ['"' ~ name ~ '": "' ~ jstr(value)|trim ~ '"'] -%}
    {%- endif -%}
  {%- endfor -%}
  { {{ lbl.parts | join(', ') }} }
{%- endmacro -%}

{%- macro target_addr(ip, port) -%}
  {%- if ip.version == 6 -%}
    [{{ ip }}]:{{ port }}
  {%- else -%}
    {{ ip }}:{{ port }}
  {%- endif -%}
{%- endmacro -%}

{%- macro param_value(v) -%}
{%- if v is none or v == '' -%}
{%- elif v is string -%}
{{ v }}
{%- elif v is iterable -%}
{{ v | select | map('string') | join(',') }}
{%- else -%}
{{ v }}
{%- endif -%}
{%- endmacro -%}

{%- set ns = namespace(rows=[]) -%}
{%- for service in queryset -%}
  {%- set parent = service.device or service.virtual_machine -%}
  {%- if parent and parent.status and parent.primary_ip -%}
    {%- set data = parent.get_config_context() -%}
    {%- set prom = data.get('prometheus-plugin-prometheus-sd', {}) -%}
    {%- set exporter = prom.get('exporter') -%}
    {%- set target_scheme = prom.get('target_scheme') -%}

    {#- Build params from parent's config context + parent's custom fields. -#}
    {%- set params_ns = namespace(parts=[]) -%}
    {%- set ctx_params = prom.get('params') or {} -%}
    {%- for k, v in ctx_params.items() -%}
      {%- set cf_value = parent.cf.get('prometheus_exporter_' ~ k) if parent.cf is defined else None -%}
      {%- set raw = cf_value if cf_value else v -%}
      {%- set final = param_value(raw)|trim -%}
      {%- set params_ns.parts = params_ns.parts + [('__param_' ~ k, final)] -%}
    {%- endfor -%}
    {%- if parent.cf is defined -%}
      {%- set seen_keys = ctx_params.keys() | list -%}
      {%- for cf_name, cf_value in parent.cf.items() -%}
        {%- if cf_name.startswith('prometheus_exporter_') and cf_value -%}
          {%- set param_key = cf_name[20:] -%}
          {%- if param_key not in seen_keys -%}
            {%- set final = param_value(cf_value)|trim -%}
            {%- set params_ns.parts = params_ns.parts + [('__param_' ~ param_key, final)] -%}
          {%- endif -%}
        {%- endif -%}
      {%- endfor -%}
    {%- endif -%}

    {%- set fields = [
      ('target_name', parent.name),
      ('service_name', service.name),
      ('service_protocol', service.protocol),
      ('site', parent.site.name if parent.site else None),
      ('dc', parent.site.slug if parent.site else None),
      ('cluster', parent.cluster.name if parent.cluster else None),
      ('tenant', parent.tenant.name if parent.tenant else None),
      ('device_role', parent.role.name if parent.role else None),
      ('platform', parent.platform.name if parent.platform else None),
      ('model', parent.device_type.model if parent.device_type is defined and parent.device_type else None),
      ('device_type', parent.device_type.slug if parent.device_type is defined and parent.device_type else None),
    ] -%}

    {%- for port in service.ports -%}
      {%- set probe_addr = target_addr(parent.primary_ip.address.ip, port)|trim -%}
      {%- if exporter -%}
        {%- set scrape_target = exporter -%}
        {%- if target_scheme -%}
          {%- set param_target = target_scheme ~ '://' ~ probe_addr -%}
        {%- else -%}
          {%- set param_target = probe_addr -%}
        {%- endif -%}
        {%- set row_fields = [('__param_target', param_target)] + params_ns.parts + fields -%}
      {%- else -%}
        {%- set scrape_target = probe_addr -%}
        {%- set row_fields = fields -%}
      {%- endif -%}
      {%- set ns.rows = ns.rows + [(scrape_target, row_fields)] -%}
    {%- endfor -%}
  {%- endif -%}
{%- endfor -%}
[
{%- for scrape_target, fields in ns.rows %}
    {
        "targets": ["{{ scrape_target }}"],
        "labels": {{ build_labels(fields) }}
    }{% if not loop.last %},{% endif %}
{%- endfor %}
]
```

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

- **Hyphens in dict keys** — `{{ data.prometheus-plugin-prometheus-sd }}` is parsed as subtraction. Always use bracket subscript: `data['prometheus-plugin-prometheus-sd']`.
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
