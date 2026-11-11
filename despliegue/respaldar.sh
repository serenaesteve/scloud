#!/bin/bash
# Copia el índice de scloud (la base de SBD, no el contenido de los archivos) a /root/respaldos-scloud
# y borra las de más de 14 días. SBD escribe la foto de forma atómica y el diario línea a línea.
set -euo pipefail
umask 077

DESTINO=/root/respaldos-scloud
install -d -m 700 "$DESTINO"
tar -czf "$DESTINO/scloud-$(date +%Y%m%d-%H%M%S).tar.gz" -C /srv/scloud/datos sbd
find "$DESTINO" -name 'scloud-*.tar.gz' -mtime +14 -delete
