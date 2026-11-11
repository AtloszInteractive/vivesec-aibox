# Samba Active Directory domain controller for testing the directory login
# (E27 S6). Development and test only: never part of a box installation.
FROM debian:bookworm-slim
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        samba samba-ad-dc samba-ad-provision samba-dsdb-modules samba-vfs-modules \
        winbind krb5-user ldb-tools openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/* /etc/samba/smb.conf
COPY entrypoint.sh /usr/local/bin/samba-ad-entrypoint
# A Windows checkout may carry CRLF line ends; bash needs LF.
RUN sed -i 's/\r$//' /usr/local/bin/samba-ad-entrypoint && chmod 0755 /usr/local/bin/samba-ad-entrypoint
LABEL vivesec.component=samba-ad-test
VOLUME /var/lib/samba/aibox
EXPOSE 389 636
ENTRYPOINT ["/usr/local/bin/samba-ad-entrypoint"]
