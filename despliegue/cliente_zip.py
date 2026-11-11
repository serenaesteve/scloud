"""Empaqueta el cliente de escritorio en un ZIP para descargarlo desde la web.

    python3 despliegue/cliente_zip.py web/descargas/scloud-cliente.zip

El cliente es el mismo paquete «scloud»: las órdenes de equipo (conectar,
subir, bajar, sincronizar) solo usan la biblioteca estándar de Python.
"""

import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

LEEME = """scloud · cliente de escritorio
==============================

Necesita Python 3.11 o más nuevo y nada más.

1. En la web de scloud, ve a Equipos → Conectar un equipo y copia la orden.
2. Abre una terminal en esta carpeta (scloud-cliente) y pégala.

Después:

  python3 -m scloud ls                         lista tu nube
  python3 -m scloud subir ~/Fotos --a /Fotos   sube una carpeta (4 archivos a la vez)
  python3 -m scloud bajar /Fotos --a ~/Abajo   baja una carpeta
  python3 -m scloud sincronizar ~/scloud       mantiene una carpeta igual aquí y en la nube

Si una transferencia se corta, vuelve a lanzarla: sigue desde donde se quedó.
El token del equipo se guarda en ~/.config/scloud/cuenta.json (solo lo puede leer tu usuario).
"""


def main(destino):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scloud-cliente/LEEME.txt", LEEME)
        for ruta in sorted((RAIZ / "scloud").rglob("*")):
            if ruta.is_file() and "__pycache__" not in ruta.parts:
                z.write(ruta, "scloud-cliente/" + ruta.relative_to(RAIZ).as_posix())
    print(f"{destino} ({destino.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
