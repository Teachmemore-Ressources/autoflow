#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AWX one-shot initialisation script
# Runs as a Docker Compose service that must complete before awx_web starts.
# 1. Apply database migrations
# 2. Set up built-in credential types
# 3. Create / update the admin superuser
# 4. Load demo data (organisation, project, inventory, job template)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

log() { echo "[autoflow-init] $*"; }

log "--- Step 1: Applying database migrations ---"
awx-manage migrate --noinput

log "--- Step 2: Setting up managed credential types ---"
awx-manage setup_managed_credential_types

log "--- Step 3: Creating / updating admin superuser ---"
awx-manage shell -c "
import os, sys
from django.contrib.auth.models import User

username = os.environ.get('AWX_ADMIN_USER', 'admin')
password = os.environ.get('AWX_ADMIN_PASSWORD', '')
email    = os.environ.get('AWX_ADMIN_EMAIL', 'admin@autoflow.local')

if not password:
    print('ERROR: AWX_ADMIN_PASSWORD is not set.', file=sys.stderr)
    sys.exit(1)

user, created = User.objects.get_or_create(username=username)
user.set_password(password)
user.is_superuser = True
user.is_staff     = True
user.email        = email
user.save()

action = 'created' if created else 'updated'
print(f'Admin user \"{username}\" {action}.')
"

log "--- Step 4: Loading demo / preload data ---"
awx-manage create_preload_data

log "--- AWX initialisation complete ---"
