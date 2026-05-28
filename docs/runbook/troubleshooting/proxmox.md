---
title: Dépannage Proxmox
---

# Dépannage Proxmox

Problèmes rencontrés lors de l'intégration AWX ↔ Proxmox VE (API, permissions, SDN, templates Cloud-Init).

---

## Référence — Permissions requises pour le token API AWX

Avant tout dépannage, vérifier que le token `ansible@pve!awx` dispose bien des ACLs suivantes :

| Path | Rôle Proxmox | Pourquoi |
|---|---|---|
| `/` | `PVEVMAdmin` | Créer, cloner, configurer, démarrer/arrêter les VMs |
| `/sdn/zones` | `PVESDNUser` | Utiliser les bridges réseau rattachés à une zone SDN |
| `/storage` | `PVEDatastoreAdmin` | Allouer de l'espace disque (création/clonage) |
| `/storage/local-lvm` | `PVEDatastoreAdmin` | Storage spécifique si différent du storage racine |

```bash
# Vérification rapide via l'API Proxmox
curl -sk -X POST "https://<PVE_IP>:8006/api2/json/access/ticket" \
  -d "username=root@pam&password=<PASS>" | python3 -c "
import sys,json; d=json.load(sys.stdin); print(d['data']['ticket'][:20]+'...')"

# Puis lister les ACLs
curl -sk "https://<PVE_IP>:8006/api2/json/access/acl" \
  -H "Cookie: PVEAuthCookie=<TICKET>" | python3 -c "
import sys,json
for a in json.load(sys.stdin)['data']:
    if a.get('ugid') == 'ansible@pve':
        print(a['path'], '→', a['roleid'])"
```

```bash
# Ou directement sur le nœud Proxmox
pveum acl list | grep ansible
```

---

## Problème 1 — 403 Forbidden : `Permission check failed (/sdn/zones/..., SDN.Use)`

**Symptôme** : Un job AWX échoue lors du clonage de template avec :

```
fatal: [localhost]: FAILED! => {
  "msg": "Unable to clone vm <NAME> from vmid <ID>: 403 Forbidden:
          Permission check failed (/sdn/zones/localnetwork/vmbr0, SDN.Use)"
}
```

**Cause** : Le rôle `PVEVMAdmin` accordé sur `/` couvre toutes les opérations VM, mais **ne couvre pas** les zones SDN. Quand un bridge réseau (`vmbr0`, `vmbr1`, etc.) est rattaché à une zone SDN (ex : `localnetwork`), Proxmox vérifie `SDN.Use` séparément sur le chemin `/sdn/zones/<zone>`.

**Diagnostic** :
```bash
# Vérifier les ACLs actuelles sur le nœud Proxmox
pveum acl list | grep ansible

# S'il manque une ligne /sdn/zones → c'est la cause
```

**Correction** :

=== "Via CLI Proxmox (sur le nœud)"

    ```bash
    # Accorder PVESDNUser sur toutes les zones SDN (propagate)
    pveum acl modify /sdn/zones \
      --users ansible@pve \
      --roles PVESDNUser \
      --propagate 1
    ```

=== "Via l'API Proxmox (à distance)"

    ```bash
    # 1. Obtenir un ticket d'authentification
    TICKET=$(curl -sk -X POST "https://<PVE_IP>:8006/api2/json/access/ticket" \
      -d "username=root@pam&password=<PASS>" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['ticket'])")
    CSRF=$(curl -sk -X POST "https://<PVE_IP>:8006/api2/json/access/ticket" \
      -d "username=root@pam&password=<PASS>" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['CSRFPreventionToken'])")

    # 2. Appliquer l'ACL
    curl -sk -X PUT "https://<PVE_IP>:8006/api2/json/access/acl" \
      -H "Cookie: PVEAuthCookie=$TICKET" \
      -H "CSRFPreventionToken: $CSRF" \
      -d "path=/sdn/zones&users=ansible@pve&roles=PVESDNUser&propagate=1"
    ```

=== "Via l'UI Proxmox"

    **Datacenter → Permissions → Add → User Permission** :

    - Path : `/sdn/zones`
    - User : `ansible@pve`
    - Role : `PVESDNUser`
    - Propagate : ✓

