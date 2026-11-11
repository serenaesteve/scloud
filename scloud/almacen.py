"""Almacén de bloques: el contenido de los archivos, fuera de la base.

Cada archivo se guarda una vez por su huella SHA-256, en
datos/bloques/ab/abcdef…  (dos letras de carpeta para no tener 100 000
ficheros en un mismo directorio). Lo que llega se escribe primero en
datos/temporal y solo se mueve a su sitio cuando la huella cuadra: un
corte a medias nunca deja un bloque roto.

Las subidas por socket se pueden reanudar: el trozo recibido se queda en
datos/temporal/<clave>.parcial y la siguiente vez se sigue desde ahí.
"""

import hashlib
import os
import re
import secrets
import threading
import time
from pathlib import Path

TROZO = 256 * 1024
HUELLA_VALIDA = re.compile(r"^[0-9a-f]{64}$")


class HuellaIncorrecta(Exception):
    pass


class Cancelada(Exception):
    pass


class Almacen:
    def __init__(self, directorio):
        self.raiz = Path(directorio)
        self.bloques = self.raiz / "bloques"
        self.temporal = self.raiz / "temporal"
        self.bloques.mkdir(parents=True, exist_ok=True)
        self.temporal.mkdir(parents=True, exist_ok=True)
        # Dos subidas del mismo bloque a la vez: el os.replace es atómico, así
        # que gana la última y el contenido es el mismo. El cerrojo solo
        # protege el contador de bytes.
        self._cerrojo = threading.Lock()

    def ruta(self, huella):
        if not HUELLA_VALIDA.match(huella or ""):
            raise ValueError("huella no válida")
        return self.bloques / huella[:2] / huella

    def existe(self, huella):
        try:
            return self.ruta(huella).is_file()
        except ValueError:
            return False

    def abrir(self, huella):
        return open(self.ruta(huella), "rb")

    # ------------------------------------------------------------ guardar

    def guardar_flujo(self, leer, tamano, avance=None, cancelada=None, huella_esperada=None):
        """Lee «tamano» bytes con leer(n), calcula la huella y guarda el bloque.

        Devuelve la huella. leer(n) puede devolver menos de n; b"" es que la
        otra parte cortó. avance(n) se llama tras cada trozo.
        """
        tmp = self.temporal / f"{secrets.token_hex(8)}.subida"
        h = hashlib.sha256()
        recibido = 0
        try:
            with open(tmp, "wb") as f:
                while recibido < tamano:
                    if cancelada and cancelada():
                        raise Cancelada()
                    trozo = leer(min(TROZO, tamano - recibido))
                    if not trozo:
                        raise ConnectionError("la conexión se cortó a mitad")
                    f.write(trozo)
                    h.update(trozo)
                    recibido += len(trozo)
                    if avance:
                        avance(len(trozo))
            huella = h.hexdigest()
            if huella_esperada and huella != huella_esperada:
                raise HuellaIncorrecta("lo recibido no coincide con la huella anunciada")
            self._colocar(tmp, huella)
            return huella
        finally:
            tmp.unlink(missing_ok=True)

    def _colocar(self, tmp, huella):
        destino = self.ruta(huella)
        destino.parent.mkdir(exist_ok=True)
        if destino.exists():
            return
        os.replace(tmp, destino)

    # ------------------------------------------------- subidas reanudables

    def parcial(self, clave):
        """El trozo ya recibido de una subida cortada (clave = hash de usuario+ruta+huella)."""
        return self.temporal / f"{clave}.parcial"

    def recibido(self, clave):
        p = self.parcial(clave)
        return p.stat().st_size if p.exists() else 0

    def continuar(self, clave, desde, leer, tamano, huella, avance=None, cancelada=None):
        """Sigue escribiendo la subida «clave» a partir de «desde» y la cierra."""
        p = self.parcial(clave)
        if desde == 0:
            p.unlink(missing_ok=True)
        with open(p, "ab") as f:
            f.truncate(desde)
            f.seek(desde)
            recibido = desde
            while recibido < tamano:
                if cancelada and cancelada():
                    raise Cancelada()
                trozo = leer(min(TROZO, tamano - recibido))
                if not trozo:
                    raise ConnectionError("la conexión se cortó a mitad")
                f.write(trozo)
                recibido += len(trozo)
                if avance:
                    avance(len(trozo))
        # Comprobar la huella del archivo entero (también lo de antes del corte)
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while bloque := f.read(1024 * 1024):
                h.update(bloque)
        if h.hexdigest() != huella:
            p.unlink(missing_ok=True)
            raise HuellaIncorrecta("lo recibido no coincide con la huella anunciada")
        self._colocar(p, huella)
        p.unlink(missing_ok=True)
        return huella

    # ------------------------------------------------------------ limpieza

    def barrer(self, en_uso, edad_temporal=24 * 3600, edad_bloque=3600):
        """Borra los bloques que nadie usa y los temporales viejos. Devuelve (bloques, bytes).

        Un bloque recién colocado puede no estar aún en la base (la subida
        acaba de terminar y el árbol se apunta justo después): por eso solo se
        borran los que llevan más de «edad_bloque» segundos ahí.
        """
        borrados = liberados = 0
        reciente = time.time() - edad_bloque
        for carpeta in self.bloques.iterdir():
            if not carpeta.is_dir():
                continue
            for bloque in carpeta.iterdir():
                if bloque.name not in en_uso:
                    try:
                        info = bloque.stat()
                        if info.st_mtime > reciente:
                            continue
                        tam = info.st_size
                        bloque.unlink()
                        borrados += 1
                        liberados += tam
                    except FileNotFoundError:
                        pass
        limite = time.time() - edad_temporal
        for tmp in self.temporal.iterdir():
            try:
                if tmp.stat().st_mtime < limite:
                    tmp.unlink()
            except FileNotFoundError:
                pass
        return borrados, liberados
