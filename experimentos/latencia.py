"""¿Cuándo compensa subir con varios hilos? Con red de verdad, es decir, con latencia.

    python3 experimentos/latencia.py [--ms 40] [--archivos 60] [--kb 200]

Arranca un scloud en memoria y, entre el cliente y el servidor, un proxy TCP
que retrasa cada trozo «ms» milisegundos en cada sentido (como una conexión
de casa a un servidor lejano). No toca la red del sistema: el retraso lo pone
el propio proxy, con dos hilos por conexión y una cola con la hora de salida
de cada trozo.

Mide lo que tarda en subir N archivos con 1, 2, 4 y 8 hilos, sin y con retraso.
"""

import argparse
import io
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scloud import cliente  # noqa: E402
from scloud.cliente import Lote  # noqa: E402
from scloud.nube import Nube  # noqa: E402
from scloud.servidor_equipos import ServidorEquipos  # noqa: E402


class ProxyLento:
    """Reenvía TCP de un puerto a otro añadiendo un retraso fijo a cada trozo."""

    def __init__(self, destino, retraso):
        self.destino, self.retraso = destino, retraso
        self.escucha = socket.create_server(("127.0.0.1", 0))
        self.puerto = self.escucha.getsockname()[1]
        threading.Thread(target=self._aceptar, daemon=True, name="proxy").start()

    def _aceptar(self):
        while True:
            try:
                a, _ = self.escucha.accept()
            except OSError:
                return
            b = socket.create_connection(self.destino)
            for s in (a, b):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for origen, fin in ((a, b), (b, a)):
                cola = queue.Queue()
                threading.Thread(target=self._leer, args=(origen, cola), daemon=True).start()
                threading.Thread(target=self._escribir, args=(fin, cola), daemon=True).start()

    def _leer(self, s, cola):
        while True:
            try:
                datos = s.recv(65536)
            except OSError:
                datos = b""
            cola.put((time.monotonic() + self.retraso, datos))
            if not datos:
                return

    def _escribir(self, s, cola):
        while True:
            sale, datos = cola.get()
            espera = sale - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            if not datos:
                try:
                    s.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            try:
                s.sendall(datos)
            except OSError:
                return


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ms", type=float, default=40)
    p.add_argument("--archivos", type=int, default=60)
    p.add_argument("--kb", type=int, default=200)
    a = p.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="scloud-latencia-"))
    nube = Nube(tmp / "datos")
    nube.crear_usuario("ana", "Ana", "contraseña-1")
    srv = ServidorEquipos(nube, "127.0.0.1", 0, carpeta_tls=tmp / "datos" / "tls").arrancar()
    locales = tmp / "locales"
    locales.mkdir()
    for i in range(a.archivos):
        (locales / f"{i}.bin").write_bytes(os.urandom(a.kb * 1024))

    print(f"{a.archivos} archivos de {a.kb} KB, con TLS. Tiempo (mejor de 3):\n")
    print(f"{'hilos':>5} │ {'sin retraso':>12} │ {f'+{a.ms:.0f} ms por sentido':>20}")
    print("──────┼──────────────┼─────────────────────")
    proxy = ProxyLento(("127.0.0.1", srv.puerto), a.ms / 1000)
    cuentas = {}
    for nombre, puerto in (("directo", srv.puerto), ("lento", proxy.puerto)):
        cuentas[nombre], _ = cliente.entrar("127.0.0.1", puerto, "ana", "contraseña-1", nombre, ruta=tmp / f"{nombre}.json")
    tanda = 0
    for hilos in (1, 2, 4, 8):
        fila = []
        for nombre in ("directo", "lento"):
            mejores = []
            for _ in range(3):
                tanda += 1
                lote = Lote(cuentas[nombre], hilos=hilos, pintar=False, salida=io.StringIO())
                for i in range(a.archivos):
                    lote.subir(locales / f"{i}.bin", f"t{tanda}/{i}.bin")
                t = time.perf_counter()
                hechos = lote.ejecutar()
                mejores.append(time.perf_counter() - t)
                assert all(x.estado == "hecha" for x in hechos), [x.error for x in hechos if x.error]
            fila.append(min(mejores))
        print(f"{hilos:>5} │ {fila[0]:>10.2f} s │ {fila[1]:>18.2f} s")
    srv.parar()
    nube.cerrar()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
