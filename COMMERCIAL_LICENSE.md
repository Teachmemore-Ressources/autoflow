# Commercial Licensing — Autoflow

**Copyright 2026 Armel NGANDO**  
Contact: [contact@getautoflow.dev](mailto:contact@getautoflow.dev)

---

## English

### 1. What Apache 2.0 Permits (Free Use)

Autoflow's custom code (Autoflow API, Event Engine, PKI Service, Security Scanner,
EE Builder, Deploy Wizard, configuration files) is licensed under the
**Apache License, Version 2.0**.

Apache 2.0 grants you the following rights **at no cost**:

| Right | Conditions |
|-------|------------|
| **Use** — run Autoflow for any purpose (personal, commercial, internal) | No conditions |
| **Copy** — reproduce the source code | Include copyright notice + license text |
| **Modify** — create derivative works, customise the platform | Document your changes |
| **Distribute** — share Autoflow or your modified version | Include copyright notice + license text + NOTICE file |
| **Sublicense** — embed Autoflow in your own product | Your product may use any compatible license |
| **Patent grant** — use any patents held by contributors | Terminates if you sue contributors for patent infringement |

**Attribution requirement:** Any redistribution (source or binary) must include:
1. A copy of the Apache 2.0 license text.
2. All existing copyright, patent, trademark, and attribution notices.
3. A copy of the `NOTICE` file.
4. A clear statement of any modifications made.

This covers the **typical use cases** for on-premise deployment, private cloud
hosting for your own organisation, and integration into your internal toolchain.

---

### 2. When a Commercial Agreement Is Required

A separate commercial agreement with Armel NGANDO is **required** before you:

| Scenario | Why a commercial agreement is needed |
|----------|--------------------------------------|
| **Offer Autoflow as a SaaS product** to paying third-party customers (i.e., you host Autoflow and bill customers for access) | Apache 2.0 does not restrict this, but the AGPL-3.0 components in the stack (Grafana, Loki, Tempo, MinIO) may impose obligations. A commercial agreement clarifies responsibilities and, where applicable, includes commercial licenses for those components. |
| **Sell a managed deployment service** where you deploy, operate, and support Autoflow on behalf of multiple unrelated clients under a recurring revenue model | Same as above. |
| **White-label or rebrand** Autoflow as your own product and sell it commercially | Attribution obligations under Apache 2.0 must be negotiated. |
| **Enterprise support contracts** — SLA, dedicated engineering, priority patches, security advisories | Not covered by the open-source license. |
| **Multi-tenant cloud platform** — isolating multiple customers on a shared Autoflow instance as a service | Technical and legal consultation required; current architecture is single-tenant. |

> **Simple rule:** if you are **charging third parties for access to a hosted
> Autoflow instance**, contact us. If you are deploying Autoflow **for your own
> organisation's internal use**, Apache 2.0 already covers you — no agreement
> needed.

---

### 3. How to Obtain a Commercial License

To discuss commercial licensing options, please reach out:

- **Email:** [contact@getautoflow.dev](mailto:contact@getautoflow.dev)
- **Subject line:** `Commercial License Inquiry — [Your Company Name]`

Please include:
1. A brief description of your intended use case.
2. Estimated number of end users or clients.
3. Deployment model (SaaS, managed service, white-label, support).

We will respond within **5 business days**.

Available commercial tiers (indicative — subject to change):

| Tier | For | Includes |
|------|-----|----------|
| **MSP / Integrator** | Agencies deploying for clients | Deployment rights for N clients, support SLA, security advisories |
| **SaaS Starter** | Early-stage SaaS products | Hosting rights, AGPL component compliance coverage, email support |
| **Enterprise** | Organisations requiring SLA & indemnification | All above + enterprise SLA, indemnification, dedicated support, roadmap input |

---

### 4. Limitation of Liability — AGPL Third-Party Components

Autoflow integrates several components licensed under the **GNU Affero General
Public License v3 (AGPL-3.0)**: Grafana, Loki, Tempo, and MinIO.

**Armel NGANDO makes no warranty and accepts no liability** for your compliance
with the AGPL-3.0 obligations that may arise from your specific use of these
components. In particular:

- If you offer Autoflow (or a product incorporating it) as a **network-accessible
  service**, the AGPL-3.0 may require you to make available the complete
  corresponding source code of those components (including any modifications).
  Since Autoflow uses these components **unmodified**, pointing users to the
  upstream repositories satisfies this obligation in the standard case.
- If you **modify** any AGPL-3.0 component, you must comply fully with AGPL-3.0,
  including making your modifications publicly available under AGPL-3.0.
- You are solely responsible for obtaining independent legal advice regarding
  your specific obligations under AGPL-3.0 for your deployment scenario.
- Commercial licenses for these components (Grafana Enterprise, MinIO commercial)
  are available directly from their respective vendors and are your responsibility
  to obtain if you require them.

This limitation of liability is in addition to, and does not supersede, the
disclaimer of warranty and limitation of liability set forth in Section 7 and
Section 8 of the Apache License, Version 2.0.

---

---

## Français

### 1. Ce qu'autorise la licence Apache 2.0 (usage libre)

Le code custom d'Autoflow (Autoflow API, Event Engine, PKI Service, Security Scanner,
EE Builder, Deploy Wizard, fichiers de configuration) est distribué sous la
**Licence Apache, Version 2.0**.

