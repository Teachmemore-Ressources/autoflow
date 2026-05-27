"""
Patch: inventory_yaml_detection.py
===================================
AWX upstream — could_be_inventory() in awx/main/utils/ansible.py — ignores
all .yml/.yaml files unconditionally (line: `elif '.' in suspected_ext: return None`).

This means inventory plugin files (plugin: community.general.proxmox, etc.)
and static YAML inventories (all:/ungrouped: root key) are never listed in
the "Inventory file" dropdown of "Source from Project" inventory sources.

This patch adds a .yml/.yaml branch before the catch-all extension exclusion,
checking the first 15 lines of the file for:
  - "plugin:"     → inventory plugin file  (e.g. proxmox, aws_ec2, vmware …)
  - "all:" / "ungrouped:"  → static YAML inventory

Tested against AWX 24.6.1.
If the upstream code changes, the assertion below will fail at build time.
"""

import re

FILEPATH = '/var/lib/awx/venv/awx/lib/python3.11/site-packages/awx/main/utils/ansible.py'

OLD = """    elif '.' in suspected_ext:
        # If not using those extensions, inventory must have _no_ extension
        return None"""

NEW = """    elif suspected_ext in ('.yml', '.yaml'):
        # ── Autoflow patch ────────────────────────────────────────────────────
        # AWX upstream excludes all .yml/.yaml files from inventory detection.
        # We extend detection to support the two YAML inventory formats:
        #   1. Inventory plugin files  → first meaningful line starts with "plugin:"
        #   2. Static YAML inventories → root key is "all:" or "ungrouped:"
        # We scan only the first 15 lines for performance (same spirit as the
        # extension-less file check above that uses valid_inventory_re).
        # ─────────────────────────────────────────────────────────────────────
        try:
            with open(inventory_path, encoding='utf-8', errors='ignore') as _inv:
                for _i, _line in enumerate(_inv):
                    if _i > 15:
                        break
                    _s = _line.strip()
                    if _s.startswith('plugin:'):          # inventory plugin
                        return inventory_rel_path
                    if re.match(r'^(all|ungrouped)\\s*:', _s):  # static YAML
                        return inventory_rel_path
        except IOError:
            pass
        return None
    elif '.' in suspected_ext:
        # If not using those extensions, inventory must have _no_ extension
        return None"""

with open(FILEPATH, 'r') as f:
    content = f.read()

assert OLD in content, (
    f"Patch target not found in {FILEPATH} — "
    "AWX version may have changed, review patch manually."
)

patched = content.replace(OLD, NEW, 1)

with open(FILEPATH, 'w') as f:
    f.write(patched)

print(f"[OK] Patch applied to {FILEPATH}")
print("     .yml/.yaml inventory detection enabled (plugin: + all:/ungrouped:)")
