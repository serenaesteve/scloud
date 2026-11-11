"""Servidor de sockets para los equipos (el cliente de escritorio).

Un hilo «aceptador» espera conexiones en el puerto. Por cada equipo que se
conecta arranca un hilo propio que atiende sus órdenes de una en una hasta
que se va. Un equipo que transfiere 4 archivos a la vez abre 4 conexiones,
así que tiene 4 hilos aquí.

    aceptador ──accept()──► hilo equipo-1  (subir Fotos/a.jpg …)
              ──accept()──► hilo equipo-2  (bajar Notas/b.md …)
              ──accept()──► hilo equipo-3  (esperar: dormido en la Condition)

El saludo TLS se hace en el hilo del equipo y no en el aceptador, para que
un cliente lento al saludar no deje a los demás sin entrar.
"""

import hashlib
import itertools
import socket
import threading
import time

from . import tls
from .almacen import Cancelada, HuellaIncorrecta
from .nube import ErrorNube, NoEncontrado, partes_ruta
from .protocolo import VERSION, Canal, ErrorProtocolo

TROZO = 256 * 1024
ESPERA_MAXIMA = 55  # segundos que un «esperar» puede quedarse dormido
SIN_ACTIVIDAD = 120  # se corta la conexión de un equipo callado


class ServidorEquipos:
    def __init__(self, nube, host="127.0.0.1", puerto=8105, carpeta_tls=None):
        self.nube = nube
        self.host, self.puerto = host, puerto
        self.contexto = None
        self.huella = None
        if carpeta_tls:
            cert, clave = tls.asegurar_certificado(carpeta_tls)
            self.contexto = tls.contexto_servidor(cert, clave)
            self.huella = tls.huella_pem(cert)
        self._parar = threading.Event()
        self._cerrojo = threading.Lock()
        self._conexiones = {}  # id -> dict con lo que hace cada una (para la sala)
        self._ids = itertools.count(1)
        self._escucha = None
        self._hilo = None
        self.atendidas = 0

    def arrancar(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.puerto))
        s.listen(64)
        s.settimeout(0.5)  # para mirar de vez en cuando si hay que parar
        self.puerto = s.getsockname()[1]
        self._escucha = s
        self._hilo = threading.Thread(target=self._aceptar, name="aceptador-equipos", daemon=True)
        self._hilo.start()
        return self

    def parar(self):
        self._parar.set()
        self.nube.avisos.despertar_todos()
        if self._escucha:
            self._escucha.close()
        with self._cerrojo:
            socks = [c["sock"] for c in self._conexiones.values()]
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._hilo:
            self._hilo.join(timeout=3)

    def _aceptar(self):
        while not self._parar.is_set():
            try:
                sock, direccion = self._escucha.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            id_ = next(self._ids)
            hilo = threading.Thread(target=self._atender, args=(id_, sock, direccion),
                                    name=f"equipo-{id_}", daemon=True)
            hilo.start()

    def conexiones(self):
        """Lo que hace ahora cada conexión (para la sala de máquinas)."""
        ahora = time.time()
        with self._cerrojo:
            return [{"id": i, "direccion": c["direccion"], "usuario": c["usuario"], "equipo": c["equipo"],
                     "orden": c["orden"], "desde": round(ahora - c["desde"]), "hilo": c["hilo"]}
                    for i, c in self._conexiones.items()]

    def _atender(self, id_, sock, direccion):
        info = {"sock": sock, "direccion": f"{direccion[0]}:{direccion[1]}", "usuario": None, "equipo": None,
                "orden": "saludo", "desde": time.time(), "hilo": threading.current_thread().name}
        with self._cerrojo:
            self._conexiones[id_] = info
            self.atendidas += 1
        canal = None
        try:
            sock.settimeout(SIN_ACTIVIDAD)
            if self.contexto:
                sock = self.contexto.wrap_socket(sock, server_side=True)
                info["sock"] = sock
            canal = Canal(sock)
            Sesion(self, canal, info, direccion[0]).bucle()
        except (ConnectionError, socket.timeout, OSError, ErrorProtocolo):
            pass
        finally:
            if canal:
                canal.cerrar()
            else:
                sock.close()
            with self._cerrojo:
                self._conexiones.pop(id_, None)


