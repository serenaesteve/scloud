"""Sincronizar una carpeta del equipo con una carpeta de la nube.

Para saber qué ha cambiado y dónde, se comparan tres versiones de cada
archivo (sincronización a tres bandas, como hace git al fusionar):

    L = lo que hay ahora en el equipo
    N = lo que hay ahora en la nube
    B = lo que había en los dos la última vez que se sincronizó (la base)

    L = N              → nada que hacer
    L = B, N ≠ B       → cambió en la nube: se baja (o se borra aquí)
    N = B, L ≠ B       → cambió aquí: se sube (o se manda a la papelera de la nube)
    L ≠ B, N ≠ B, L ≠ N → cambió en los dos sitios: conflicto. Se guardan las
                          dos: la de aquí se renombra «(conflicto de …)» y se sube.

La base se guarda en .scloud-sincro.json, dentro de la propia carpeta.

En modo vigilar trabajan tres hilos:
  · vigía-nube: con su propia conexión, duerme en «esperar» hasta que el
    servidor avisa de un cambio (lo despierta una Condition allí).
  · vigía-local: mira la carpeta cada pocos segundos (tamaño y fecha).
  · el principal: cuando cualquiera de los dos levanta el Event, hace una ronda.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .cliente import Conexion, Lote, huella_archivo
from .protocolo import ErrorRemoto

ESTADO = ".scloud-sincro.json"
IGNORADOS = {".DS_Store", "Thumbs.db", "desktop.ini", ESTADO}


def ignorado(nombre):
    return nombre in IGNORADOS or nombre.endswith(".scl-parcial") or nombre.startswith("~$") or nombre.startswith(".scloud")


def unir(*partes):
    return "/".join(p.strip("/") for p in partes if p and p.strip("/"))


class Sincronizador:
    def __init__(self, cuenta, local, remota="/", hilos=4, avisar=print, intervalo=2.0, equipo=""):
        self.cuenta = cuenta
        self.local = Path(local).resolve()
        self.remota = "/" + unir(remota)
        self.hilos = hilos
        self.avisar = avisar
        self.intervalo = intervalo
        self.equipo = equipo or os.uname().nodename
        self.parar = threading.Event()
        self.hay_cambios = threading.Event()
        self.local.mkdir(parents=True, exist_ok=True)
        self.ruta_estado = self.local / ESTADO

    # ------------------------------------------------------------- estado

    def _leer_estado(self):
        try:
            d = json.loads(self.ruta_estado.read_text())
            if d.get("remota") == self.remota and d.get("servidor") == f"{self.cuenta.servidor}:{self.cuenta.puerto}":
                return d
        except (OSError, ValueError):
            pass
        return {"archivos": {}, "carpetas": []}

    def _guardar_estado(self, archivos, carpetas):
        tmp = self.ruta_estado.with_suffix(".tmp")
        tmp.write_text(json.dumps({"remota": self.remota, "servidor": f"{self.cuenta.servidor}:{self.cuenta.puerto}",
                                   "archivos": archivos, "carpetas": sorted(carpetas)}, ensure_ascii=False, indent=1))
        os.replace(tmp, self.ruta_estado)

    # ------------------------------------------------------------ mirar

    def _recorrer(self):
        """{ruta: (tamaño, mtime_ns)} de los archivos y el conjunto de carpetas."""
        archivos, carpetas = {}, set()
        for raiz, dirs, ficheros in os.walk(self.local):
            dirs[:] = [d for d in dirs if not ignorado(d)]
            rel = Path(raiz).relative_to(self.local).as_posix()
            rel = "" if rel == "." else rel
            for d in dirs:
                carpetas.add(unir(rel, d))
            for f in ficheros:
                if ignorado(f):
                    continue
                try:
                    st = os.stat(Path(raiz) / f)
                except FileNotFoundError:
                    continue
                archivos[unir(rel, f)] = (st.st_size, st.st_mtime_ns)
        return archivos, carpetas

    def firma(self):
        archivos, carpetas = self._recorrer()
        return hash((frozenset(archivos.items()), frozenset(carpetas)))

    # ------------------------------------------------------------- ronda

    def ronda(self):
        resumen = {"subidos": 0, "bajados": 0, "borrados_aqui": 0, "borrados_nube": 0, "conflictos": 0, "errores": 0}
        estado = self._leer_estado()
        base = estado["archivos"]  # ruta -> {"huella", "tam", "mtime_ns"}
        base_carpetas = set(estado["carpetas"])

        con = Conexion(self.cuenta)
        try:
            con.hola()
            try:
                r = con.orden("arbol", ruta=self.remota, sincroniza=True)
            except ErrorRemoto as e:
                if e.codigo != 404:
                    raise
                con.orden("carpeta", ruta=self.remota)
                r = con.orden("arbol", ruta=self.remota, sincroniza=True)
                if base:
                    # La carpeta de la nube ha desaparecido entera (¿a la papelera?).
                    # Mejor volver a subirlo todo que borrar en el equipo.
                    self.avisar("! la carpeta de la nube no estaba: se vuelve a subir lo de este equipo")
                    base, base_carpetas = {}, set()
            nube = {e["ruta"]: e for e in r["entradas"] if e["tipo"] == "archivo"}
            nube_carpetas = {e["ruta"] for e in r["entradas"] if e["tipo"] == "carpeta"}

            locales, local_carpetas = self._recorrer()
            aqui = {}
            for ruta, (tam, mtime) in locales.items():
                b = base.get(ruta)
                if b and b["tam"] == tam and b["mtime_ns"] == mtime:
                    aqui[ruta] = b["huella"]  # no ha cambiado: no hace falta volver a leerlo
                else:
                    try:
                        aqui[ruta] = huella_archivo(self.local / ruta)
                    except FileNotFoundError:
                        pass

            nueva_base = {}
            lote = Lote(self.cuenta, hilos=self.hilos, pintar=False, salida=_Nulo())
            borrar_aqui, borrar_nube = [], []

            for ruta in sorted(set(aqui) | set(nube) | set(base)):
                l = aqui.get(ruta)
                n = nube[ruta]["huella"] if ruta in nube else None
                b = base[ruta]["huella"] if ruta in base else None
                remota = unir(self.remota, ruta)
                if l == n:
                    if l is not None:
                        nueva_base[ruta] = self._apunte(ruta, l)
                elif l == b:  # cambió en la nube
                    if n is None:
                        borrar_aqui.append(ruta)
                    else:
                        lote.bajar(remota, self.local / ruta, nube[ruta]["tam"])
                elif n == b:  # cambió aquí
                    if l is None:
                        borrar_nube.append((ruta, b))
                    else:
                        lote.subir(self.local / ruta, remota, base=b or "", huella=l)
                else:  # cambió en los dos
                    if l is None:
                        lote.bajar(remota, self.local / ruta, nube[ruta]["tam"])
                    elif n is None:
                        lote.subir(self.local / ruta, remota, base="", huella=l)
                    else:
                        otro = self._nombre_conflicto(ruta)
                        os.replace(self.local / ruta, self.local / otro)
                        self.avisar(f"! conflicto en {ruta}: la versión de este equipo pasa a «{Path(otro).name}»")
                        resumen["conflictos"] += 1
                        lote.subir(self.local / otro, unir(self.remota, otro), base="", huella=l)
                        lote.bajar(remota, self.local / ruta, nube[ruta]["tam"])

            # Carpetas (también las vacías)
            crear_aqui = sorted(c for c in nube_carpetas - local_carpetas if c not in base_carpetas)
            crear_nube = sorted(c for c in local_carpetas - nube_carpetas if c not in base_carpetas)
            quitar_aqui = sorted((c for c in local_carpetas - nube_carpetas if c in base_carpetas), reverse=True)
            quitar_nube = sorted((c for c in nube_carpetas - local_carpetas if c in base_carpetas), reverse=True)
            for c in crear_aqui:
                (self.local / c).mkdir(parents=True, exist_ok=True)
            for c in crear_nube:
                con.orden("carpeta", ruta=unir(self.remota, c))

            for t in lote.ejecutar():
                ruta = Path(t.local).relative_to(self.local).as_posix()
                if t.estado != "hecha":
                    resumen["errores"] += 1
                    self.avisar(f"✗ {ruta}: {t.error}")
                    continue
                nueva_base[ruta] = self._apunte(ruta, t.resultado["huella"])
                if t.sentido == "subida":
                    resumen["subidos"] += 1
                    extra = " (ya estaba en tu nube)" if t.resultado.get("instantanea") else ""
                    self.avisar(f"↑ {ruta}{extra}")
                else:
                    resumen["bajados"] += 1
                    self.avisar(f"↓ {ruta}")

            for ruta in borrar_aqui:
                try:
                    (self.local / ruta).unlink()
                    resumen["borrados_aqui"] += 1
                    self.avisar(f"− {ruta} (se borró en la nube; está en su papelera)")
                except FileNotFoundError:
                    pass
            for ruta, huella in borrar_nube:
                try:
                    con.orden("borrar", ruta=unir(self.remota, ruta), huella=huella)
                    resumen["borrados_nube"] += 1
                    self.avisar(f"− {ruta} (a la papelera de la nube)")
                except ErrorRemoto as e:
                    if e.codigo != 404:
                        resumen["errores"] += 1
                        self.avisar(f"✗ {ruta}: {e}")

            for c in quitar_aqui:
                try:
                    (self.local / c).rmdir()  # solo si ha quedado vacía
                except OSError:
                    pass
            for c in quitar_nube:
                quedan = [r for r in nueva_base if r.startswith(c + "/")]
                if not quedan:
                    try:
                        con.orden("borrar", ruta=unir(self.remota, c))
                    except ErrorRemoto:
                        pass
        finally:
            con.cerrar()

        _, carpetas_finales = self._recorrer()
        self._guardar_estado(nueva_base, carpetas_finales)
        return resumen

    def _apunte(self, ruta, huella):
        st = os.stat(self.local / ruta)
        return {"huella": huella, "tam": st.st_size, "mtime_ns": st.st_mtime_ns}

    def _nombre_conflicto(self, ruta):
        p = Path(ruta)
        marca = datetime.now().strftime("%Y-%m-%d %H.%M")
        nombre = f"{p.stem} (conflicto de {self.equipo} {marca}){p.suffix}"
        return (p.parent / nombre).as_posix() if p.parent != Path(".") else nombre

    # ----------------------------------------------------------- vigilar

    def vigilar(self):
        """Sincroniza ahora y cada vez que algo cambie, aquí o en la nube, hasta Ctrl+C."""
        hilos = [threading.Thread(target=self._vigia_nube, name="vigía-nube", daemon=True),
                 threading.Thread(target=self._vigia_local, name="vigía-local", daemon=True)]
        for h in hilos:
            h.start()
        self.hay_cambios.set()
        try:
            while not self.parar.is_set():
                if not self.hay_cambios.wait(timeout=0.5):
                    continue
                time.sleep(0.4)  # deja que terminen de escribirse los archivos que llegan a ráfagas
                self.hay_cambios.clear()
                try:
                    r = self.ronda()
                    if any(r.values()):
                        self.avisar("· " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in r.items() if v))
                except (OSError, ErrorRemoto) as e:
                    self.avisar(f"✗ no se pudo sincronizar ({e}); se reintenta en 5 s")
                    self.parar.wait(5)
                    self.hay_cambios.set()
        finally:
            self.parar.set()
            for h in hilos:
                h.join(timeout=2)

    def _vigia_nube(self):
        gen = 0
        while not self.parar.is_set():
            try:
                con = Conexion(self.cuenta)
                try:
                    gen = con.hola()["gen"]
                    while not self.parar.is_set():
                        nueva = con.orden("esperar", gen=gen, segundos=25)["gen"]
                        if nueva != gen:
                            gen = nueva
                            self.hay_cambios.set()
                finally:
                    con.canal.cerrar()
            except (OSError, ErrorRemoto):
                self.parar.wait(3)  # servidor caído: se vuelve a intentar

    def _vigia_local(self):
        anterior = self.firma()
        while not self.parar.wait(self.intervalo):
            actual = self.firma()
            if actual != anterior:
                anterior = actual
                self.hay_cambios.set()


class _Nulo:
    def isatty(self):
        return False

    def write(self, *_):
        pass

    def flush(self):
        pass
