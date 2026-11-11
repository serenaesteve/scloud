#!/bin/bash
# Publica (o actualiza) scloud en scloud.serenaesteve.com. Se puede lanzar las veces que haga falta:
# no toca los datos. Las subidas que estén a medias en ese momento se cortan (los equipos reanudan solos).
#   sudo bash despliegue/desplegar.sh
set -euo pipefail

DOMINIO=scloud.serenaesteve.com
IP_SERVIDOR=82.165.244.40
ORIGEN=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DIR=$ORIGEN/despliegue

# 1. Usuario propio, sin shell ni acceso a los datos de los demás servicios
id scloud >/dev/null 2>&1 || useradd --system --home-dir /srv/scloud --shell /usr/sbin/nologin scloud
install -d -o root -g scloud -m 750 /srv/scloud
install -d -o root -g scloud -m 750 /srv/scloud/app
install -d -o scloud -g scloud -m 700 /srv/scloud/datos

# 2. El código: de root y solo lectura para el servicio
rsync -a --delete --chown=root:scloud --chmod=D750,F640 --exclude __pycache__ \
  "$ORIGEN/scloud" "$ORIGEN/sbd" "$ORIGEN/web" /srv/scloud/app/
# El cliente de escritorio, para descargarlo desde «Conectar un equipo»
python3 "$DIR/cliente_zip.py" /srv/scloud/app/web/descargas/scloud-cliente.zip
chown -R root:scloud /srv/scloud/app/web/descargas
chmod 750 /srv/scloud/app/web/descargas
chmod 640 /srv/scloud/app/web/descargas/scloud-cliente.zip

# 3. El servicio
install -m 644 "$DIR/scloud.service" /etc/systemd/system/scloud.service
systemctl daemon-reload
systemctl enable scloud.service >/dev/null 2>&1
systemctl restart scloud.service
for _ in $(seq 40); do curl -s -o /dev/null -m 1 http://127.0.0.1:8104/api/yo && break; sleep 0.3; done
systemctl is-active scloud.service
echo "Servicio: $(curl -s -m 5 http://127.0.0.1:8104/api/yo)"

# 4. Copia diaria del índice (la base de SBD; los bloques no)
install -m 755 "$DIR/respaldar.sh" /usr/local/sbin/scloud-respaldar
install -m 644 "$DIR/scloud-respaldo.service" "$DIR/scloud-respaldo.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now scloud-respaldo.timer >/dev/null 2>&1

# 5. Apache en el puerto 80 (redirección a HTTPS y verificación de Let's Encrypt)
install -d -m 755 /var/www/acme
install -m 644 "$DIR/scloud.conf" /etc/apache2/sites-available/scloud.conf
a2ensite -q scloud.conf
apache2ctl -t 2>&1 | tail -1
systemctl reload apache2

# 6. HTTPS, solo si el DNS público ya apunta aquí (es lo que comprueba Let's Encrypt)
IP_DOMINIO=$(dig +short A "$DOMINIO" @1.1.1.1 | tail -1)
IPV6_DOMINIO=$(dig +short AAAA "$DOMINIO" @1.1.1.1 | tail -1)
if [ "$IP_DOMINIO" != "$IP_SERVIDOR" ] || [ -n "$IPV6_DOMINIO" ]; then
  echo
  echo "FALTA EL DNS: $DOMINIO apunta a ${IP_DOMINIO:-nada}${IPV6_DOMINIO:+ y a la IPv6 $IPV6_DOMINIO}."
  echo "Tiene que tener un registro A con $IP_SERVIDOR y ningún registro AAAA."
  exit 0
fi
if [ ! -e "/etc/letsencrypt/live/$DOMINIO/fullchain.pem" ]; then
  certbot certonly --webroot -w /var/www/acme -d "$DOMINIO" --non-interactive --keep-until-expiring \
    --deploy-hook "systemctl reload apache2"
fi
install -m 644 "$DIR/scloud-ssl.conf" /etc/apache2/sites-available/scloud-ssl.conf
a2ensite -q scloud-ssl.conf
apache2ctl -t 2>&1 | tail -1
systemctl reload apache2
sleep 1
echo "Publicado: https://$DOMINIO → $(curl -s -o /dev/null -m 10 --resolve "$DOMINIO:443:$IP_SERVIDOR" -w '%{http_code}' "https://$DOMINIO/")"