class Sesion:
    """Las órdenes de una conexión. Vive en el hilo de esa conexión."""

    def __init__(self, servidor, canal, info, ip):
        self.servidor = servidor
        self.nube = servidor.nube
        self.canal = canal
        self.info = info
        self.ip = ip
        self.usuario = None
        self.equipo = None

    def bucle(self):
        while not self.servidor._parar.is_set():
            mensaje = self.canal.recibir()
            orden = str(mensaje.get("orden", ""))
            self.info["orden"], self.info["desde"] = orden, time.time()
            if orden == "adios":
                self.canal.enviar(ok=True)
                return
            metodo = getattr(self, f"o_{orden}", None)
            if metodo is None:
                self.canal.enviar(ok=False, error=f"orden desconocida: {orden}", codigo=400)
                continue
            if self.usuario is None and orden not in ("hola", "entrar"):
                self.canal.enviar(ok=False, error="primero hay que saludar (hola)", codigo=401)
                continue
            try:
                metodo(mensaje)
            except ErrorNube as e:
                self.canal.enviar(ok=False, error=str(e), codigo=e.estado)
            except (ValueError, TypeError, KeyError) as e:
                self.canal.enviar(ok=False, error=f"orden mal formada: {e}", codigo=400)
            self.info["orden"] = "en espera"

    # ------------------------------------------------------------ entrar

    def _dentro(self, equipo, usuario):
        self.equipo, self.usuario = equipo, usuario
        self.info["usuario"], self.info["equipo"] = usuario["nombre"], equipo["nombre"]
        self.nube.equipo_visto(equipo["id"])

    def o_hola(self, m):
        if int(m.get("version", 0)) != VERSION:
            self.canal.enviar(ok=False, error=f"este servidor habla SCL/{VERSION}", codigo=426)
            return
        equipo, usuario = self.nube.equipo_por_token(m.get("token"))
        self._dentro(equipo, usuario)
        self.canal.enviar(ok=True, usuario=usuario["nombre"], visible=usuario["visible"], equipo=equipo["nombre"],
                          gen=self.nube.avisos.generacion(usuario["id"]))

    def o_entrar(self, m):
        """Usuario y contraseña → un token nuevo para este equipo. La contraseña no se guarda en el equipo."""
        usuario = self.nube.entrar(m.get("usuario", ""), m.get("clave", ""), origen=self.ip)
        token, equipo = self.nube.crear_equipo(usuario["id"], m.get("equipo", ""))
        self._dentro(self.nube.equipo_por_token(token)[0], usuario)
        self.canal.enviar(ok=True, token=token, usuario=usuario["nombre"], visible=usuario["visible"],
                          equipo=equipo["nombre"])

    # ------------------------------------------------------------- mirar

    def o_espacio(self, m):
        self.canal.enviar(ok=True, **self.nube.espacio(self.usuario["id"]))

    def _carpeta_id(self, ruta):
        n = self.nube.resolver(self.usuario["id"], ruta)
        if n is not None and n["tipo"] != "carpeta":
            raise ErrorNube(f"«{ruta}» no es una carpeta.")
        return n["id"] if n else 0

    def o_listar(self, m):
        datos = self.nube.listar(self.usuario["id"], self._carpeta_id(m.get("ruta", "/")))
        self.canal.enviar(ok=True, hijos=datos["hijos"])

    def o_arbol(self, m):
        uid = self.usuario["id"]
        gen = self.nube.avisos.generacion(uid)  # antes de leer: si cambia mientras, se vuelve a pedir
        entradas = self.nube.arbol(uid, self._carpeta_id(m.get("ruta", "/")))
        for e in entradas:
            e.pop("id", None)
        if m.get("sincroniza"):
            self.nube.equipo_visto(self.equipo["id"], str(m.get("ruta", "/"))[:200])
        self.canal.enviar(ok=True, entradas=entradas, gen=gen)

    def o_esperar(self, m):
        """Long polling: se duerme hasta que cambie algo en la nube de este usuario."""
        segundos = min(float(m.get("segundos", 30)), ESPERA_MAXIMA)
        gen = self.nube.avisos.esperar(self.usuario["id"], int(m.get("gen", 0)), segundos, self.servidor._parar)
        self.nube.equipo_visto(self.equipo["id"])
        self.canal.enviar(ok=True, gen=gen)

    # ----------------------------------------------------------- cambiar

    def _padre_y_nombre(self, ruta, crear=True):
        partes = partes_ruta(ruta)
        if not partes:
            raise ErrorNube("Falta la ruta.")
        uid = self.usuario["id"]
        if crear:
            padre = self.nube.asegurar_carpetas(uid, 0, partes[:-1])
        else:
            padre = self._carpeta_id("/".join(partes[:-1]))
        return padre, partes[-1]

    def o_carpeta(self, m):
        padre, nombre = self._padre_y_nombre(m["ruta"])
        self.nube.crear_carpeta(self.usuario["id"], padre, nombre, existe_ok=True)
        self.canal.enviar(ok=True)

    def o_borrar(self, m):
        n = self.nube.resolver(self.usuario["id"], m["ruta"])
        if n is None:
            raise ErrorNube("La raíz no se puede borrar.")
        if m.get("huella") and n["huella"] != m["huella"]:
            raise ErrorNube("Cambió en la nube desde la última vez: no se borra.", 409)
        self.nube.a_papelera(self.usuario["id"], n["id"])
        self.canal.enviar(ok=True)

    def o_mover(self, m):
        uid = self.usuario["id"]
        n = self.nube.resolver(uid, m["de"])
        if n is None:
            raise ErrorNube("La raíz no se puede mover.")
        padre, nombre = self._padre_y_nombre(m["a"])
        if n["padre_id"] != padre:
            self.nube.mover(uid, n["id"], padre)
        if n["nombre"] != nombre:
            self.nube.renombrar(uid, n["id"], nombre)
        self.canal.enviar(ok=True)

    # ------------------------------------------------------ transferir

    def o_subir(self, m):
        uid = self.usuario["id"]
        ruta, tam, huella = str(m["ruta"]), int(m["tam"]), str(m["huella"]).lower()
        if tam < 0:
            raise ErrorNube("Tamaño no válido.")
        base = m.get("base")
        padre, nombre = self._padre_y_nombre(ruta)
        prep = self.nube.preparar_subida(uid, padre, nombre, tam, base=base)

        if self.nube.tiene_bloque(prep["propietario"], huella):
            # Ese contenido ya está en tu nube: no hace falta mandarlo otra vez
            fila = self.nube.registrar_archivo(uid, padre, nombre, huella, tam, m.get("mod"), base=base)
            self.canal.enviar(ok=True, instantanea=True, version=fila["version"])
            return

        clave = hashlib.sha256(f"{uid}:{ruta}:{huella}:{tam}".encode()).hexdigest()[:40]
        desde = self.nube.almacen.recibido(clave)
        if desde > tam:
            desde = 0
        reg = self.nube.transferencias
        inicio = time.time()
        try:
            with reg.turno(uid, nombre, "subida", "equipo", tam, origen=self.equipo["nombre"]) as t:
                self.info["orden"] = f"subir {nombre}"
                reg.avanzar(t, desde)
                self.canal.enviar(ok=True, desde=desde)  # ya hay turno: que empiece a mandar
                self.nube.almacen.continuar(clave, desde, self.canal.leer, tam, huella,
                                            avance=lambda n: reg.avanzar(t, n), cancelada=t.cancelar.is_set)
        except HuellaIncorrecta as e:
            self.canal.enviar(ok=False, error=str(e), codigo=422)
            return
        except Cancelada:
            # Si se canceló a mitad, quedan bytes sin leer en el socket y la
            # conversación ya no tiene arreglo: se avisa y se corta.
            self.canal.enviar(ok=False, error="cancelada desde la web", codigo=499)
            raise ConnectionError("subida cancelada")
        fila = self.nube.registrar_archivo(uid, padre, nombre, huella, tam, m.get("mod"), base=base)
        self.nube.apuntar(uid, "subida", "equipo", nombre, tam - desde, (time.time() - inicio) * 1000)
        self.canal.enviar(ok=True, version=fila["version"], reanudada=desde > 0)

    def o_bajar(self, m):
        uid = self.usuario["id"]
        n = self.nube.resolver(uid, m["ruta"])
        if n is None or n["tipo"] != "archivo":
            raise NoEncontrado("Eso no es un archivo.")
        desde = max(0, min(int(m.get("desde", 0)), n["tamano"]))
        reg = self.nube.transferencias
        inicio = time.time()
        with reg.turno(uid, n["nombre"], "bajada", "equipo", n["tamano"], origen=self.equipo["nombre"]) as t:
            self.info["orden"] = f"bajar {n['nombre']}"
            reg.avanzar(t, desde)
            self.canal.enviar(ok=True, tam=n["tamano"], huella=n["huella"], desde=desde, mod=n["modificado"])
            with self.nube.almacen.abrir(n["huella"]) as f:
                f.seek(desde)
                while trozo := f.read(TROZO):
                    if t.cancelar.is_set():
                        raise ConnectionError("bajada cancelada")  # el cliente verá el corte y la huella no cuadrará
                    self.canal.enviar_bytes(trozo)
                    reg.avanzar(t, len(trozo))
        self.nube.apuntar(uid, "bajada", "equipo", n["nombre"], n["tamano"] - desde, (time.time() - inicio) * 1000)
