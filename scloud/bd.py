"""Acceso a SBD, la base de datos con lenguaje propio en español.

SBD funciona dentro de este mismo proceso, sobre una carpeta de datos. En
scloud hay muchos hilos a la vez (los de Flask, uno por cada conexión de
socket y el barrendero), pero SBD ejecuta las órdenes de una en una. Por eso
todo pasa por un único cerrojo reentrante: una transacción lo tiene cogido de
principio a fin y ningún otro hilo ve un cambio a medias.
"""

import threading
import time
from contextlib import contextmanager
from pathlib import Path

import sbd
from sbd import SBDError

BASE = "scloud"
DIR_MIGRACIONES = Path(__file__).with_name("migraciones")


def ahora_ms():
    return int(time.time() * 1000)


def lista(valores):
    """«[?, ?, ?]» para usar con «en»: SBD no acepta una lista en un solo parámetro."""
    return "[" + ", ".join("?" for _ in valores) + "]"


class BaseDatos:
    def __init__(self, directorio):
        self.directorio = Path(directorio).resolve()
        self.directorio.mkdir(parents=True, exist_ok=True)
        self._servidor = sbd.servidor(str(self.directorio))
        if not self._servidor.existe(BASE):
            self._servidor.crear(BASE)
        self._sesion = sbd.Sesion(self._servidor, BASE, etiqueta="scloud")
        self._cerrojo = threading.RLock()
        self._en_transaccion = 0
        self.ordenes = 0
        self.espera_total = 0.0
        self._migrar()

    def ejecutar(self, orden, params=()):
        inicio = time.perf_counter()
        with self._cerrojo:
            self.espera_total += time.perf_counter() - inicio
            self.ordenes += 1
            return self._sesion.ejecutar(orden, list(params))

    def consultar(self, orden, params=()):
        return self.ejecutar(orden, params).dicts()

    def una(self, orden, params=()):
        filas = self.consultar(orden, params)
        return filas[0] if filas else None

    def valor(self, orden, params=()):
        return self.ejecutar(orden, params).scalar()

    @contextmanager
    def transaccion(self):
        """Todo o nada. Anidada dentro de otra, forma parte de la de fuera."""
        with self._cerrojo:
            if self._en_transaccion:
                self._en_transaccion += 1
                try:
                    yield self
                finally:
                    self._en_transaccion -= 1
                return
            self._sesion.ejecutar("empezar")
            self._en_transaccion = 1
            try:
                yield self
            except BaseException:
                self._en_transaccion = 0
                self._sesion.ejecutar("deshacer")
                raise
            self._en_transaccion = 0
            self._sesion.ejecutar("confirmar")

    def version(self):
        tablas = {fila[0] for fila in self.ejecutar("tablas").rows}
        if "ajustes" not in tablas:
            return 0
        valor = self.valor('buscar ajustes donde clave = "version_esquema" mostrar valor')
        return int(valor) if valor else 0

    def _migrar(self):
        actual = self.version()
        rutas = sorted((int(r.name[:3]), r) for r in DIR_MIGRACIONES.glob("[0-9][0-9][0-9]_*.sbd"))
        for numero, ruta in rutas:
            if numero <= actual:
                continue
            with self.transaccion():
                self.ejecutar(ruta.read_text(encoding="utf-8"))
                self.ejecutar('insertar o actualizar en ajustes { clave: "version_esquema", valor: ? }', [str(numero)])

    def ajuste(self, clave, defecto=None):
        valor = self.valor("buscar ajustes donde clave = ? mostrar valor", [clave])
        return defecto if valor is None else valor

    def poner_ajuste(self, clave, valor):
        self.ejecutar("insertar o actualizar en ajustes { clave: ?, valor: ? }", [clave, str(valor)])

    def cerrar(self):
        with self._cerrojo:
            if self._servidor is not None:
                self._sesion.cerrar()
                self._servidor.cerrar()
                self._servidor = None


__all__ = ["BASE", "BaseDatos", "SBDError", "ahora_ms", "lista"]
