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

log "--- Step 5: Registering Gitea Container Registry credential ---"
awx-manage shell -c "
import os, json
from awx.main.models import Credential, CredentialType, Organization

try:
    org  = Organization.objects.get(id=1)
    ct   = CredentialType.objects.get(name='Container Registry')
    host = os.environ.get('GITEA_ROOT_URL', 'http://localhost:3001').replace('http://', '').replace('https://', '').rstrip('/')

    cred, created = Credential.objects.get_or_create(
        name='Gitea Container Registry',
        defaults={
            'description': 'OCI registry Gitea — ' + host,
            'credential_type': ct,
            'organization': org,
            'inputs': {
                'host': host,
                'username': os.environ.get('AWX_ADMIN_USER', 'admin'),
                'password': os.environ.get('GITEA_REGISTRY_TOKEN', ''),
                'verify_ssl': False,
            },
        }
    )
    action = 'created' if created else 'already exists'
    print(f'Container Registry credential \"{cred.name}\" {action} (id={cred.id}).')
except Exception as e:
    print(f'WARNING: could not register Container Registry credential: {e}')
"

log "--- Step 6: Registering Execution Environments ---"
awx-manage shell -c "
import os
from awx.main.models import ExecutionEnvironment, Credential, Organization

try:
    org  = Organization.objects.get(id=1)
    cred = Credential.objects.filter(name='Gitea Container Registry').first()
    host = os.environ.get('GITEA_ROOT_URL', 'http://localhost:3001').replace('http://', '').replace('https://', '').rstrip('/')
    user = os.environ.get('AWX_ADMIN_USER', 'admin')

    ees = [
        {
            'name': 'EE Base',
            'description': 'Collections communes — community.general, ansible.posix, community.crypto',
            'image': f'{host}/{user}/ee-base:1.0.0',
        },
        {
            'name': 'EE Security',
            'description': 'Sécurité et conformité — boto3, openssl, community.crypto',
            'image': f'{host}/{user}/ee-security:1.0.0',
        },
    ]

    for ee_def in ees:
        ee, created = ExecutionEnvironment.objects.get_or_create(
            name=ee_def['name'],
            defaults={
                'description': ee_def['description'],
                'image': ee_def['image'],
                'pull': 'missing',
                'credential': cred,
                'organization': org,
            }
        )
        action = 'created' if created else 'already exists'
        print(f'EE \"{ee.name}\" {action} (id={ee.id}, image={ee.image}).')
except Exception as e:
    print(f'WARNING: could not register Execution Environments: {e}')
"

log "--- AWX initialisation complete ---"
