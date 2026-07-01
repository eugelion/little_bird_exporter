# Bird Exporter

This service queries the Kubernetes API, finds pods by regex, runs `exec` commands inside those pods, and exposes the results as Prometheus metrics on `/metrics`.

The core idea is simple: add new data collection through `templates/bird-exporter/values.yaml` (no new Python collector file required for standard cases).

## How a Collector Works

A collector describes one check:

```yaml
collectors:
  - name: bird_bgp_global
    collectorType: generic_exec
    namespace: kube-system
    podNameRegex: "calico-no"
    execTimeoutSeconds: 30
    execCommand: "birdcl show protocols all {protocol} | grep 'BGP state'"
    metrics:
      - name: bird_bgp_established
        description: "1 if BGP session is Established, 0 otherwise"
        labels: ["collector", "namespace", "pod", "protocol"]
        parser:
          type: regex_equals
          pattern: "BGP state:\\s*(?P<state>[A-Za-z]+)"
          group: state
          expected: Established
          successValue: 1
          failureValue: 0
```

Internal flow:

1. Exporter finds pods in `namespace` by `podNameRegex`.
2. It runs `execCommand` in each matched pod.
3. It waits up to `execTimeoutSeconds`.
4. It parses command stdout with parser rules.
5. It exposes metrics on `/metrics`.

## Collector Parameters

### `name`

Collector name. It goes into the `collector` label so you can identify where metrics come from.

```yaml
name: bird_bgp_global
```

### `collectorType`

Collector type. Current primary type:

```yaml
collectorType: generic_exec
```

It executes commands in pods and parses stdout according to YAML rules.

### `namespace`

Namespace where exporter searches for pods.

```yaml
namespace: kube-system
```

### `podNameRegex`

Regex for pod names. Keep it specific to avoid querying unintended pods.

```yaml
podNameRegex: "calico-no"
```

### `execTimeoutSeconds`

How long to wait for command execution in a pod.

```yaml
execTimeoutSeconds: 30
```

If a pod does not respond or command hangs, collector:

- increments `bird_exporter_collector_errors_total`;
- updates failure metrics (for example, `bird_bgp_established = 0`);
- sets `state="unknown"` for `regex_state` parser.

### `execCommand`

Command executed inside each matched pod.

```yaml
execCommand: "birdcl show protocols all {protocol} | grep 'BGP state'"
```

If command contains `{protocol}`, value is injected from `birdProtocol` in `values.yaml`.

If protocol is not needed, just do not use `{protocol}`:

```yaml
execCommand: "filebeat test config"
```

### `metrics`

List of metrics derived from command stdout. One command can produce multiple metrics.

## Metric Parameters

### `name`

Prometheus metric name.

```yaml
name: bird_bgp_established
```

### `description`

Metric HELP text used in Prometheus exposition format.

```yaml
description: "1 if BGP session is Established, 0 otherwise"
```

### `labels`

Metric labels. Common base labels:

- `collector`
- `namespace`
- `pod`
- `protocol`

Some parsers add extra labels, for example `state`.

```yaml
labels: ["collector", "namespace", "pod", "protocol", "state"]
```

### `parser`

Rule that transforms stdout into a metric value.

## Parser Types

### `contains`

Checks if a string is present in command output.

```yaml
parser:
  type: contains
  pattern: "Config OK"
  successValue: 1
  failureValue: 0
```

If stdout contains `Config OK`, metric is `1`; otherwise `0`.

### `regex_equals`

Extracts a regex group and compares it with expected value.

```yaml
parser:
  type: regex_equals
  pattern: "BGP state:\\s*(?P<state>[A-Za-z]+)"
  group: state
  expected: Established
  successValue: 1
  failureValue: 0
```

For stdout:

```text
BGP state: Established
```

`group: state` resolves to `Established`; if it equals `expected`, metric is `1`, otherwise `0`.

### `regex_state`

Extracts a regex group and stores it as a label.

```yaml
parser:
  type: regex_state
  pattern: "BGP state:\\s*(?P<state>[A-Za-z]+)"
  group: state
  stateLabel: state
  value: 1
```

Example result:

```text
bird_bgp_state{collector="bird_bgp_global",namespace="kube-system",pod="calico-node-1",protocol="Global_10_26_212_1",state="Established"} 1
```

If regex does not match, exporter sets:

