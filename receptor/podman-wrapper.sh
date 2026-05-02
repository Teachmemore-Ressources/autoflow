#!/usr/bin/env bash
# Translate Podman-specific CLI flags to Docker equivalents.
# AWX hardcodes "podman" and passes Podman-only flags that Docker rejects.
#
# Translations applied:
#   --network slirp4netns:*  → --network bridge  (Podman rootless networking)
#   --network=slirp4netns:*  → --network=bridge
#   --annotation key=val     → (dropped, Podman-only OCI annotation)
#   --userns=*               → (dropped, Podman user-namespace flag)

args=()
argv=("$@")
count=$#
i=0

while [ $i -lt $count ]; do
    arg="${argv[$i]}"
    case "$arg" in
        --network)
            i=$((i + 1))
            net="${argv[$i]}"
            if [[ "$net" == slirp4netns* ]]; then
                args+=(--network bridge)
            else
                args+=(--network "$net")
            fi
            ;;
        --network=slirp4netns*)
            args+=(--network=bridge)
            ;;
        --userns=*)
            # Podman user-namespace — no Docker equivalent, drop it
            ;;
        --annotation)
            # Podman OCI annotation — skip key=value pair
            i=$((i + 1))
            ;;
        --annotation=*)
            # Inline form — drop
            ;;
        *)
            args+=("$arg")
            ;;
    esac
    i=$((i + 1))
done

exec /usr/local/bin/docker "${args[@]}"
