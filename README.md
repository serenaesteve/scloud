# scloud

Una nube privada para guardar, compartir y sincronizar archivos entre dispositivos. Está publicada en **https://scloud.serenaesteve.com**.

- **Web** (Flask): subir y bajar arrastrando archivos o carpetas enteras, varias a la vez y con una gota que se llena según avanzan. También carpetas, vista previa, buscar, mover, papelera de 30 días, compartir con otras cuentas («ver» o «editar»), enlaces de descarga con contraseña, caducidad y límite de descargas, y buzones para que alguien sin cuenta te mande archivos.
- **Equipos** (cliente de escritorio por sockets): `subir` y `bajar` con varios hilos y reanudación si se corta, y `sincronizar`, que mantiene una carpeta igual en el ordenador y en la nube, con conflictos resueltos sin perder nada.
- **En directo**: lo que sube un equipo, otra pestaña o alguien desde un buzón aparece al momento en la web (Server-Sent Events).

Python 3.11 o más nuevo. El servidor usa Flask; el cliente, solo la biblioteca estándar. Los datos van en [SBD](sbd/LEEME-SBD.md), el motor de bases de datos propio con lenguaje en español, que va copiado dentro del proyecto.

```bash
python3 -m scloud servir                       # web en http://127.0.0.1:8104 y equipos en el 8105 (TLS)
python3 -m unittest discover -s pruebas -t .   # 39 pruebas
/root/life-dashboard/.venv/bin/python pruebas/navegador.py [--capturas DIR]   # recorrido con Chromium
python3 experimentos/latencia.py               # ¿compensan los hilos? (con latencia simulada)
```

## Cliente de escritorio

En la web, **Equipos → Conectar un equipo** da el cliente (un ZIP) y la orden para conectarlo:

```bash
python3 -m scloud conectar scl_… --servidor scloud.serenaesteve.com --puerto 8105 --huella AB:55:…
python3 -m scloud ls /Fotos
python3 -m scloud subir ~/Fotos --a / --hilos 4      # carpetas enteras, también las vacías
python3 -m scloud bajar /Fotos --a ~/Descargas
python3 -m scloud sincronizar ~/scloud               # se queda vigilando; Ctrl+C para parar
python3 -m scloud sincronizar ~/Trabajo --remota /Clase --una-vez
```

## Cómo está hecho

```
                   navegador ──HTTPS──► Apache ──► Flask (un hilo por petición) ─┐
                                                                                 ├─► Nube ──► SBD (índice)
 cliente de escritorio ──TLS · SCL/1──► servidor de equipos (un hilo por conexión) ┘    └──► datos/bloques (contenido)
```

| Archivo | Qué hace |
|---|---|
| [scloud/nube.py](scloud/nube.py) | Cuentas, árbol, permisos heredados, papelera, compartir, enlaces, equipos y cuotas |
| [scloud/almacen.py](scloud/almacen.py) | El contenido, guardado por su huella SHA-256 (lo repetido ocupa una vez) y con subidas reanudables |
| [scloud/web.py](scloud/web.py) | La API con Flask: subidas en streaming, descargas con `Range`, ZIP al vuelo, SSE y enlaces públicos |
| [scloud/servidor_equipos.py](scloud/servidor_equipos.py) | El servidor de sockets: un aceptador y un hilo por equipo |
| [scloud/protocolo.py](scloud/protocolo.py) · [PROTOCOLO.md](PROTOCOLO.md) | SCL/1: líneas de JSON y bytes en crudo |
| [scloud/transferencias.py](scloud/transferencias.py) | Lo que está en curso y los semáforos que limitan cuántas van a la vez |
| [scloud/avisos.py](scloud/avisos.py) | Colas para las pestañas y una `Condition` para los equipos que esperan |
| [scloud/cliente.py](scloud/cliente.py) · [scloud/sincro.py](scloud/sincro.py) | El cliente: conexión con huella fijada, lote con varios hilos y sincronización a tres bandas |
| [scloud/tls.py](scloud/tls.py) | El certificado (con `openssl` en un subproceso) y la huella |
| [web/](web/) | La interfaz: HTML, CSS y JavaScript sin framework |

Los criterios de evaluación de 0490 están en [CRITERIOS.md](CRITERIOS.md).

## Qué hay en esta carpeta

| Carpeta | Contenido |
|---|---|
| `scloud/` | El servidor y el cliente (Python) |
| `web/` | La interfaz, la página de los enlaces y la de privacidad |
| `sbd/` | La copia de SBD que usa scloud |
| `pruebas/` | Pruebas automáticas y el recorrido con navegador |
| `experimentos/` | La medida de hilos con latencia |
| `capturas/` | Capturas del recorrido (`pruebas/navegador.py --capturas capturas`) |
| `despliegue/` | Servicio, Apache, copia diaria y el script para publicar |
| `linkedin/` | Las publicaciones 43 y 44 con sus chuletas (copias: las originales siguen en /root/publicaciones-linkedin y /root/guion-examen) |

Fuera de aquí solo está lo que el sistema necesita en su sitio, y todo sale de `despliegue/`: la copia publicada y los datos reales en `/srv/scloud`, la configuración en `/etc` (systemd, Apache y Let's Encrypt) y las copias del índice en `/root/respaldos-scloud`.

## Publicarlo

```bash
sudo bash despliegue/desplegar.sh
```

Crea el usuario `scloud`, copia el código a `/srv/scloud/app`, arranca `scloud.service` (web en 127.0.0.1:8104 tras Apache con Let's Encrypt; equipos en el 8105 abierto a internet), empaqueta el cliente y programa una copia diaria del índice en `/root/respaldos-scloud` (14 días). No toca los datos de `/srv/scloud/datos`. Cada cuenta nueva tiene 2 GB, y scloud deja siempre 20 GB libres en el disco, que es de todos los servicios de la máquina.