**Vérification** :
```bash
# Sur le nœud, après correction
pveum acl list | grep ansible
# Doit afficher :
# /                ansible@pve  PVEVMAdmin       1
# /sdn/zones       ansible@pve  PVESDNUser       1
# /storage         ansible@pve  PVEDatastoreAdmin 1
# /storage/local-lvm ansible@pve PVEDatastoreAdmin 1
```

Relancer le job AWX → le clone doit passer sans 403.

!!! info "Pourquoi PVESDNUser et pas PVEVMAdmin ?"
    `PVEVMAdmin` ne contient pas `SDN.Use` par conception Proxmox (séparation des responsabilités réseau/VM).
    `PVESDNUser` est le rôle minimal qui inclut `SDN.Audit` + `SDN.Use` — suffisant pour créer des VMs sur un bridge SDN.

---

## Problème 2 — 403 Forbidden : `Permission check failed (/storage/..., Datastore.AllocateSpace)`

**Symptôme** :
```
msg: "Unable to clone vm from vmid <ID>: 403 Forbidden:
     Permission check failed (/storage/local-lvm, Datastore.AllocateSpace)"
```

**Cause** : Le storage cible n'est pas couvert par les ACLs du token.

**Correction** :
```bash
# Accorder PVEDatastoreAdmin sur le storage manquant
pveum acl modify /storage/<STORAGE_NAME> \
  --users ansible@pve \
  --roles PVEDatastoreAdmin \
  --propagate 1
```

---

## Problème 3 — 500 Internal Server Error lors du clonage

**Symptôme** : `msg: "Unable to clone vm: 500 Internal Server Error"`

**Diagnostic** :
```bash
# Consulter les logs Proxmox sur le nœud
journalctl -u pvedaemon --since "5 minutes ago" | grep -i "error\|clone"

# Ou depuis l'UI : Datacenter → <nœud> → Task History
```

**Causes fréquentes** :

=== "Template introuvable (vmid inexistant)"

    ```bash
    # Vérifier que le template existe
    qm list | grep <VMID>

    # Si absent : recréer le template Cloud-Init
    # Voir la procédure dans create_proxmox_templates.sh
    ```

=== "Espace disque insuffisant"

    ```bash
    # Sur le nœud Proxmox
    pvesm status
    df -h /var/lib/pve/local-btrfs 2>/dev/null || df -h /

    # Nettoyer les snapshots inutiles
    # UI : Storage → local-lvm → Content → supprimer les disques orphelins
    ```

=== "Lock sur le template (autre opération en cours)"

    ```bash
    # Vérifier les locks
    qm config <VMID> | grep lock

    # Forcer la suppression du lock (avec précaution)
    qm unlock <VMID>
    ```

---

## Problème 4 — `community.proxmox.proxmox` : fichier d'inventaire ignoré

**Symptôme** : Dans AWX, la source d'inventaire Proxmox affiche `0 hosts` ou l'erreur :
```
Skipping due to inventory source not ending in "proxmox.yaml" nor "proxmox.yml"
```

**Cause** : Le plugin `community.proxmox.proxmox` impose que le fichier d'inventaire soit nommé exactement `*.proxmox.yml` ou `*.proxmox.yaml`.

**Correction** : Renommer le fichier d'inventaire dans le repo Git :
```
inventories/proxmox_dynamic.yml  →  inventories/proxmox.yml
```

Et s'assurer que le fichier commence bien par :
```yaml
plugin: community.proxmox.proxmox
```

!!! warning "Pas de Jinja2 dans les champs du plugin"
    Le plugin d'inventaire ne résout **pas** les templates Jinja2 dans ses champs de configuration.
    Les valeurs comme `proxmox_url: "{{ proxmox_api_host }}"` **ne fonctionnent pas**.
    Utiliser les variables d'environnement injectées par le credential AWX à la place :

    ```yaml
    # inventories/proxmox.yml — correct
    plugin: community.proxmox.proxmox
    url: "{{ lookup('env', 'PROXMOX_URL') }}"
    user: "{{ lookup('env', 'PROXMOX_USER') }}"
    token_id: "{{ lookup('env', 'PROXMOX_TOKEN_ID') }}"
    token_secret: "{{ lookup('env', 'PROXMOX_TOKEN_SECRET') }}"
    validate_certs: false
    want_facts: true
    ```

