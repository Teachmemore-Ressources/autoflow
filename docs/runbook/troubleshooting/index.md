---
title: Dépannage
---

# Guide de dépannage

Index des problèmes courants par service.

---

## Diagnostic rapide

```bash
# État des containers
docker compose ps

# Dernières erreurs toutes sources
for s in awx_web awx_task traefik gitea event_engine; do
  echo "=== $s ===" 
  docker logs autoflow_$s --tail=5 2>&1 | grep -i "error\|fatal\|critical" || echo "(aucune erreur récente)"
done

# Ressources
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

---

## Index des problèmes

| Symptôme | Guide |
|---|---|
| AWX 502 / jobs qui ne se lancent pas | [Dépannage AWX](awx.md) |
| Gitea inaccessible / push refusé | [Dépannage Gitea](gitea.md) |
| Grafana vide / alertes muettes | [Dépannage Monitoring](monitoring.md) |
| Certificat expiré / HTTPS cassé | [Dépannage Certificats](certificates.md) |
| Service injoignable / DNS cassé | [Dépannage Réseau](network.md) |
| `error setting certificate file` sur git sync AWX | [Certificats — Problème 6b](certificates.md#probleme-6b) |
| Fichiers YAML absents du dropdown "Inventory file" | [AWX — Problème 16](awx.md#probleme-16) |
| Proxmox 403 `SDN.Use` lors du clonage de template | [Proxmox — Problème 1](proxmox.md#probleme-1) |
| Plugin inventaire Proxmox ignoré / 0 hosts | [Proxmox — Problème 4](proxmox.md#probleme-4) |
| `proxmox_api_token_secret` indéfini dans les playbooks | [Proxmox — Problème 5](proxmox.md#probleme-5) |
| Variable indéfinie dans `01_clone_vm.yml` (`vm_cpu_type`, …) | [Proxmox — Problème 6](proxmox.md#probleme-6) |
| Clé SSH rejetée par l'API Proxmox (`invalid urlencoded string`) | [Proxmox — Problème 7](proxmox.md#probleme-7) |
| Timeout démarrage VM (`generating cloud-init ISO`) | [Proxmox — Problème 8](proxmox.md#probleme-8) |
| VM ne démarre pas — `KVM virtualisation not available` | [Proxmox — Problème 9](proxmox.md#probleme-9) |
| `shrinking disks is not supported` sur le resize | [Proxmox — Problème 10](proxmox.md#probleme-10) |
| `kvm=0` ignoré — `False` traité comme truthy par Proxmox | [Proxmox — Problème 9](proxmox.md#probleme-9) |
| Tâche `qmstart` périmée — `failed_when` déclenché dès l'attempt 1 | [Proxmox — Problème 11](proxmox.md#probleme-11) |
| Timeout SSH après démarrage VM (`Timeout when waiting for X:22`) | [Proxmox — Problème 12](proxmox.md#probleme-12) |
| `Permission denied (publickey)` après démarrage VM | [Proxmox — Problème 13](proxmox.md#probleme-13) |
| Architecture gestion clés SSH AWX (génération, rotation, montage EE) | [Proxmox — Problème 13 §Architecture](proxmox.md#ssh-architecture) |
