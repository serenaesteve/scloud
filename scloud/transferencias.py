"""Las transferencias en curso, compartidas por todos los hilos.

Cada subida o bajada, venga de la web, de un equipo o de un enlace, se
apunta aquí. Así la web puede enseñar en directo lo que mandan tus equipos
y cualquier pestaña puede cancelar una transferencia de otra.

Para no saturar el disco ni la red, solo pueden ir a la vez
LIMITE_GLOBAL transferencias, y LIMITE_USUARIO por cuenta. Las demás
esperan «en cola» en un semáforo y arrancan solas cuando se libera un hueco.
"""

import itertools
import threading
import time
from contextlib import contextmanager

from .almacen import Cancelada

LIMITE_GLOBAL = 12
LIMITE_USUARIO = 4
# Cada cuánto se avisa del progreso de una transferencia (por transferencia)
INTERVALO_AVISO = 0.2
# Cuánto se quedan las terminadas en la lista
RECUERDO = 120


class Transferencia:
    __slots__ = ("id", "usuario_id", "nombre", "sentido", "canal", "origen", "total", "hecho",
                 "estado", "creada", "inicio", "fin", "error", "cancelar", "_ultimo_aviso")

    def to_dict(self):
        ahora = time.time()
        duracion = (self.fin or ahora) - self.inicio if self.inicio else 0
        velocidad = self.hecho / duracion if duracion > 0.05 else 0
        return {
            "id": self.id, "nombre": self.nombre, "sentido": self.sentido, "canal": self.canal,
            "origen": self.origen, "total": self.total, "hecho": self.hecho, "estado": self.estado,
            "velocidad": round(velocidad), "error": self.error,
        }


class Registro:
    def __init__(self, avisos, limite_global=LIMITE_GLOBAL, limite_usuario=LIMITE_USUARIO):
        self.avisos = avisos
        self._cerrojo = threading.Lock()
        self._todas = {}
        self._ids = itertools.count(1)
        self._global = threading.BoundedSemaphore(limite_global)
        self._por_usuario = {}
        self.limite_usuario = limite_usuario
        self.limite_global = limite_global

    def _semaforo(self, usuario_id):
        with self._cerrojo:
            s = self._por_usuario.get(usuario_id)
            if s is None:
                s = self._por_usuario[usuario_id] = threading.BoundedSemaphore(self.limite_usuario)
            return s

    def _avisar(self, t, forzar=False):
        ahora = time.monotonic()
        if not forzar and ahora - t._ultimo_aviso < INTERVALO_AVISO:
            return
        t._ultimo_aviso = ahora
        self.avisos.enviar([t.usuario_id], "transferencia", t.to_dict())

    @contextmanager
    def turno(self, usuario_id, nombre, sentido, canal, total, origen="", id_cliente=None):
        """Apunta la transferencia, espera su turno y la cierra al salir.

        Dentro del with, t.cancelar (threading.Event) dice si alguien la canceló.
        """
        t = Transferencia()
        t.id = id_cliente if id_cliente else f"s{next(self._ids)}"
        t.usuario_id, t.nombre, t.sentido, t.canal, t.origen = usuario_id, nombre, sentido, canal, origen
        t.total, t.hecho, t.estado = total, 0, "en cola"
        t.creada, t.inicio, t.fin, t.error = time.time(), 0, 0, None
        t.cancelar = threading.Event()
        t._ultimo_aviso = 0
        with self._cerrojo:
            self._todas[t.id] = t
        self._avisar(t, forzar=True)

        propio = self._semaforo(usuario_id)
        cogidos = []
        try:
            # Primero el hueco de la cuenta y luego el global, siempre en este
            # orden: dos hilos que cogieran los semáforos al revés se podrían
            # quedar esperando el uno al otro para siempre.
            for semaforo in (propio, self._global):
                while not semaforo.acquire(timeout=0.5):
                    if t.cancelar.is_set():
                        raise Cancelada()
                cogidos.append(semaforo)
            t.estado, t.inicio = "en curso", time.time()
            self._avisar(t, forzar=True)
            yield t
            t.estado = "hecha"
        except BaseException as e:
            t.estado = "cancelada" if t.cancelar.is_set() else "error"
            t.error = str(e) or e.__class__.__name__
            raise
        finally:
            for semaforo in reversed(cogidos):
                semaforo.release()
            t.fin = time.time()
            self._avisar(t, forzar=True)
            self._olvidar_viejas()

    def avanzar(self, t, n):
        t.hecho += n  # solo lo toca el hilo dueño de la transferencia
        self._avisar(t)

    def cancelar(self, usuario_id, id_):
        with self._cerrojo:
            t = self._todas.get(id_)
        if t is None or t.usuario_id != usuario_id or t.estado not in ("en cola", "en curso"):
            return False
        t.cancelar.set()
        return True

    def de(self, usuario_id):
        with self._cerrojo:
            return [t.to_dict() for t in self._todas.values() if t.usuario_id == usuario_id]

    def resumen(self):
        with self._cerrojo:
            estados = [t.estado for t in self._todas.values()]
        return {"en_curso": estados.count("en curso"), "en_cola": estados.count("en cola"),
                "limite_global": self.limite_global, "limite_usuario": self.limite_usuario}

    def _olvidar_viejas(self):
        limite = time.time() - RECUERDO
        with self._cerrojo:
            for id_ in [i for i, t in self._todas.items() if t.fin and t.fin < limite]:
                del self._todas[id_]