```text
state="unknown"
```

### `regex_value`

Extracts a numeric value from regex group.

```yaml
parser:
  type: regex_value
  pattern: "workers:\\s*(?P<count>\\d+)"
  group: count
```

If stdout is:

```text
workers: 5
```

metric value is `5`.

### `number`

Treats whole stdout as a numeric value.

```yaml
parser:
  type: number
  failureValue: 0
```

If command returns:

```text
42
```

metric value is `42`.  
If stdout is empty, exporter uses `failureValue`.

## Examples

### BGP Established

```yaml
collectors:
  - name: bird_bgp_global
    collectorType: generic_exec
    namespace: kube-system
    podNameRegex: "calico-no"
    execTimeoutSeconds: 30
    execCommand: "birdcl show protocols all {protocol} | grep 'BGP state'"
    metrics:
      - name: bird_bgp_established
        description: "1 if BGP session is Established, 0 otherwise"
        labels: ["collector", "namespace", "pod", "protocol"]
        parser:
          type: regex_equals
          pattern: "BGP state:\\s*(?P<state>[A-Za-z]+)"
          group: state
          expected: Established
          successValue: 1
          failureValue: 0
```

### BGP State as Label

```yaml
collectors:
  - name: bird_bgp_global
    collectorType: generic_exec
    namespace: kube-system
    podNameRegex: "calico-no"
    execTimeoutSeconds: 30
    execCommand: "birdcl show protocols all {protocol} | grep 'BGP state'"
    metrics:
      - name: bird_bgp_state
        description: "Current BGP state as label"
        labels: ["collector", "namespace", "pod", "protocol", "state"]
        parser:
          type: regex_state
          pattern: "BGP state:\\s*(?P<state>[A-Za-z]+)"
          group: state
          stateLabel: state
          value: 1
```

### Filebeat Config Check

For this collector protocol is not required. Just do not use `{protocol}` in command.

```yaml
collectors:
  - name: filebeat_config_check
    collectorType: generic_exec
    namespace: kube-system
    podNameRegex: "filebeat"
    execTimeoutSeconds: 30
    execCommand: "filebeat test config"
    metrics:
      - name: filebeat_config_ok
        description: "1 if Filebeat config test passed, 0 otherwise"
        labels: ["collector", "namespace", "pod"]
        parser:
          type: contains
          pattern: "Config OK"
          successValue: 1
          failureValue: 0
```

### Numeric Metric From Command

```yaml
collectors:
  - name: nginx_workers
    collectorType: generic_exec
    namespace: default
    podNameRegex: "nginx"
    execTimeoutSeconds: 10
    execCommand: "ps aux | grep '[n]ginx: worker' | wc -l"
    metrics:
      - name: nginx_worker_processes
        description: "Number of nginx worker processes"
        labels: ["collector", "namespace", "pod"]
        parser:
          type: number
          failureValue: 0
```

## Timeout and Error Behavior

If command hangs or pod does not respond, exporter does not wait forever. It stops exec by `execTimeoutSeconds`.

On error:

- `bird_exporter_collector_errors_total` is incremented;
- parser receives empty stdout;
- `regex_equals` returns `failureValue`;
- `regex_state` sets `state="unknown"`;
- `number` returns `failureValue` for empty stdout.

For BGP, that typically means:

```text
bird_bgp_established = 0
bird_bgp_state{state="unknown"} = 1
```

## Load Considerations

Approximate load:

```text
collectors * matched_pods / pollIntervalSeconds
```

If you have 1 collector, 20 pods, and 30s interval, exporter performs 20 exec calls every 30 seconds. Usually fine, but kube-apiserver and kubelet are not unlimited.

Recommended defaults:

- do not set `pollIntervalSeconds` too low;
- keep `podNameRegex` specific;
- keep `replicaCount: 1` unless you intentionally want duplicate polling;
- avoid too many heavy commands.

## Helm

Main config:

```text
templates/bird-exporter/values.yaml
```

templates renders it into ConfigMap:

```text
templates/bird-exporter/templates/configmap.yaml
```

Install:

```bash
helm upgrade --install bird-exporter ./templates/bird-exporter
```

Install with per-cluster overrides:

```bash
helm upgrade --install bird-exporter ./templates/bird-exporter \
  -f templates/bird-exporter/values-cluster-example.yaml
```