La licence Apache 2.0 vous accorde les droits suivants **gratuitement** :

| Droit | Conditions |
|-------|------------|
| **Utiliser** — faire tourner Autoflow pour tout usage (personnel, commercial, interne) | Aucune condition |
| **Copier** — reproduire le code source | Inclure le texte de copyright et de la licence |
| **Modifier** — créer des œuvres dérivées, personnaliser la plateforme | Documenter vos changements |
| **Distribuer** — partager Autoflow ou votre version modifiée | Inclure copyright + licence + fichier NOTICE |
| **Sous-licencier** — intégrer Autoflow dans votre propre produit | Votre produit peut utiliser toute licence compatible |
| **Grant de brevets** — utiliser les brevets détenus par les contributeurs | Prend fin si vous poursuivez les contributeurs en contrefaçon de brevet |

**Obligation d'attribution :** toute redistribution (source ou binaire) doit inclure :
1. Une copie du texte de la licence Apache 2.0.
2. Tous les avis de copyright, de brevet, de marque et d'attribution existants.
3. Une copie du fichier `NOTICE`.
4. Un énoncé clair de toute modification apportée.

Cela couvre les **cas d'usage typiques** : déploiement on-premise, hébergement
cloud privé pour votre propre organisation, et intégration dans votre outillage
interne.

---

### 2. Ce qui nécessite un accord commercial séparé

Un accord commercial séparé avec Armel NGANDO est **obligatoire** avant de :

| Scénario | Pourquoi un accord commercial est nécessaire |
|----------|----------------------------------------------|
| **Proposer Autoflow en SaaS** à des clients tiers payants (vous hébergez Autoflow et facturez l'accès) | Apache 2.0 ne l'interdit pas, mais les composants AGPL-3.0 de la stack (Grafana, Loki, Tempo, MinIO) peuvent générer des obligations. Un accord commercial clarifie les responsabilités et, le cas échéant, inclut des licences commerciales pour ces composants. |
| **Vendre un service de déploiement managé** — vous déployez, opérez et supportez Autoflow pour le compte de plusieurs clients non liés sous un modèle de revenus récurrents | Idem ci-dessus. |
| **Rebrandir ou white-labeler** Autoflow comme votre propre produit commercial | Les obligations d'attribution Apache 2.0 doivent être négociées. |
| **Contrats de support enterprise** — SLA, ingénierie dédiée, patchs prioritaires, avis de sécurité | Non couvert par la licence open-source. |
| **Plateforme cloud multi-tenant** — isolation de plusieurs clients sur une instance Autoflow partagée en tant que service | Consultation technique et juridique requise ; l'architecture actuelle est mono-tenant. |

> **Règle simple :** si vous **facturez des tiers pour l'accès à une instance
> Autoflow hébergée**, contactez-nous. Si vous déployez Autoflow pour
> **l'usage interne de votre propre organisation**, Apache 2.0 vous couvre déjà
> — aucun accord nécessaire.

---

### 3. Comment obtenir une licence commerciale

Pour discuter des options de licence commerciale :

- **Email :** [contact@getautoflow.dev](mailto:contact@getautoflow.dev)
- **Objet :** `Demande de licence commerciale — [Nom de votre entreprise]`

Merci d'inclure :
1. Une brève description de votre cas d'usage.
2. Le nombre estimé d'utilisateurs finaux ou de clients.
3. Le modèle de déploiement (SaaS, service managé, white-label, support).

Nous répondrons dans les **5 jours ouvrés**.

---

### 4. Limitation de responsabilité — composants tiers AGPL

Autoflow intègre plusieurs composants sous **licence GNU Affero General Public
License v3 (AGPL-3.0)** : Grafana, Loki, Tempo et MinIO.

**Armel NGANDO ne donne aucune garantie et n'accepte aucune responsabilité**
quant à votre conformité aux obligations AGPL-3.0 pouvant découler de votre
usage spécifique de ces composants. En particulier :

- Si vous proposez Autoflow (ou un produit l'incorporant) en tant que
  **service accessible en réseau**, l'AGPL-3.0 peut vous obliger à mettre à
  disposition le code source complet correspondant à ces composants (y compris
  les modifications). Autoflow utilisant ces composants **sans modification**,
  pointer les utilisateurs vers les dépôts upstream satisfait cette obligation
  dans le cas standard.
- Si vous **modifiez** un composant AGPL-3.0, vous devez vous conformer
  intégralement à l'AGPL-3.0, y compris en rendant vos modifications publiquement
  disponibles sous AGPL-3.0.
- Vous êtes seul responsable d'obtenir un avis juridique indépendant concernant
  vos obligations spécifiques au titre de l'AGPL-3.0 pour votre scénario de
  déploiement.
- Des licences commerciales pour ces composants (Grafana Enterprise, MinIO
  commercial) sont disponibles directement auprès de leurs éditeurs respectifs
  et relèvent de votre responsabilité si vous souhaitez vous en affranchir.

Cette limitation de responsabilité s'ajoute à, et ne remplace pas, la clause
d'exclusion de garantie et de limitation de responsabilité énoncée aux articles 7
et 8 de la Licence Apache, Version 2.0.

---

*Ce document est fourni à titre informatif uniquement et ne constitue pas un avis
juridique. Consultez un professionnel du droit qualifié pour les décisions de
licence propres à votre situation.*
