# Autoflow — Network Playbooks

Bibliothèque de playbooks Ansible pour l'automatisation réseau multi-vendeurs.
Utilise l'**EE Network** (`ee-network`) qui embarque NAPALM, Netmiko, Nornir et toutes
les collections constructeurs.

## Structure

```
network-playbooks/
├── cisco/
│   ├── ios_facts.yml          Collecte les facts IOS (version, interfaces, voisins CDP)
│   ├── ios_backup.yml         Sauvegarde la running-config dans /tmp/backups/
│   ├── ios_vlan.yml           Gestion VLAN (create/delete/rename)
│   └── ios_compliance.yml     Vérifie les lignes de config obligatoires
├── juniper/
│   ├── junos_facts.yml        Collecte les facts Junos (version, interfaces, routes)
│   └── junos_backup.yml       Sauvegarde la config Junos (set/XML) dans /tmp/backups/
├── arista/
│   ├── eos_facts.yml          Collecte les facts EOS (version, interfaces, LLDP)
│   └── eos_backup.yml         Sauvegarde la running-config EOS
├── f5/
│   ├── f5_facts.yml           Collecte les facts BIG-IP (pools, virtual servers)
│   └── f5_pool.yml            Gestion des pools et membres (add/remove/enable/disable)
├── compliance/
│   ├── config_drift.yml       Détecte les dérives de config vs golden config
│   └── security_baseline.yml  Vérifie la conformité sécurité réseau (NTP, AAA, SSH v2…)
├── inventory/
│   ├── hosts.yml              Inventaire d'exemple (à adapter)
│   └── README.md              Guide de configuration de l'inventaire
├── group_vars/
│   ├── cisco_ios.yml          Variables de connexion Cisco IOS
│   ├── juniper.yml            Variables de connexion Juniper
│   ├── arista.yml             Variables de connexion Arista
│   └── f5.yml                 Variables de connexion F5
└── README.md                  Ce fichier
```

## Prérequis

- **Execution Environment** : `ee-network:1.0.0` enregistré dans AWX
- **Credentials AWX** : `Network Device Credential` (type `Network`) par groupe de devices
- **Inventory AWX** : inventory réseau avec les bons groupes (`cisco_ios`, `juniper`, `arista`, `f5`)

## Utilisation rapide

### Dans AWX
1. Créer un projet AWX pointant sur ce repo Gitea
2. Créer un job template avec l'EE `ee-network`
3. Sélectionner le playbook désiré
4. Associer le bon credential réseau (SSH ou API selon vendeur)

### En local (test)
```bash
# Cisco IOS — collecte des facts
ansible-playbook cisco/ios_facts.yml -i inventory/hosts.yml \
  -e "ansible_user=admin ansible_password=secret"

# Compliance — drift de config
ansible-playbook compliance/config_drift.yml -i inventory/hosts.yml \
  -e "golden_config_dir=/path/to/golden"
```

## Variables d'extra_vars AWX recommandées

| Variable | Description | Exemple |
|----------|-------------|---------|
| `target_hosts` | Groupe ou host cible | `cisco_ios` |
| `backup_dir` | Répertoire de sauvegarde | `/tmp/backups` |
| `vlan_id` | ID du VLAN (ios_vlan.yml) | `100` |
| `vlan_name` | Nom du VLAN | `PROD_SERVERS` |
| `golden_config_dir` | Chemin golden configs | `/configs/golden` |

## Vendeurs supportés

| Vendeur | Collection | Protocole | Notes |
|---------|-----------|-----------|-------|
| Cisco IOS/IOS-XE | `cisco.ios` | SSH | Testé IOS 15.x, IOS-XE 17.x |
| Cisco NX-OS | `cisco.nxos` | SSH/NX-API | Nexus 9k, 7k |
| Cisco ASA | `cisco.asa` | SSH | ASA 9.x |
| Cisco IOS-XR | `cisco.iosxr` | SSH/NETCONF | ASR, NCS |
| Juniper Junos | `junipernetworks.junos` | NETCONF/SSH | EX, QFX, MX, SRX |
| Arista EOS | `arista.eos` | SSH/eAPI | EOS 4.x |
| F5 BIG-IP | `f5networks.f5_modules` | REST API | TMOS 15.x+ |
