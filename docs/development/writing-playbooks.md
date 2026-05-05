---
title: Écrire des playbooks
---

# Écrire des playbooks Ansible

Guide des conventions et bonnes pratiques pour les playbooks utilisés dans Autoflow.

---

## Structure d'un dépôt de playbooks

```
network-playbooks/
├── README.md
├── collections/
│   └── requirements.yml        # Collections Galaxy à installer
├── roles/
│   └── requirements.yml        # Rôles Galaxy à installer
├── inventory/
│   ├── production/
│   │   ├── hosts.yml
│   │   └── group_vars/
│   └── staging/
│       └── hosts.yml
├── playbooks/
│   ├── deploy-vlan.yml
│   ├── backup-configs.yml
│   └── check-connectivity.yml
├── templates/
│   └── interface.j2
├── vars/
│   └── common.yml
└── .ansible-lint                # Configuration ansible-lint
```

---

## Conventions de nommage

```yaml
# Bon : nom descriptif avec contexte
- name: Configure VLAN 100 on access switches

# Mauvais : vague
- name: Do the thing
```

### Fichiers et variables

```
# Playbooks : verbe-objet.yml
deploy-vlan.yml
backup-configs.yml
check-connectivity.yml

# Variables : snake_case
vlan_id: 100
interface_name: GigabitEthernet0/1
backup_path: /tmp/backups
```

---

## Structure d'un playbook standard

```yaml
---
# deploy-vlan.yml
- name: Deploy VLAN configuration
  hosts: "{{ target_hosts | default('access_switches') }}"
  gather_facts: false
  become: false

  vars:
    vlan_id: "{{ vlan_id | mandatory }}"
    vlan_name: "{{ vlan_name | default('VLAN_' + vlan_id | string) }}"

  pre_tasks:
    - name: Validate input variables
      ansible.builtin.assert:
        that:
          - vlan_id is defined
          - vlan_id | int > 0
          - vlan_id | int < 4095
        fail_msg: "vlan_id must be between 1 and 4094"

  tasks:
    - name: Configure VLAN
      cisco.ios.ios_vlans:
        config:
          - vlan_id: "{{ vlan_id }}"
            name: "{{ vlan_name }}"
            state: active
        state: merged

  post_tasks:
    - name: Verify VLAN exists
      cisco.ios.ios_vlans:
        state: gathered
      register: vlan_facts

    - name: Assert VLAN is configured
      ansible.builtin.assert:
        that: >
          vlan_facts.gathered | selectattr('vlan_id', 'equalto', vlan_id | int) | list | length > 0
```

---

## Variables extra_vars depuis AWX

AWX peut passer des variables à votre playbook via `extra_vars`. Toujours valider ces variables en entrée :

```yaml
vars:
  # Valeur par défaut sécurisée
  target_environment: "{{ target_environment | default('staging') }}"
  
  # Variable obligatoire (échoue si absente)
  device_ip: "{{ device_ip | mandatory }}"
  
  # Validation de type
  max_retries: "{{ max_retries | default(3) | int }}"

pre_tasks:
  - name: Validate environment
    ansible.builtin.assert:
      that:
        - target_environment in ['staging', 'production']
      fail_msg: "target_environment must be 'staging' or 'production'"
```

---

## Gestion des secrets

Ne jamais stocker de secrets dans les playbooks ou les inventaires. Utiliser les **Credentials AWX** :

```yaml
# Les credentials AWX injectent automatiquement les variables
# Exemple : credential SSH → ansible_user, ansible_ssh_private_key_file
# Credential réseau → ansible_user, ansible_password, ansible_become_password

tasks:
  - name: Connect to device
    cisco.ios.ios_command:
      commands: show version
    # ansible_user et ansible_password sont injectés par AWX
```

Pour les secrets applicatifs, utiliser les **Custom Credentials AWX** :

```yaml
# Dans AWX : Credential Type avec champs injectés en extra_vars ou env_vars
# Le playbook consomme :
tasks:
  - name: Use API key
    ansible.builtin.uri:
      url: "https://api.example.com/v1/data"
      headers:
        Authorization: "Bearer {{ api_key }}"  # injecté par AWX
```

---

## Idempotence

Chaque tâche doit pouvoir être exécutée plusieurs fois sans effet de bord :

```yaml
# Bon : idempotent
- name: Ensure directory exists
  ansible.builtin.file:
    path: /opt/myapp
    state: directory
    mode: '0755'

# Mauvais : non idempotent (crée un doublon à chaque run)
- name: Add line to file
  ansible.builtin.shell: echo "config=value" >> /etc/myapp.conf
```

---

## Tags

```yaml
tasks:
  - name: Install packages
    ansible.builtin.package:
      name: "{{ item }}"
    loop: "{{ packages }}"
    tags: [install, packages]

  - name: Configure service
    ansible.builtin.template:
      src: config.j2
      dest: /etc/service/config
    tags: [configure, config]

  - name: Restart service
    ansible.builtin.service:
      name: myservice
      state: restarted
    tags: [restart, never]  # 'never' = uniquement si tagué explicitement
```

```bash
# Exécuter uniquement l'installation
ansible-playbook deploy.yml --tags install

# Tout sauf restart
ansible-playbook deploy.yml --skip-tags restart
```

---

## Gestion des erreurs

```yaml
tasks:
  - name: Check if service is running
    ansible.builtin.command: systemctl is-active myservice
    register: service_status
    failed_when: false  # Ne pas échouer si le service est absent
    changed_when: false

  - name: Handle failure gracefully
    block:
      - name: Risky operation
        ansible.builtin.command: risky_command
    rescue:
      - name: Rollback
        ansible.builtin.command: rollback_command
    always:
      - name: Clean up temp files
        ansible.builtin.file:
          path: /tmp/tempfile
          state: absent
```

---

## Inventaires pour AWX

```yaml
# inventory/production/hosts.yml
---
all:
  children:
    access_switches:
      hosts:
        sw-floor-1:
          ansible_host: 192.168.1.10
          ansible_network_os: cisco.ios.ios
        sw-floor-2:
          ansible_host: 192.168.1.11
          ansible_network_os: cisco.ios.ios
      vars:
        ansible_connection: network_cli
        ansible_become: true
        ansible_become_method: enable
    
    core_switches:
      hosts:
        sw-core-1:
          ansible_host: 192.168.1.1
```

---

## Lint et tests

```bash
# Linter (avant chaque commit)
ansible-lint playbooks/

# Configuration .ansible-lint
cat > .ansible-lint << 'EOF'
warn_list:
  - yaml[line-length]
skip_list:
  - no-changed-when  # Peut être ignoré pour certains modules réseau
exclude_paths:
  - .cache/
EOF

# Syntaxe check
ansible-playbook --syntax-check playbooks/deploy-vlan.yml -i "localhost,"

# Dry-run
ansible-playbook --check --diff playbooks/deploy-vlan.yml \
  -i inventory/staging/hosts.yml \
  -e "vlan_id=100 vlan_name=TEST"
```

---

## Intégration avec AWX

### Job Template recommandé

| Paramètre | Valeur recommandée |
|---|---|
| **Source Control** | Dépôt Gitea |
| **Branch** | `main` |
| **Credentials** | SSH Key + Network Credential |
| **Execution Environment** | EE approprié |
| **Ask Variables** | ✅ (pour les extra_vars dynamiques) |
| **Concurrent Jobs** | ❌ (désactivé — évite les conflits) |
| **Verbosity** | 1 (Normal) en prod, 3 (Debug) en dev |
| **Timeout** | 600s (10 min) |
