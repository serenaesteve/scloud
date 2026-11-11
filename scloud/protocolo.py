"""SCL/1: el protocolo de los equipos (cliente de escritorio ↔ servidor).

Va sobre TCP (con TLS). Cada mensaje es una línea de JSON acabada en «\\n».
Si una orden lleva contenido (subir) o lo devuelve (bajar), los bytes van
en crudo justo después de la línea, y la línea dice cuántos son. Así se
puede leer con un readline() y luego un read(n), sin base64 ni trozos.

    → {"orden": "subir", "ruta": "Fotos/a.jpg", "tam": 812345, "huella": "9f…"}
    ← {"ok": true, "desde": 0}                      (o "desde": 524288 si se cortó antes)
    → 812345 bytes
    ← {"ok": true, "version": 1}

Está explicado entero en PROTOCOLO.md.
"""

import json

VERSION = 1
MAX_CABECERA = 1024 * 1024  # un árbol grande cabe; una línea sin fin, no
PUERTO = 8105


class ErrorProtocolo(Exception):
    pass


class ErrorRemoto(Exception):
    """El otro lado contestó {"ok": false}."""

    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.codigo = codigo


class Canal:
    """Un socket con lectura por líneas y por bloques de bytes."""

    def __init__(self, sock):
        self.sock = sock
        self._lector = sock.makefile("rb", buffering=256 * 1024)
        self.enviados = 0
        self.recibidos = 0

    def enviar(self, **cabecera):
        linea = json.dumps(cabecera, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        self.sock.sendall(linea)
        self.enviados += len(linea)

    def enviar_bytes(self, datos):
        self.sock.sendall(datos)
        self.enviados += len(datos)

    def recibir(self):
        linea = self._lector.readline(MAX_CABECERA + 1)
        if not linea:
            raise ConnectionError("el otro lado cerró la conexión")
        if len(linea) > MAX_CABECERA or not linea.endswith(b"\n"):
            raise ErrorProtocolo("línea de cabecera demasiado larga")
        self.recibidos += len(linea)
        try:
            mensaje = json.loads(linea)
        except ValueError as e:
            raise ErrorProtocolo(f"cabecera que no es JSON: {e}") from None
        if not isinstance(mensaje, dict):
            raise ErrorProtocolo("la cabecera tiene que ser un objeto JSON")
        return mensaje

    def leer(self, n):
        """Hasta n bytes (al menos 1, salvo que se corte la conexión: entonces b"")."""
        datos = self._lector.read1(n)
        self.recibidos += len(datos)
        return datos

    def respuesta(self):
        """La siguiente línea; si dice ok: false, lanza ErrorRemoto."""
        r = self.recibir()
        if not r.get("ok"):
            raise ErrorRemoto(r.get("error", "error sin explicar"), r.get("codigo", 400))
        return r

    def cerrar(self):
        try:
            self._lector.close()
        finally:
            try:
                self.sock.close()
            except OSError:
                pass
