#!/bin/bash
# Test domain controller for the directory login (E27 S6): provisions the
# AIBOX.TEST domain on the first start, adds the test accounts, then runs
# samba in the foreground. Development and test only.
#
#   OU=AIBox    AIBox-Admins, AIBox-Analysts, Team-A (Team-A is a member of
#               AIBox-Analysts: nested membership)
#   OU=Service  svc-aibox      the read-only lookup account
#   OU=People   alice          in Team-A -> AIBox-Analysts (nested)
#               bob            in AIBox-Admins
#               carol          in AIBox-Analysts, disabled
#               dave           in no group
#
# Passwords come from the environment: SAMBA_ADMIN_PASSWORD (Administrator),
# SAMBA_SERVICE_PASSWORD (svc-aibox) and SAMBA_USER_PASSWORD (the people).
set -euo pipefail

REALM="${SAMBA_REALM:-AIBOX.TEST}"
DOMAIN="${SAMBA_DOMAIN:-AIBOX}"
TARGET=/var/lib/samba/aibox
CONF="$TARGET/etc/smb.conf"

for name in SAMBA_ADMIN_PASSWORD SAMBA_SERVICE_PASSWORD SAMBA_USER_PASSWORD; do
    if [ -z "${!name:-}" ]; then
        echo "samba-ad: $name is required" >&2
        exit 2
    fi
done

if [ ! -f "$CONF" ]; then
    echo "samba-ad: provisioning $REALM" >&2
    # xattr_tdb keeps the NT ACLs in a database instead of security.* extended
    # attributes, so the container needs no CAP_SYS_ADMIN.
    samba-tool domain provision --targetdir="$TARGET" --realm="$REALM" --domain="$DOMAIN" \
        --server-role=dc --dns-backend=SAMBA_INTERNAL --host-name=dc01 \
        --adminpass="$SAMBA_ADMIN_PASSWORD" \
        --option="vfs objects = dfs_samba4 acl_xattr xattr_tdb" \
        --option="dns forwarder = 127.0.0.1" >/dev/null
fi

st() { samba-tool "$@" -s "$CONF"; }

if [ ! -f "$TARGET/.seeded" ]; then
    echo "samba-ad: adding the test accounts" >&2
    BASE="DC=$(echo "$REALM" | tr '[:upper:]' '[:lower:]' | sed 's/\./,DC=/g')"
    st domain passwordsettings set --max-pwd-age=0 >/dev/null
    for ou in AIBox Service People; do
        st ou add "OU=$ou" >/dev/null
    done
    for group in AIBox-Admins AIBox-Analysts Team-A; do
        st group add "$group" --groupou="OU=AIBox" >/dev/null
    done
    st group addmembers AIBox-Analysts Team-A >/dev/null
    st user add svc-aibox "$SAMBA_SERVICE_PASSWORD" --userou="OU=Service" \
        --description="AI Box read-only directory lookup" >/dev/null
    st user add alice "$SAMBA_USER_PASSWORD" --userou="OU=People" --given-name="Alíz" --surname="Kovács" >/dev/null
    st user add bob "$SAMBA_USER_PASSWORD" --userou="OU=People" --given-name="Bob" --surname="Admin" >/dev/null
    st user add carol "$SAMBA_USER_PASSWORD" --userou="OU=People" --given-name="Carol" --surname="Disabled" >/dev/null
    st user add dave "$SAMBA_USER_PASSWORD" --userou="OU=People" --given-name="Dave" --surname="Nogroup" >/dev/null
    st group addmembers Team-A alice >/dev/null
    st group addmembers AIBox-Admins bob >/dev/null
    st group addmembers AIBox-Analysts carol >/dev/null
    st user disable carol >/dev/null
    echo "$BASE" > "$TARGET/.seeded"
fi

echo "samba-ad: base $(cat "$TARGET/.seeded"), starting samba" >&2
exec samba --configfile="$CONF" --foreground --no-process-group --debug-stdout
