"""Cliente de escritorio: habla SCL/1 con el servidor por sockets.

Una Conexion es un socket. Un Lote reparte muchas transferencias entre
varios hilos, cada uno con su propia conexión, y dibuja el progreso.

    cola de prioridad (los pequeños primero)
        │
        ├──► hilo 1 ── conexión 1 ──► servidor
        ├──► hilo 2 ── conexión 2 ──► servidor
        └──► hilo 3 ── conexión 3 ──► servidor
                         │
    hilo pintor ◄── progreso (dict + Lock) ──┘
"""

import hashlib
import itertools
import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import tls
from .protocolo import PUERTO, VERSION, Canal, ErrorRemoto

TROZO = 256 * 1024
REINTENTOS = 3


class HuellaDistinta(Exception):
    pass


def huella_archivo(ruta, avance=None):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while bloque := f.read(1024 * 1024):
            h.update(bloque)
            if avance:
                avance(len(bloque))
    return h.hexdigest()


def legible(n):
    for unidad in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unidad == "GB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}".replace(".", ",")
        n /= 1024


# ------------------------------------------------------------------ cuenta

def ruta_config():
    return Path(os.environ.get("SCLOUD_CUENTA") or Path.home() / ".config" / "scloud" / "cuenta.json")


class Cuenta:
    """Dónde está el servidor, con qué token se entra y qué huella TLS se espera."""

    def __init__(self, servidor="127.0.0.1", puerto=PUERTO, token=None, huella=None, tls=True, usuario=None, ruta=None):
        self.servidor, self.puerto, self.token, self.huella, self.tls, self.usuario = servidor, puerto, token, huella, tls, usuario
        self.ruta = Path(ruta) if ruta else ruta_config()

    @classmethod
    def cargar(cls, ruta=None):
        ruta = Path(ruta) if ruta else ruta_config()
        if not ruta.exists():
            raise SystemExit("Este equipo aún no está conectado. Usa: python3 -m scloud entrar --servidor …")
        d = json.loads(ruta.read_text())
        return cls(d["servidor"], d["puerto"], d.get("token"), d.get("huella"), d.get("tls", True), d.get("usuario"), ruta)

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        datos = {"servidor": self.servidor, "puerto": self.puerto, "token": self.token, "huella": self.huella,
                 "tls": self.tls, "usuario": self.usuario}
        # El token abre tu nube: el archivo solo lo puede leer tu usuario
        fd = os.open(self.ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(datos, f, indent=2)


# --------------------------------------------------------------- conexión

class Conexion:
    def __init__(self, cuenta, timeout=70):
        self.cuenta = cuenta
        sock = socket.create_connection((cuenta.servidor, cuenta.puerto), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.huella = None
        if cuenta.tls:
            sock = tls.contexto_cliente().wrap_socket(sock, server_hostname=cuenta.servidor)
            self.huella = tls.huella_conexion(sock)
            if cuenta.huella and self.huella != cuenta.huella:
                sock.close()
                raise ConnectionError(
                    "¡Cuidado! El servidor presenta un certificado distinto del que conocía este equipo.\n"
                    f"  esperaba  {cuenta.huella}\n  recibido  {self.huella}\n"
                    "Puede que alguien se esté haciendo pasar por él. No se ha mandado nada.")
        self.canal = Canal(sock)

    def orden(self, orden, **datos):
        self.canal.enviar(orden=orden, **datos)
        return self.canal.respuesta()

    def hola(self):
        return self.orden("hola", token=self.cuenta.token, version=VERSION)

    def cerrar(self):
        try:
            self.canal.enviar(orden="adios")
        except OSError:
            pass
        self.canal.cerrar()

    # ------------------------------------------------------------ archivos

    def subir(self, local, remota, avance=None, base=None, huella=None, cancelar=None):
        local = Path(local)
        tam = local.stat().st_size
        huella = huella or huella_archivo(local)
        mod = int(local.stat().st_mtime * 1000)
        datos = {"ruta": remota, "tam": tam, "huella": huella, "mod": mod}
        if base is not None:
            datos["base"] = base
        r = self.orden("subir", **datos)
        if r.get("instantanea"):
            if avance:
                avance(tam, total=True)
            return {"huella": huella, "instantanea": True, "version": r["version"]}
        desde = r["desde"]
        if avance and desde:
            avance(desde, total=False)
        with open(local, "rb") as f:
            f.seek(desde)
            enviado = desde
            while enviado < tam:
                if cancelar is not None and cancelar.is_set():
                    raise ConnectionError("cancelada")
                trozo = f.read(min(TROZO, tam - enviado))
                if not trozo:
                    raise IOError(f"{local} se ha acortado mientras se subía")
                self.canal.enviar_bytes(trozo)
                enviado += len(trozo)
                if avance:
                    avance(len(trozo))
        r = self.canal.respuesta()
        return {"huella": huella, "version": r["version"], "reanudada": r.get("reanudada", False)}

    def bajar(self, remota, local, avance=None, cancelar=None):
        """Baja a «local». Lo que llega va a un .scl-parcial; si se corta, la próxima vez sigue desde ahí."""
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        parcial = local.with_name(f".{local.name}.scl-parcial")
        desde = parcial.stat().st_size if parcial.exists() else 0
        r = self.orden("bajar", ruta=remota, desde=desde)
        tam, huella = r["tam"], r["huella"]
        if r["desde"] < desde:
            desde = r["desde"]
        if avance and desde:
            avance(desde, total=False)
        with open(parcial, "ab") as f:
            f.truncate(desde)
            recibido = desde
            while recibido < tam:
                if cancelar is not None and cancelar.is_set():
                    raise ConnectionError("cancelada")
                trozo = self.canal.leer(min(TROZO, tam - recibido))
                if not trozo:
                    raise ConnectionError("el servidor cortó a mitad")
                f.write(trozo)
                recibido += len(trozo)
                if avance:
                    avance(len(trozo))
        if huella_archivo(parcial) != huella:
            parcial.unlink(missing_ok=True)
            raise HuellaDistinta(f"{remota}: lo bajado no coincide con la huella del servidor")
        os.replace(parcial, local)
        os.utime(local, (time.time(), r["mod"] / 1000))
        return {"huella": huella, "tam": tam, "reanudada": desde > 0}


def entrar(servidor, puerto, usuario, clave, equipo, huella=None, usar_tls=True, ruta=None):
    """Primera vez: usuario y contraseña → token del equipo. Devuelve la Cuenta guardada."""
    cuenta = Cuenta(servidor, puerto, None, huella, usar_tls, ruta=ruta)
    con = Conexion(cuenta)
    try:
        r = con.orden("entrar", usuario=usuario, clave=clave, equipo=equipo)
    finally:
        con.cerrar()
    cuenta.token, cuenta.huella, cuenta.usuario = r["token"], con.huella, r["usuario"]
    cuenta.guardar()
    return cuenta, r


def conectar_token(servidor, puerto, token, huella=None, usar_tls=True, ruta=None):
    """Con el token que da la web al «conectar un equipo»."""
    cuenta = Cuenta(servidor, puerto, token, huella, usar_tls, ruta=ruta)
    con = Conexion(cuenta)
    try:
        r = con.hola()
    finally:
        con.cerrar()
    cuenta.huella, cuenta.usuario = con.huella, r["usuario"]
    cuenta.guardar()
    return cuenta, r


# ------------------------------------------------------------------- lote

@dataclass(order=True)
class Trabajo:
    prioridad: int
    orden: int
    sentido: str = field(compare=False)  # "subida" | "bajada"
    local: Path = field(compare=False)
    remota: str = field(compare=False)
    tam: int = field(compare=False, default=0)
    base: str = field(compare=False, default=None)
    huella: str = field(compare=False, default=None)
    hecho: int = field(compare=False, default=0)
    estado: str = field(compare=False, default="en cola")
    error: str = field(compare=False, default=None)
    resultado: dict = field(compare=False, default=None)


class Lote:
    """Muchas transferencias con N hilos, cada uno con su propia conexión.

    La cola es de prioridad: los archivos pequeños salen antes, así que en
    una carpeta con cien notas y un vídeo las notas aparecen enseguida en la
    nube en lugar de esperar al vídeo.
    """

    def __init__(self, cuenta, hilos=4, pintar=True, salida=sys.stderr, cancelar=None):
        self.cuenta = cuenta
        self.hilos = max(1, hilos)
        self.pintar = pintar and salida.isatty()
        self.salida = salida
        self.cola = queue.PriorityQueue()
        self.trabajos = []
        self._orden = itertools.count()
        self._cerrojo = threading.Lock()
        self.cancelar = cancelar or threading.Event()
        self._fin = threading.Event()
        self._lineas = 0
        self.inicio = 0

    def subir(self, local, remota, base=None, huella=None):
        local = Path(local)
        tam = local.stat().st_size
        t = Trabajo(tam, next(self._orden), "subida", local, remota, tam, base, huella)
        self.trabajos.append(t)
        self.cola.put(t)
        return t

    def bajar(self, remota, local, tam=0):
        t = Trabajo(tam, next(self._orden), "bajada", Path(local), remota, tam)
        self.trabajos.append(t)
        self.cola.put(t)
        return t

    def ejecutar(self):
        if not self.trabajos:
            return []
        self.inicio = time.time()
        obreros = [threading.Thread(target=self._obrero, name=f"transferencia-{i + 1}", daemon=True)
                   for i in range(min(self.hilos, len(self.trabajos)))]
        pintor = threading.Thread(target=self._pintor, name="pintor", daemon=True) if self.pintar else None
        for o in obreros:
            o.start()
        if pintor:
            pintor.start()
        try:
            for o in obreros:
                while o.is_alive():
                    o.join(timeout=0.2)  # join con espera corta: así Ctrl+C llega enseguida
        except KeyboardInterrupt:
            self.cancelar.set()
            for o in obreros:
                o.join()
            raise
        finally:
            self._fin.set()
            if pintor:
                pintor.join()
        return self.trabajos

    def _obrero(self):
        con = None
        try:
            while not self.cancelar.is_set():
                try:
                    t = self.cola.get_nowait()
                except queue.Empty:
                    return
                for intento in range(1, REINTENTOS + 1):
                    try:
                        if con is None:
                            con = Conexion(self.cuenta)
                            con.hola()
                        self._hacer(con, t)
                        break
                    except (ConnectionError, OSError) as e:
                        # Se cortó: otra conexión y a seguir desde donde se quedó
                        if con:
                            con.canal.cerrar()
                        con = None
                        if self.cancelar.is_set() or intento == REINTENTOS:
                            self._marcar(t, "error", str(e))
                            break
                        time.sleep(0.5 * intento)
                    except (ErrorRemoto, HuellaDistinta) as e:
                        self._marcar(t, "error", str(e))
                        break
        finally:
            if con:
                con.cerrar()

    def _hacer(self, con, t):
        self._marcar(t, "en curso")
        with self._cerrojo:
            t.hecho = 0

        def avance(n, total=False):
            with self._cerrojo:
                t.hecho = n if total else t.hecho + n

        if t.sentido == "subida":
            t.resultado = con.subir(t.local, t.remota, avance, base=t.base, huella=t.huella, cancelar=self.cancelar)
        else:
            t.resultado = con.bajar(t.remota, t.local, avance, cancelar=self.cancelar)
            t.tam = t.resultado["tam"]
        with self._cerrojo:
            t.hecho = t.tam
        self._marcar(t, "hecha")

    def _marcar(self, t, estado, error=None):
        with self._cerrojo:
            t.estado, t.error = estado, error
        if not self.pintar and estado in ("hecha", "error"):
            signo = "↑" if t.sentido == "subida" else "↓"
            extra = f"  ✗ {error}" if error else ("  (ya estaba)" if (t.resultado or {}).get("instantanea") else "")
            print(f"{signo} {t.remota}  {legible(t.tam)}{extra}", file=self.salida, flush=True)

    # ------------------------------------------------------------ pintar

    def _pintor(self):
        while not self._fin.wait(0.15):
            self._dibujar()
        self._dibujar(final=True)

    def _dibujar(self, final=False):
        with self._cerrojo:
            activos = [(t.sentido, t.remota, t.hecho, t.tam) for t in self.trabajos if t.estado == "en curso"]
            hechos = sum(1 for t in self.trabajos if t.estado in ("hecha", "error"))
            errores = [(t.remota, t.error) for t in self.trabajos if t.estado == "error"]
            total = sum(t.tam for t in self.trabajos)
            llevado = sum(t.hecho for t in self.trabajos)
        segundos = max(time.time() - self.inicio, 0.001)
        lineas = []
        for sentido, remota, hecho, tam in activos:
            lineas.append(f"  {'↑' if sentido == 'subida' else '↓'} {barra(hecho, tam, 18)} {recortar(remota, 40)}")
        lineas.append(f"{barra(llevado, total, 28)} {hechos}/{len(self.trabajos)} archivos · "
                      f"{legible(llevado)} de {legible(total)} · {legible(llevado / segundos)}/s")
        if final:
            lineas = lineas[-1:] + [f"  ✗ {r}: {e}" for r, e in errores]
        texto = ""
        if self._lineas:
            texto += f"\x1b[{self._lineas}F\x1b[J"
        texto += "\n".join(lineas) + "\n"
        self._lineas = len(lineas)
        self.salida.write(texto)
        self.salida.flush()


def barra(hecho, total, ancho):
    parte = hecho / total if total else 1
    llenas = int(parte * ancho)
    return "▕" + "█" * llenas + "░" * (ancho - llenas) + f"▏{parte * 100:3.0f} %"


def recortar(texto, n):
    return texto if len(texto) <= n else "…" + texto[-(n - 1):]
