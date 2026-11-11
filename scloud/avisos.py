"""Avisos entre hilos: quién tiene que enterarse de un cambio.

Dos formas de esperar, cada una para lo suyo:

* La web abre /api/eventos (Server-Sent Events). Cada pestaña tiene su
  propia cola (queue.Queue): el hilo que hace el cambio mete el aviso y el
  hilo de esa pestaña lo saca y lo manda. Productor y consumidor.

* El cliente de escritorio manda «esperar» por el socket y se queda dormido
  en una variable de condición (threading.Condition) hasta que cambie algo
  en su nube. Cada usuario tiene un número de generación que sube con cada
  cambio: el cliente dice cuál conoce y se despierta cuando hay otro.
"""

import queue
import threading


class Avisos:
    def __init__(self):
        self._cerrojo = threading.Lock()
        self._colas = {}  # usuario_id -> set de colas (una por pestaña)
        self._condicion = threading.Condition()
        self._generacion = {}  # usuario_id -> int

    # ---------------------------------------------------- pestañas (SSE)

    def suscribir(self, usuario_id):
        cola = queue.Queue(maxsize=500)
        with self._cerrojo:
            self._colas.setdefault(usuario_id, set()).add(cola)
        return cola

    def baja(self, usuario_id, cola):
        with self._cerrojo:
            colas = self._colas.get(usuario_id)
            if colas:
                colas.discard(cola)
                if not colas:
                    del self._colas[usuario_id]

    def pestanas(self):
        with self._cerrojo:
            return sum(len(c) for c in self._colas.values())

    def enviar(self, usuarios, tipo, datos):
        with self._cerrojo:
            colas = [c for u in set(usuarios) for c in self._colas.get(u, ())]
        for cola in colas:
            try:
                cola.put_nowait((tipo, datos))
            except queue.Full:
                pass  # una pestaña dormida no frena a las demás

    # --------------------------------------------- equipos (Condition)

    def generacion(self, usuario_id):
        with self._condicion:
            return self._generacion.get(usuario_id, 1)

    def cambio(self, usuarios, carpetas=()):
        """Algo cambió en la nube de estos usuarios: despierta a sus equipos y avisa a sus pestañas."""
        usuarios = set(usuarios)
        with self._condicion:
            for u in usuarios:
                self._generacion[u] = self._generacion.get(u, 1) + 1
            self._condicion.notify_all()
        self.enviar(usuarios, "cambio", {"carpetas": list(dict.fromkeys(carpetas))})  # ids y nombres («compartido») mezclados

    def esperar(self, usuario_id, conocida, segundos, parar=None):
        """Duerme hasta que la generación del usuario no sea «conocida» o pase el tiempo."""
        with self._condicion:
            self._condicion.wait_for(
                lambda: self._generacion.get(usuario_id, 1) != conocida or (parar is not None and parar.is_set()),
                timeout=segundos,
            )
            return self._generacion.get(usuario_id, 1)

    def despertar_todos(self):
        with self._condicion:
            self._condicion.notify_all()