---

## Problème 9 — VM ne démarre pas : `KVM virtualisation configured, but not available`

**Symptôme** : La tâche de démarrage échoue dans les logs Proxmox avec :

```
KVM virtualisation configured, but not available.
Either disable in VM configuration or enable in BIOS.
```

La VM reste à `status: "stopped"` indéfiniment.

**Cause** : Le nœud Proxmox est un **environnement virtualisé** (VM imbriquée) sans virtualisation
hardware disponible, ou VT-x/AMD-V est désactivé dans le BIOS/UEFI.
Les VMs Proxmox ont par défaut `kvm: 1` et `cpu: host`, ce qui exige KVM hardware.

**Diagnostic** :

```bash
# Sur le nœud Proxmox — vérifier que KVM est disponible
ls /dev/kvm && echo "KVM disponible" || echo "KVM absent"

# Vérifier que VT-x/AMD-V est activé
egrep -c '(vmx|svm)' /proc/cpuinfo
# 0 = pas de virtualisation hardware → utiliser kvm: false
```

**Correction selon l'environnement** :

=== "Proxmox dans une VM (lab / nested)"

    **Option A — Désactiver KVM dans la config VM (recommandé pour le lab)** :

    Dans `group_vars/all.yml` :
    ```yaml
    vm_cpu_type: "x86-64-v2"  # Minimum requis pour Rocky9/Ubuntu22+ (SSE4.2, POPCNT)
    vm_kvm: false              # Désactive l'accélération KVM hardware
    ```

    !!! warning "kvm64 insuffisant pour les OS modernes"
        `kvm64` ne fournit pas SSE4.2 ni POPCNT. **Rocky Linux 9 et Ubuntu 22.04+
        requièrent `x86-64-v2` minimum** — avec `kvm64`, le kernel panic au démarrage :
        ```
        Kernel panic - not syncing: Attempted to kill init! exitcode=0x00007f00
        ```
        (exit code 127 = instruction CPU manquante → init/systemd ne peut pas s'exécuter)

        | CPU type | SSE4.2 | POPCNT | Rocky9 | Ubuntu22 | Rocky8 | Ubuntu20 |
        |---|---|---|---|---|---|---|
        | `kvm64`     | ✗ | ✗ | ✗ PANIC | ✗ PANIC | ✓ | ✓ |
        | `x86-64-v2` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
        | `x86-64-v3` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

    Dans `01_clone_vm.yml`, **utiliser l'API REST directement** — ne pas passer `kvm` au module `proxmox_kvm` :

    !!! warning "Piège Jinja2 — `kvm: false` ignoré silencieusement"
        Le module `community.general.proxmox_kvm` reçoit la valeur Python `False` (booléen).
        Jinja2 la sérialise en chaîne `"False"` (capital F).
        Proxmox interprète toute chaîne non vide comme `True` → `kvm=1` appliqué malgré la valeur `false`.

        **Ne pas faire :**
        ```yaml
        community.general.proxmox_kvm:
          kvm: "{{ vm_kvm }}"   # Jinja2 rend false → "False" → truthy pour Proxmox
        ```

        **Solution — passer par l'API REST avec une valeur entière explicite :**
        ```yaml
        - name: "Configurer CPU type et KVM via API Proxmox"
          ansible.builtin.uri:
            url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/config"
            method: PUT
            headers:
              Authorization: "{{ _proxmox_auth }}"
            body_format: form-urlencoded
            body:
              cpu: "{{ vm_cpu_type }}"
              kvm: "{{ '1' if vm_kvm else '0' }}"   # chaîne "0" ou "1", pas un booléen
            validate_certs: false
            status_code: [200]

        - name: "Vérifier que kvm={{ '1' if vm_kvm else '0' }} est bien appliqué"
          ansible.builtin.uri:
            url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/config"
            method: GET
            headers:
              Authorization: "{{ _proxmox_auth }}"
            validate_certs: false
          register: _vm_config_check
          failed_when: >-
            (_vm_config_check.json.data.kvm | default(1) | int) != (1 if vm_kvm else 0)
        ```

    !!! info "Impact performance"
        `kvm: false` utilise l'émulation TCG (logicielle). C'est plus lent qu'avec KVM hardware,
        mais suffisant pour un lab de démonstration. Pour la production, activer la virtualisation
        imbriquée sur l'hyperviseur parent.

    **Option B — Activer la virtualisation imbriquée (nested virtualization)** :

    Si le parent est un hyperviseur Linux (KVM) :
    ```bash
    # Intel
    echo "options kvm_intel nested=1" > /etc/modprobe.d/kvm_intel.conf
    modprobe -r kvm_intel && modprobe kvm_intel

    # AMD
    echo "options kvm_amd nested=1" > /etc/modprobe.d/kvm_amd.conf
    modprobe -r kvm_amd && modprobe kvm_amd
    ```

    Si le parent est VMware ESXi : **VM → Edit Settings → CPU → Expose hardware assisted virtualization to guest OS**.

=== "Proxmox physique (BIOS)"

    Activer VT-x (Intel) ou AMD-V dans le BIOS/UEFI, puis `kvm: true` et `cpu: host`.

---

## Problème 10 — `shrinking disks is not supported` lors du resize

**Symptôme** : La tâche de redimensionnement échoue avec :

```
resize: shrinking disks is not supported
```

**Cause** : Le template Proxmox a un disque **plus grand** que la taille demandée dans le survey.
Ex : template `scsi0: ...size=20G` et `vm_disk_gb=15` → Proxmox refuse de réduire.

**Correction** : S'assurer que `vm_disk_gb` ≥ taille du disque du template.

Dans le survey AWX, corriger les choices du champ "Taille disque" :
```
Avant : 15, 20, 30   (15 < 20G du template → shrink refusé)
Après : 20, 30, 40   (20 = taille minimale du template)
```

!!! note
    Le playbook a `failed_when: false` sur cette tâche — le job ne plante pas si le resize
    échoue. La VM est créée avec la taille du template (20G). Seule la taille ≥ template
    sera effectivement appliquée.

---

## Problème 8 — Timeout lors du démarrage de la VM (`generating cloud-init ISO`)

**Symptôme** : La tâche de démarrage de la VM échoue avec :

```
fatal: [localhost]: FAILED! => {
  "msg": "Reached timeout while waiting for starting VM.
          Last line in task before timeout: [{'t': 'generating cloud-init ISO', 'n': 1}]"
}
```

**Cause** : Deux problèmes combinés :

1. **Timeout trop court** : `community.general.proxmox_kvm` avec `state: started` a un timeout **par défaut de 30 secondes**. La génération du Cloud-Init ISO prend plus de temps, surtout sur un stockage lent ou une première configuration.

2. **Race condition** : La régénération du Cloud-Init ISO (`PUT /qemu/{id}/cloudinit`) est une tâche asynchrone Proxmox. Si la VM démarre *pendant que* l'ISO est encore en cours de génération, Proxmox bloque le boot jusqu'à la fin — dépassant le timeout de 30s.

**Correction** :

Étape 1 — Attendre la fin de la tâche Cloud-Init avant de démarrer :

```yaml
- name: "Régénérer le disque Cloud-Init"
  ansible.builtin.uri:
    url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/cloudinit"
    method: PUT
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
    status_code: [200, 500]
  register: cloudinit_regen

# ❌ NE PAS utiliser le UPID dans l'URL — voir note ci-dessous
# ✅ Interroger la liste des tâches filtrée par vmid + typefilter
- name: "Attendre la fin de la génération Cloud-Init"
  ansible.builtin.uri:
    url: "https://{{ proxmox_api_host }}:8006/api2/json/nodes/{{ proxmox_api_node }}/tasks?vmid={{ vm_id }}&typefilter=qmcloudinit&limit=1"
    method: GET
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
  register: cloudinit_tasks
  until: >-
    cloudinit_tasks.json.data | length == 0 or
    'endtime' in (cloudinit_tasks.json.data[0] | default({}))
  retries: 30
  delay: 2
```

Étape 2 — Démarrer la VM via l'API directe plutôt que `proxmox_kvm` :

```yaml
# POST /status/start déclenche le démarrage et retourne un UPID
- name: "Envoyer la commande de démarrage via API Proxmox"
  ansible.builtin.uri:
    url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/status/start"
    method: POST
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
    status_code: [200]

# GET /status/current : poll jusqu'à status == "running" (max 10 min)
- name: "Attendre que la VM soit en état 'running'"
  ansible.builtin.uri:
    url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/status/current"
    method: GET
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
  register: vm_status
  until: vm_status.json.data.status == "running"
  retries: 120   # 120 × 5s = 10 minutes
  delay: 5
```

!!! danger "Ne pas utiliser `proxmox_kvm state=started` pour ce cas"
    Le paramètre `timeout` du module `community.general.proxmox_kvm` avec `state: started`
    contrôle l'attente de la tâche Proxmox de démarrage. Quand Proxmox intègre la génération
    du Cloud-Init ISO au processus de boot (même après une pré-génération), cette tâche peut
    dépasser le timeout — y compris avec `timeout: 300`.

    En passant par l'API directe + poll de `status/current`, on contrôle précisément le timeout
    et on évite la dépendance au comportement interne du module.

!!! warning "Pourquoi ne pas utiliser le UPID dans l'URL ?"
    `PUT /qemu/{id}/cloudinit` retourne un **UPID** Proxmox dans `data`, ex :
    `UPID:pve:000123AB:00000000:5F000000:qmcloudinit:200:root@pam:`

    En théorie on pourrait faire `GET /nodes/{node}/tasks/{upid}/status`, mais les `:` du UPID
    **ne sont pas encodés** par Ansible lors de la construction de l'URL. L'URL résultante est
    mal parsée et Proxmox répond avec la **liste de toutes les tâches** au lieu du statut de
    la tâche précise. Symptôme :

    ```
    'list object' has no attribute 'status'
    ```

    **Contournement** : `GET /tasks?vmid=X&typefilter=qmcloudinit&limit=1` — on attend
    qu'un champ `endtime` apparaisse dans la première entrée (tâche terminée).

---

## Problème 7 — Clé SSH rejetée par l'API Proxmox (`invalid urlencoded string`)

**Symptôme** : La tâche "Configurer Cloud-Init" échoue avec HTTP 400 :

```json
{
  "errors": {
    "sshkeys": "invalid format - invalid urlencoded string:
    ssh-ed25519%20AAAA...Qy2QLpsGTu6IWQ35%20ansible-awx\n"
  },
  "message": "Parameter verification failed."
}
```

**Cause** : Deux problèmes combinés dans l'encodage du champ `sshkeys` de l'API Proxmox :

1. **Le slash `/`** dans la clé (ex : `FqiDXn/qQrt5...`) n'est pas encodé.
   Le filtre Jinja2 `| urlencode` considère `/` comme un caractère "safe" (RFC 3986 path) et ne l'encode pas. Proxmox attend pourtant `%2F`.

2. **Le `\n` final** injecté par le Survey AWX (le champ texte ajoute une nouvelle ligne) se retrouve dans la valeur envoyée à l'API.

**Correction** : pré-encoder la clé avec `set_fact` avant de l'utiliser dans `uri` :

```yaml
- name: "Préparer l'encodage de la clé SSH"
  ansible.builtin.set_fact:
    _sshkey_encoded: "{{ cloudinit_ssh_pubkey | trim | urlencode | replace('/', '%2F') }}"

- name: "Configurer Cloud-Init via API Proxmox"
  ansible.builtin.uri:
    body:
      sshkeys: "{{ _sshkey_encoded }}"   # ← utiliser la variable pré-encodée
```

- `| trim` supprime le `\n` final
- `| urlencode` encode les espaces en `%20`
- `| replace('/', '%2F')` encode les slashes restants

!!! info "Pourquoi ne pas passer la clé directement au module `proxmox_kvm` ?"
    Le module `community.general.proxmox_kvm` ne gère pas le champ `sshkeys` (Cloud-Init SSH keys) — il faut obligatoirement passer par l'API REST Proxmox via `ansible.builtin.uri` pour configurer ce paramètre.

---

## Problème 6 — Variable indéfinie dans `01_clone_vm.yml` (`vm_cpu_type`, `vm_start_on_boot`, …)

**Symptôme** : Un job AWX échoue sur la tâche de configuration VM avec :

```
fatal: [localhost]: FAILED! => {
  "msg": "The task includes an option with an undefined variable.
          'vm_cpu_type' is undefined"
}
```

**Cause** : Ces variables sont déclarées dans `roles/vm_baseline/defaults/main.yml`.
Les `defaults` d'un rôle ne sont chargés que quand le rôle s'exécute.
`01_clone_vm.yml` tourne sur `localhost` sans inclure le rôle → les defaults ne sont **jamais** chargés.

```
01_clone_vm.yml (localhost, sans rôle)
    └── utilise vm_cpu_type       ← undefined ici !

02_configure_vm.yml (VM, avec rôle vm_baseline)
    └── roles/vm_baseline/defaults/main.yml  ← vm_cpu_type: "host" ici seulement
```

**Règle générale** : toute variable utilisée dans un playbook qui ne charge pas le rôle où elle est définie doit être déclarée dans `group_vars/all.yml` (portée globale) ou dans `vars:` du play.

**Correction** : ajouter les variables manquantes dans `playbooks/group_vars/all.yml` :

```yaml
# --- Options VM par défaut (utilisées dans 01_clone_vm.yml) ---
vm_cpu_type: "host"       # Type de CPU virtuel — "host" passe les flags CPU natifs
vm_start_on_boot: true    # Démarrage automatique au boot du nœud
```

**Variables concernées** (utilisées dans `01_clone_vm.yml` mais non issues du Survey) :

| Variable | Valeur par défaut | Source correcte |
|---|---|---|
| `vm_cpu_type` | `"host"` | `group_vars/all.yml` |
| `vm_start_on_boot` | `true` | `group_vars/all.yml` |
| `vm_balloon` | `0` | Survey AWX (déjà présent) |

!!! tip "Bonne pratique"
    Séparer les variables en deux catégories :

    - **Survey AWX** → ce que l'utilisateur choisit à chaque déploiement (`vm_hostname`, `vm_ip`, `vm_os`, `vm_ram_mb`, …)
    - **`group_vars/all.yml`** → les constantes d'infrastructure du lab (`proxmox_api_host`, `vm_cpu_type`, `proxmox_storage`, …)

    Les `roles/*/defaults/main.yml` sont des valeurs de dernier recours — elles ne doivent pas être la seule source pour des variables partagées entre plusieurs playbooks.

---

## Problème 5 — `proxmox_api_token_secret` indéfini dans les playbooks

**Symptôme** : Un playbook utilisant le module `community.general.proxmox_kvm` échoue avec :
```
"The task includes an option with an undefined variable. 'proxmox_api_token_secret' is undefined"
```

**Cause** : Le type de credential AWX `Proxmox API Token` n'injectait que des variables d'environnement (`env`), pas des variables Ansible (`extra_vars`). Les modules Ansible lisent des variables, pas des variables d'environnement.

**Correction** : Le credential type doit injecter **les deux** :

```json
{
  "env": {
    "PROXMOX_URL": "https://{{ proxmox_api_host }}:8006",
    "PROXMOX_USER": "{{ proxmox_api_user }}",
    "PROXMOX_TOKEN_ID": "{{ proxmox_api_token_id }}",
    "PROXMOX_TOKEN_SECRET": "{{ proxmox_api_token_secret }}"
  },
  "extra_vars": {
    "proxmox_api_host": "{{ proxmox_api_host }}",
    "proxmox_api_user": "{{ proxmox_api_user }}",
    "proxmox_api_token_id": "{{ proxmox_api_token_id }}",
    "proxmox_api_token_secret": "{{ proxmox_api_token_secret }}"
  }
}
```

Voir la procédure complète : [Configuration AWX — Types de credentials personnalisés](../../configuration/awx.md#types-de-credentials-personnalises).

---

## Problème 11 — Tâche `qmstart` périmée retournée par `GET /tasks` {#probleme-11}

**Symptôme** : Le job AWX échoue à la tâche "Attendre la fin de la tâche de démarrage" dès la **première tentative** (retries: 0/60), avec :

```
fatal: [localhost]: FAILED! => {
  "failed_when_result": true,
  "json": {
    "data": [{
      "endtime": 1779885604,
      "id": "200",
      "status": "KVM virtualisation configured, but not available...",
      "type": "qmstart",
      "upid": "UPID:pve:00183F96:039B3F90:6A16E624:qmstart:200:ansible@pve!awx:"
    }]
  }
}
```

L'UPID dans le résultat correspond à un **run précédent** (starttime = endtime de l'ancienne tâche), pas à la commande `POST /status/start` qui vient d'être envoyée.

**Cause** : `GET /nodes/{node}/tasks?vmid=X&typefilter=qmstart&limit=1` retourne la tâche la plus récente enregistrée en base, **quelle que soit sa date**. Si le VM a déjà tenté de démarrer lors d'un run précédent (et échoué), cette ancienne tâche, dont `endtime` est déjà renseigné, est retournée immédiatement — avant même que la nouvelle tâche `qmstart` ait eu le temps d'apparaître dans la liste.

Le `until` attend `'endtime' in task` → déjà vrai → `failed_when` se déclenche sur l'ancienne erreur.

**Diagnostic** : Comparer l'UPID de la tâche retournée avec celui renvoyé par `POST /status/start` :
```yaml
- debug:
    msg:
      - "UPID attendu  : {{ vm_start.json.data }}"
      - "UPID retourné : {{ start_tasks.json.data[0].upid | default('aucun') }}"
```
Si les UPID diffèrent → tâche périmée.

**Correction** : Filtrer avec le paramètre `since=<timestamp>` pour n'exposer que les tâches démarrées **après** l'envoi de la commande de démarrage :

```yaml
# ── Étape A : enregistrer l'heure AVANT l'envoi de la commande ──
- name: "Enregistrer le timestamp avant démarrage"
  ansible.builtin.command: date +%s
  register: _ts_before_start
  changed_when: false

# ── Étape B : envoyer la commande de démarrage ──────────────────
- name: "Envoyer la commande de démarrage via API Proxmox"
  ansible.builtin.uri:
    url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/status/start"
    method: POST
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
    status_code: [200]
  register: vm_start

# ── Étape C : attendre la tâche avec filtre since= ─────────────
# since - 5 : marge de 5s pour absorber les micro-décalages d'horloge
- name: "Attendre la fin de la tâche de démarrage Proxmox"
  ansible.builtin.uri:
    url: >-
      https://{{ proxmox_api_host }}:8006/api2/json/nodes/{{ proxmox_api_node
      }}/tasks?vmid={{ vm_id }}&typefilter=qmstart&limit=1&since={{ _ts_before_start.stdout | int - 5 }}
    method: GET
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
    status_code: [200]
  register: start_tasks
  until: >-
    start_tasks.json.data | length > 0 and
    'endtime' in (start_tasks.json.data[0] | default({}))
  retries: 60
  delay: 5
  failed_when: >-
    start_tasks.json.data | length > 0 and
    'endtime' in (start_tasks.json.data[0]) and
    (start_tasks.json.data[0].get('status', 'OK') | string) not in ['OK', '']
```

!!! note "Différence vs l'approche UPID"
    Une alternative serait d'utiliser l'UPID renvoyé par `POST /status/start` pour appeler
    `GET /nodes/{node}/tasks/{upid}/status`. **Ne pas faire ça** : l'UPID contient des `:` qui
    corrompent le path URL (Ansible/requests ne les encode pas automatiquement dans le path).
    Le résultat serait un `400 Bad Request` ou une liste de tâches au lieu d'un objet tâche.
    Le filtre `since=` + `typefilter=` est plus robuste.

!!! tip "Nettoyer les VMs de test"
    Pour éviter les tâches périmées à l'avenir : supprimer les VMs laissées en état `stopped`
    après un run raté. Les tâches restent en base Proxmox même après suppression de la VM,
    mais elles n'apparaissent plus dans le filtre `vmid=` une fois la VM supprimée.

---

## Problème 12 — Timeout SSH après démarrage VM (`Timeout when waiting for X:22`) {#probleme-12}

**Symptôme** : La dernière tâche du playbook échoue après 301 secondes :

```
TASK [Attendre que le port SSH soit disponible sur 192.168.1.80]
fatal: [localhost]: FAILED! => {
  "changed": false,
  "elapsed": 301,
  "msg": "Timeout when waiting for 192.168.1.80:22"
}
PLAY RECAP
localhost : ok=14  changed=1  unreachable=0  failed=1  skipped=0
```

La VM est **running** (tâches 1-13 réussies, dont `GET /status/current` → `"running"`), et elle
répond au ping, mais SSH ne répond pas.

**Cause** : cloud-init doit réaliser plusieurs étapes après le premier boot avant que SSH soit
utilisable :

1. Initialiser les modules cloud-init
2. Configurer le réseau statique (peut nécessiter un redémarrage de l'interface)
3. Créer l'utilisateur (`ansible`) + répertoire `.ssh`
4. Écrire les clés autorisées (`authorized_keys`)
5. Démarrer `sshd`

Sur des templates récents avec un disque cloud-init froid, ce processus prend typiquement
**2 à 4 minutes** sur un lab nested. Le timeout par défaut (300s) avec un délai initial de 60s
ne laisse que ~240s de polling effectif — insuffisant.

**Diagnostic** : Avant de modifier les timeouts, vérifier que le problème est bien la durée et non
un problème de réseau ou de cloud-init cassé :

```yaml
# Ajouter avant le wait_for pour confirmer que cloud-init a bien le bon ipconfig0
- name: "Vérifier l'IP assignée avant d'attendre SSH"
  ansible.builtin.uri:
    url: "{{ _proxmox_base }}/qemu/{{ vm_id }}/config"
    method: GET
    headers:
      Authorization: "{{ _proxmox_auth }}"
    validate_certs: false
  register: _pre_ssh_config

- name: "Afficher l'IP configurée"
  ansible.builtin.debug:
    msg: "ipconfig0 : {{ _pre_ssh_config.json.data.ipconfig0 | default('NON DÉFINI') }}"
```

Si `ipconfig0` est absent → cloud-init config n'a pas été appliquée (voir [Problème 7](proxmox.md#probleme-7)).  
Si `ipconfig0` est correct → c'est juste un problème de délai, augmenter les timeouts.

**Correction** : Dans `01_clone_vm.yml`, augmenter `delay` et `timeout` du `wait_for` :

```yaml
- name: "Attendre que le port SSH soit disponible sur {{ vm_ip }}"
  ansible.builtin.wait_for:
    host: "{{ vm_ip }}"
    port: 22
    timeout: 600   # 10 min (était 300)
    delay: 120     # attendre 2 min avant le premier check (était 60)
    state: started
  register: ssh_wait
```

!!! note "delay vs timeout"
    `delay` = temps d'attente avant le **premier** test (laisse cloud-init démarrer).  
    `timeout` = durée **totale** maximale incluant le delay.  
    Avec `delay: 120` et `timeout: 600`, le module tente pendant 480s effectives après le delay.

!!! tip "Si SSH reste inaccessible après 10 min"
    1. Vérifier que le template a bien un serveur SSH installé (`openssh-server`)
    2. Vérifier que l'EE AWX est sur un réseau qui peut joindre la VM (même VLAN / routage actif)
    3. Se connecter à la console Proxmox (`noVNC`) pour voir l'état cloud-init en direct :
       ```bash
       # Depuis la console VM
       cloud-init status --long
       journalctl -u cloud-init --no-pager | tail -30
       ```
    4. Vérifier que `sshd` écoute :
       ```bash
       ss -tlnp | grep :22
       ```
