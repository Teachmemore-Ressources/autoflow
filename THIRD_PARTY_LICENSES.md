# Third-Party Licenses

This document lists all third-party components integrated into Autoflow's Docker Compose stack, along with their respective licenses and obligations.

> **Last updated:** 2026-05-23  
> **Autoflow version:** 1.0.0  
> **Copyright:** 2026 Armel NGANDO — Licensed under Apache 2.0

---

## Table of Contents

- [Automation & Orchestration](#automation--orchestration)
- [Web Framework & API](#web-framework--api)
- [Infrastructure & Proxy](#infrastructure--proxy)
- [Source Control & Registry](#source-control--registry)
- [Databases & Storage](#databases--storage)
- [Observability — AGPL-3.0 Components](#observability--agpl-30-components)
- [Monitoring & Alerting](#monitoring--alerting)
- [Security](#security)
- [Backup](#backup)
- [Python Libraries](#python-libraries)
- [Summary Table](#summary-table)
- [AGPL-3.0 Usage Statement](#agpl-30-usage-statement)

---

## Automation & Orchestration

### AWX 24.6.1

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | Red Hat, Inc. and the Ansible community |
| **Source** | https://github.com/ansible/awx |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

**Obligations (Apache 2.0):**
- Include a copy of the Apache 2.0 license when redistributing.
- Retain all copyright, patent, trademark, and attribution notices.
- State changes if the Work is modified.
- Include a NOTICE file if one is provided.

**Note:** AWX is the core automation engine powering Autoflow. Autoflow ships AWX unmodified as a Docker image. The custom configuration files (`awx/settings.py`, `awx/credentials.py`) are original Autoflow code and do not constitute a Derivative Work of AWX under Apache 2.0.

---

## Web Framework & API

### FastAPI 0.115.5

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Sebastián Ramírez |
| **Source** | https://github.com/tiangolo/fastapi |
| **License text** | https://github.com/tiangolo/fastapi/blob/master/LICENSE |

**Obligations (MIT):** Include copyright notice and license text when redistributing.

---

### Pydantic v2 2.10.3

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Samuel Colvin and contributors |
| **Source** | https://github.com/pydantic/pydantic |
| **License text** | https://github.com/pydantic/pydantic/blob/main/LICENSE |

**Obligations (MIT):** Include copyright notice and license text when redistributing.

---

## Infrastructure & Proxy

### Traefik v2.11

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Containous / Traefik Labs |
| **Source** | https://github.com/traefik/traefik |
| **License text** | https://github.com/traefik/traefik/blob/master/LICENSE.md |

**Obligations (MIT):** Include copyright notice and license text when redistributing.

---

## Source Control & Registry

### Gitea 1.23

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | The Gitea Authors |
| **Source** | https://github.com/go-gitea/gitea |
| **License text** | https://github.com/go-gitea/gitea/blob/main/LICENSE |

**Obligations (MIT):** Include copyright notice and license text when redistributing.

---

## Databases & Storage

### PostgreSQL 15.17

| Field | Value |
|-------|-------|
| **License** | PostgreSQL License (BSD-like) |
| **Author** | The PostgreSQL Global Development Group |
| **Source** | https://www.postgresql.org/ |
| **License text** | https://www.postgresql.org/about/licence/ |

**Obligations (PostgreSQL License):** Include copyright notice in documentation or redistribution. This is a permissive license — no copyleft restrictions.

---

### Redis 7.4.8

| Field | Value |
|-------|-------|
| **License** | BSD 3-Clause |
| **Author** | Redis Ltd. and contributors |
| **Source** | https://github.com/redis/redis |
| **License text** | https://github.com/redis/redis/blob/unstable/COPYING |

**Obligations (BSD 3-Clause):** Include copyright notice and the three BSD clauses when redistributing. Do not use the names of the copyright holders to endorse or promote derived products without permission.

---

## Observability — AGPL-3.0 Components

> **IMPORTANT LEGAL NOTICE**
>
> The following components are licensed under the **GNU Affero General Public License v3 (AGPL-3.0)**. This section documents their usage in Autoflow and the applicable obligations.
>
> See [AGPL-3.0 Usage Statement](#agpl-30-usage-statement) at the end of this document for the full position statement.

---

### Grafana 12.4.2

| Field | Value |
|-------|-------|
| **License** | AGPL-3.0 |
| **Author** | Grafana Labs |
| **Source** | https://github.com/grafana/grafana |
| **License text** | https://github.com/grafana/grafana/blob/main/LICENSE |

**Usage in Autoflow:** Grafana is run as an unmodified Docker container. Autoflow interacts with Grafana exclusively through its documented HTTP API (datasource provisioning, dashboard import). No Grafana source code is modified or incorporated into Autoflow's custom services.

> ⚠️ **AGPL obligation note:** If Autoflow is offered as a SaaS product where users interact with Grafana over a network, the AGPL-3.0 network use provision may require making the complete Grafana source code (including any modifications) available. Since Autoflow uses Grafana **unmodified**, the standard upstream source code satisfies this requirement.

---

### Loki 3.4.2

| Field | Value |
|-------|-------|
| **License** | AGPL-3.0 |
| **Author** | Grafana Labs |
| **Source** | https://github.com/grafana/loki |
| **License text** | https://github.com/grafana/loki/blob/main/LICENSE |

**Usage in Autoflow:** Loki is run as an unmodified Docker container. Autoflow's Promtail agent sends logs to Loki via its documented HTTP push API. No Loki source code is modified or incorporated into Autoflow's custom services.

> ⚠️ **AGPL obligation note:** Same as Grafana above. Unmodified usage with documented APIs.

---

### Tempo 2.6.1

| Field | Value |
|-------|-------|
| **License** | AGPL-3.0 |
| **Author** | Grafana Labs |
| **Source** | https://github.com/grafana/tempo |
| **License text** | https://github.com/grafana/tempo/blob/main/LICENSE |

**Usage in Autoflow:** Tempo is run as an unmodified Docker container. Autoflow's OpenTelemetry Collector sends traces to Tempo via its documented OTLP API. No Tempo source code is modified or incorporated into Autoflow's custom services.

> ⚠️ **AGPL obligation note:** Same as Grafana above. Unmodified usage with documented APIs.

---

### MinIO (RELEASE.2024-10-13)

| Field | Value |
|-------|-------|
| **License** | AGPL-3.0 |
| **Author** | MinIO, Inc. |
| **Source** | https://github.com/minio/minio |
| **License text** | https://github.com/minio/minio/blob/master/LICENSE |

**Usage in Autoflow:** MinIO is run as an unmodified Docker container as an S3-compatible object storage backend for Loki log chunks. Autoflow does not interact directly with MinIO's API — communication occurs internally between Loki and MinIO. No MinIO source code is modified or incorporated into Autoflow's custom services.

> ⚠️ **AGPL obligation note:** Same as Grafana above. Unmodified usage as an internal backend. For commercial SaaS deployments, consult [MinIO's commercial licensing](https://min.io/pricing) if AGPL compliance is not acceptable for your use case.

---

## Monitoring & Alerting

### Prometheus v3.11.1

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | The Prometheus Authors |
| **Source** | https://github.com/prometheus/prometheus |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

**Obligations (Apache 2.0):** Same as AWX above.

---

### Alertmanager 0.32

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | The Prometheus Authors |
| **Source** | https://github.com/prometheus/alertmanager |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

**Obligations (Apache 2.0):** Same as AWX above.

---

### Node Exporter

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | The Prometheus Authors |
| **Source** | https://github.com/prometheus/node_exporter |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

---

### Postgres Exporter

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | The Prometheus Community |
| **Source** | https://github.com/prometheus-community/postgres_exporter |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

---

### Redis Exporter

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Oliver Paukstadt |
| **Source** | https://github.com/oliver006/redis_exporter |
| **License text** | https://github.com/oliver006/redis_exporter/blob/master/LICENSE |

---

### OpenTelemetry Collector

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | OpenTelemetry Authors |
| **Source** | https://github.com/open-telemetry/opentelemetry-collector |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

---

## Security

### Trivy 0.63.0

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | Aqua Security |
| **Source** | https://github.com/aquasecurity/trivy |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

**Obligations (Apache 2.0):** Same as AWX above.

---

## Backup

### Restic (latest)

| Field | Value |
|-------|-------|
| **License** | BSD 2-Clause |
| **Author** | Alexander Neumann and contributors |
| **Source** | https://github.com/restic/restic |
| **License text** | https://github.com/restic/restic/blob/master/LICENSE |

**Obligations (BSD 2-Clause):** Include copyright notice and the two BSD clauses in documentation or redistribution.

---

## Python Libraries

### PyJWT 2.10.1

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Jose Padilla |
| **Source** | https://github.com/jpadilla/pyjwt |
| **License text** | https://github.com/jpadilla/pyjwt/blob/master/LICENSE |

---

### slowapi 0.1.9

| Field | Value |
|-------|-------|
| **License** | MIT |
| **Author** | Romain Clement |
| **Source** | https://github.com/laurentS/slowapi |
| **License text** | https://github.com/laurentS/slowapi/blob/master/LICENSE |

---

### opentelemetry-sdk 1.27.0

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | OpenTelemetry Authors |
| **Source** | https://github.com/open-telemetry/opentelemetry-python |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

---

### ldap3 (latest)

| Field | Value |
|-------|-------|
| **License** | LGPL-3.0 |
| **Author** | Giovanni Cannata |
| **Source** | https://github.com/cannatag/ldap3 |
| **License text** | https://github.com/cannatag/ldap3/blob/dev/COPYING.LESSER.txt |

**Obligations (LGPL-3.0):**
- The LGPL allows use of the library in proprietary/commercial software **without** copyleft contamination, provided:
  - The library is used dynamically (standard pip install — not statically linked).
  - Users can replace the ldap3 library with a compatible version.
  - The LGPL license text is included.
- Autoflow uses ldap3 as a standard Python dependency (dynamic linking). This use is LGPL-compliant.

---

### bcrypt (latest)

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 |
| **Author** | The Python Cryptographic Authority |
| **Source** | https://github.com/pyca/bcrypt |
| **License text** | https://www.apache.org/licenses/LICENSE-2.0 |

---

### cryptography (latest)

| Field | Value |
|-------|-------|
| **License** | Apache 2.0 AND BSD 3-Clause (dual license) |
| **Author** | The Python Cryptographic Authority (PyCA) |
| **Source** | https://github.com/pyca/cryptography |
| **License text** | https://github.com/pyca/cryptography/blob/main/LICENSE |

**Note:** The `cryptography` package is dual-licensed under Apache 2.0 and BSD 3-Clause. You may choose which license to comply with. Autoflow treats it as Apache 2.0 for consistency.

---

## Summary Table

| Component | Version | License | Copyleft? | Redistribution safe? |
|-----------|---------|---------|-----------|---------------------|
| AWX | 24.6.1 | Apache 2.0 | No | Yes |
| FastAPI | 0.115.5 | MIT | No | Yes |
| Pydantic v2 | 2.10.3 | MIT | No | Yes |
| Traefik | v2.11 | MIT | No | Yes |
| Gitea | 1.23 | MIT | No | Yes |
| PostgreSQL | 15.17 | PostgreSQL | No | Yes |
| Redis | 7.4.8 | BSD 3-Clause | No | Yes |
| **Grafana** | **12.4.2** | **AGPL-3.0** | **Yes (network)** | **See note** |
| **Loki** | **3.4.2** | **AGPL-3.0** | **Yes (network)** | **See note** |
| **Tempo** | **2.6.1** | **AGPL-3.0** | **Yes (network)** | **See note** |
| **MinIO** | RELEASE.2024-10-13 | **AGPL-3.0** | **Yes (network)** | **See note** |
| Prometheus | v3.11.1 | Apache 2.0 | No | Yes |
| Alertmanager | 0.32 | Apache 2.0 | No | Yes |
| Trivy | 0.63.0 | Apache 2.0 | No | Yes |
| Restic | latest | BSD 2-Clause | No | Yes |
| PyJWT | 2.10.1 | MIT | No | Yes |
| slowapi | 0.1.9 | MIT | No | Yes |
| opentelemetry | 1.27.0 | Apache 2.0 | No | Yes |
| ldap3 | latest | LGPL-3.0 | Weak | Yes (dynamic) |
| bcrypt | latest | Apache 2.0 | No | Yes |
| cryptography | latest | Apache 2.0 + BSD | No | Yes |

---

## AGPL-3.0 Usage Statement

The following components are licensed under the GNU Affero General Public
License v3 (AGPL-3.0): **Grafana, Loki, Tempo, MinIO**.

Autoflow's position regarding these components:

> These components are used **without modification of their source code**,
> exclusively via their documented HTTP APIs. Autoflow does not distribute
> code derived from these components. The custom Autoflow services
> (Autoflow API, Event Engine, PKI Service, Security Scanner, EE Builder,
> Deploy Wizard) are original works developed independently and do not
> incorporate any code from these AGPL-licensed components.

**Consequences for the three Autoflow deployment models:**

| Model | AGPL impact | Recommendation |
|-------|-------------|----------------|
| **On-premise** (customer installs on their own infrastructure) | Minimal — customer runs AGPL software themselves. Standard open-source usage. | Recommend customers read AGPL terms. No action required from Autoflow. |
| **MSP / Integrator** (Autoflow installs on customer's behalf) | Equivalent to on-premise. The AGPL software runs on the customer's infrastructure. | Disclose AGPL components in contract. Include this file. |
| **SaaS** (Autoflow hosts for multiple customers) | **Higher risk** — AGPL network use provision. Users interacting with Grafana/Loki/Tempo over a network may trigger source code disclosure obligations. Since all components are unmodified, pointing to upstream source code satisfies this obligation. | Obtain legal counsel. Consider purchasing commercial licenses from Grafana Labs and MinIO for SaaS deployments. |

**Commercial alternatives for AGPL components:**

- **Grafana Enterprise** — commercial license available from Grafana Labs: https://grafana.com/products/enterprise/
- **MinIO commercial license** — available from MinIO, Inc.: https://min.io/pricing

---

*This document is provided for informational purposes only and does not constitute legal advice. Consult a qualified legal professional for licensing decisions specific to your use case.*
