"""La nube: cuentas, árbol de carpetas, permisos, papelera, compartir y enlaces.

Todo lo que cambia datos pasa por aquí, la usen la web (Flask), el servidor
de sockets o las pruebas. Cada operación comprueba el permiso de quien la
pide antes de tocar nada y avisa a quien tenga que enterarse.
"""

import hashlib
import hmac
import mimetypes
import re
import secrets
import shutil
import threading
import time
import unicodedata
from pathlib import Path

from .almacen import Almacen
from .avisos import Avisos
from .bd import BaseDatos, ahora_ms, lista
from .transferencias import Registro

GB = 1024 ** 3
CUOTA_DEFECTO = 5 * GB
DIAS_PAPELERA = 30
NIVELES = {"ver": 1, "editar": 2, "propietario": 3}
NOMBRE_USUARIO = re.compile(r"^[a-z0-9._]{3,32}$")


class ErrorNube(Exception):
    estado = 400

    def __init__(self, mensaje, estado=None):
        super().__init__(mensaje)
        if estado:
            self.estado = estado


class NoEncontrado(ErrorNube):
    estado = 404


class SinPermiso(ErrorNube):
    estado = 403


class Conflicto(ErrorNube):
    estado = 409


class SinEspacio(ErrorNube):
    estado = 413


# ----------------------------------------------------------------- utilidades

def hash_clave(clave, sal=None):
    sal = sal or secrets.token_hex(16)
    h = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=2 ** 14, r=8, p=1, dklen=32)
    return h.hex(), sal


def clave_correcta(clave, h, sal):
    return hmac.compare_digest(hash_clave(clave, sal)[0], h)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normalizar_usuario(nombre):
    """«Lucía Pérez» → «lucia.perez»: el mismo cambio al crear la cuenta y al entrar."""
    sin_tildes = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode()
    nombre = re.sub(r"\s+", ".", sin_tildes.strip().lower())
    return re.sub(r"[^a-z0-9._]", "", nombre)


def limpiar_nombre(nombre):
    """El nombre de un archivo o carpeta. Nunca puede salir del árbol: sin «/», sin «..»."""
    nombre = unicodedata.normalize("NFC", (nombre or "").strip())
    nombre = "".join(c for c in nombre if c.isprintable())
    if not nombre or nombre in (".", "..") or "/" in nombre or "\\" in nombre:
        raise ErrorNube("Ese nombre no vale: no puede estar vacío ni llevar «/» o «\\».")
    if len(nombre) > 255:
        raise ErrorNube("El nombre es demasiado largo (máximo 255 caracteres).")
    return nombre


def partes_ruta(ruta):
    """«/Fotos/2025/a.jpg» → ["Fotos", "2025", "a.jpg"], comprobando cada trozo."""
    return [limpiar_nombre(p) for p in (ruta or "").replace("\\", "/").split("/") if p not in ("", ".")]


def adivinar_mime(nombre):
    return mimetypes.guess_type(nombre)[0] or "application/octet-stream"


def nombre_libre(nombre, ocupados):
    if nombre not in ocupados:
        return nombre
    p = Path(nombre)
    base, ext = (p.stem, p.suffix) if p.suffix and p.stem else (nombre, "")
    n = 2
    while f"{base} ({n}){ext}" in ocupados:
        n += 1
    return f"{base} ({n}){ext}"


# ---------------------------------------------------------------------- nube

class Nube:
    def __init__(self, datos, cuota=CUOTA_DEFECTO, registro_abierto=True, reserva_disco=20 * GB):
        self.datos = Path(datos)
        self.datos.mkdir(parents=True, exist_ok=True)
        self.bd = BaseDatos(self.datos / "sbd")
        self.almacen = Almacen(self.datos)
        self.avisos = Avisos()
        self.transferencias = Registro(self.avisos)
        self.cuota = cuota
        self.registro_abierto = registro_abierto
        self.reserva_disco = reserva_disco
        self._intentos = {}
        self._cerrojo_intentos = threading.Lock()

    def cerrar(self):
        self.avisos.despertar_todos()
        self.bd.cerrar()

    # ================================================================ cuentas

    def crear_usuario(self, nombre, visible, clave, rol="usuario", cuota=None):
        nombre = normalizar_usuario(nombre)
        if not NOMBRE_USUARIO.match(nombre):
            raise ErrorNube("El usuario necesita entre 3 y 32 letras o números (sin tildes ni espacios).")
        if len(clave or "") < 8:
            raise ErrorNube("La contraseña necesita al menos 8 caracteres.")
        visible = (visible or "").strip()[:60] or nombre
        h, sal = hash_clave(clave)
        with self.bd.transaccion():
            if self.bd.valor("buscar usuarios donde nombre = ? mostrar id", [nombre]):
                raise Conflicto("Ese usuario ya existe. Prueba con otro.")
            fila = self.bd.una(
                "insertar en usuarios { nombre: ?, visible: ?, rol: ?, clave_hash: ?, clave_sal: ?, cuota: ?, creado: ? } devolver id",
                [nombre, visible, rol, h, sal, cuota or self.cuota, ahora_ms()])
        return self.usuario(fila["id"])

    def usuario(self, id_):
        return self.bd.una("buscar usuarios donde id = ? mostrar id, nombre, visible, rol, cuota, generacion, creado", [id_])

    def usuario_por_nombre(self, nombre):
        return self.bd.una("buscar usuarios donde nombre = ? mostrar id, nombre, visible", [normalizar_usuario(nombre)])

    def entrar(self, nombre, clave, origen="?"):
        """Comprueba usuario y contraseña. Tras 8 fallos seguidos desde el mismo sitio, espera."""
        clave_intentos = (origen, normalizar_usuario(nombre))
        with self._cerrojo_intentos:
            fallos, hasta = self._intentos.get(clave_intentos, (0, 0))
            if hasta > time.time():
                raise ErrorNube(f"Demasiados intentos. Vuelve a probar dentro de {int(hasta - time.time()) + 1} s.", 429)
        fila = self.bd.una("buscar usuarios donde nombre = ?", [normalizar_usuario(nombre)])
        if fila is None:
            hash_clave(clave or "x")  # que tarde lo mismo exista o no el usuario
            ok = False
        else:
            ok = clave_correcta(clave or "", fila["clave_hash"], fila["clave_sal"])
        with self._cerrojo_intentos:
            if ok:
                self._intentos.pop(clave_intentos, None)
            else:
                fallos += 1
                self._intentos[clave_intentos] = (fallos, time.time() + 30 if fallos >= 8 else 0)
        if not ok:
            raise ErrorNube("Usuario o contraseña incorrectos.", 401)
        return self.usuario(fila["id"])

    def cambiar_clave(self, usuario_id, actual, nueva):
        fila = self.bd.una("buscar usuarios donde id = ?", [usuario_id])
        if not clave_correcta(actual or "", fila["clave_hash"], fila["clave_sal"]):
            raise ErrorNube("La contraseña actual no es esa.", 401)
        if len(nueva or "") < 8:
            raise ErrorNube("La contraseña nueva necesita al menos 8 caracteres.")
        h, sal = hash_clave(nueva)
        self.bd.ejecutar("actualizar usuarios poner clave_hash = ?, clave_sal = ?, generacion = generacion + 1 donde id = ?",
                         [h, sal, usuario_id])

    def borrar_cuenta(self, usuario_id, clave):
        """Borra la cuenta y todo lo suyo (archivos, papelera, enlaces, equipos, lo compartido).

        Los bloques se quedan huérfanos y el barrendero los quita en su siguiente pasada.
        """
        fila = self.bd.una("buscar usuarios donde id = ?", [usuario_id])
        if not fila or not clave_correcta(clave or "", fila["clave_hash"], fila["clave_sal"]):
            raise ErrorNube("Esa no es tu contraseña.", 401)
        afectados = self.bd.consultar("""buscar compartidos como c { unir nodos como n en n.id = c.nodo_id
            donde n.propietario_id = ? mostrar distintos c.usuario_id como u }""", [usuario_id])
        with self.bd.transaccion():
            for tabla, columna in (("compartidos", "usuario_id"), ("enlaces", "creador_id"), ("equipos", "usuario_id"),
                                   ("historial", "usuario_id")):
                self.bd.ejecutar(f"borrar de {tabla} donde {columna} = ?", [usuario_id])
            ids = [f["id"] for f in self.bd.consultar("buscar nodos donde propietario_id = ? mostrar id", [usuario_id])]
            for i in range(0, len(ids), 500):
                trozo = ids[i:i + 500]
                self.bd.ejecutar(f"borrar de compartidos donde nodo_id en {lista(trozo)}", trozo)
                self.bd.ejecutar(f"borrar de enlaces donde nodo_id en {lista(trozo)}", trozo)
            self._borrar_filas(ids)
            self.bd.ejecutar("borrar de usuarios donde id = ?", [usuario_id])
        self.avisos.cambio([f["u"] for f in afectados], ["compartido"])

    def cerrar_sesiones(self, usuario_id):
        self.bd.ejecutar("actualizar usuarios poner generacion = generacion + 1 donde id = ?", [usuario_id])

    def usado(self, usuario_id):
        return self.bd.valor(
            'buscar nodos donde propietario_id = ? y tipo = "archivo" mostrar si_nulo(sumar(tamano), 0)', [usuario_id]) or 0

    def espacio(self, usuario_id):
        u = self.usuario(usuario_id)
        return {"usado": self.usado(usuario_id), "cuota": u["cuota"]}

    def comprobar_espacio(self, propietario_id, extra):
        if extra <= 0:
            return
        # El disco es de todos los servicios de la máquina: nunca se llena por scloud
        libre = shutil.disk_usage(self.datos).free
        if libre - extra < self.reserva_disco:
            raise SinEspacio("El servidor está casi lleno y no admite más archivos ahora mismo.", 507)
        e = self.espacio(propietario_id)
        if e["usado"] + extra > e["cuota"]:
            libre = max(0, e["cuota"] - e["usado"])
            raise SinEspacio(f"No cabe: quedan {libre} bytes libres en esa nube.")

    # ================================================================== nodos

    def nodo(self, id_):
        return self.bd.una("buscar nodos donde id = ?", [id_])

    def ancestros(self, nodo):
        """[raíz, …, padre, nodo]: el camino desde la raíz de su dueño."""
        camino = [nodo]
        visto = {nodo["id"]}
        while camino[0]["padre_id"]:
            padre = self.nodo(camino[0]["padre_id"])
            if padre is None or padre["id"] in visto:
                break
            visto.add(padre["id"])
            camino.insert(0, padre)
        return camino

    def permiso(self, usuario_id, nodo):
        """«propietario», «editar», «ver» o None. Un permiso sobre una carpeta vale para lo de dentro."""
        if nodo is None or nodo["borrado"]:
            return None
        if nodo["propietario_id"] == usuario_id:
            return "propietario"
        ids = [n["id"] for n in self.ancestros(nodo)]
        filas = self.bd.consultar(f"buscar compartidos donde usuario_id = ? y nodo_id en {lista(ids)} mostrar permiso",
                                  [usuario_id, *ids])
        mejor = max((NIVELES[f["permiso"]] for f in filas), default=0)
        return {0: None, 1: "ver", 2: "editar"}[mejor]

    def exigir(self, usuario_id, nodo_id, nivel="ver"):
        """El nodo, si el usuario llega al nivel pedido. Si no puede ni verlo, como si no existiera."""
        nodo = self.nodo(nodo_id)
        p = self.permiso(usuario_id, nodo)
        if p is None:
            raise NoEncontrado("No existe o no tienes acceso.")
        if NIVELES[p] < NIVELES[nivel]:
            raise SinPermiso("Solo puedes ver esto: para cambiarlo necesitas permiso de edición."
                             if nivel == "editar" else "Esto solo lo puede hacer quien es dueño.")
        nodo["permiso"] = p
        return nodo

    def carpeta_destino(self, usuario_id, carpeta_id):
        """(propietario_id, padre_id) donde crear algo: 0 es la raíz propia."""
        if not carpeta_id:
            return usuario_id, 0
        c = self.exigir(usuario_id, carpeta_id, "editar")
        if c["tipo"] != "carpeta":
            raise ErrorNube("Eso no es una carpeta.")
        return c["propietario_id"], c["id"]

    def _hijos(self, propietario_id, padre_id):
        return self.bd.consultar(
            "buscar nodos donde propietario_id = ? y padre_id = ? y borrado = 0", [propietario_id, padre_id])

    def _hijo(self, propietario_id, padre_id, nombre):
        return self.bd.una(
            "buscar nodos donde propietario_id = ? y padre_id = ? y nombre = ? y borrado = 0",
            [propietario_id, padre_id, nombre])

    def interesados(self, propietario_id, padre_id):
        """Quién ve esa carpeta: su dueño y las cuentas con las que está compartida (ella o una de arriba)."""
        quienes = {propietario_id}
        if padre_id:
            padre = self.nodo(padre_id)
            if padre:
                ids = [n["id"] for n in self.ancestros(padre)]
                for f in self.bd.consultar(f"buscar compartidos donde nodo_id en {lista(ids)} mostrar usuario_id", ids):
                    quienes.add(f["usuario_id"])
        return quienes

    def _avisar(self, propietario_id, *carpetas):
        quienes = set()
        for c in carpetas:
            quienes |= self.interesados(propietario_id, c)
        self.avisos.cambio(quienes, carpetas)

    # -------------------------------------------------------------- listar

    def _decorar(self, filas, usuario_id):
        """Marca qué hijos están compartidos o tienen enlace (solo se lo cuenta a su dueño)."""
        propios = [f["id"] for f in filas if f["propietario_id"] == usuario_id]
        compartidos, con_enlace = set(), set()
        if propios:
            compartidos = {f["nodo_id"] for f in self.bd.consultar(
                f"buscar compartidos donde nodo_id en {lista(propios)} mostrar nodo_id", propios)}
            con_enlace = {f["nodo_id"] for f in self.bd.consultar(
                f"buscar enlaces donde nodo_id en {lista(propios)} mostrar nodo_id", propios)}
        salida = []
        for f in filas:
            salida.append({
                "id": f["id"], "nombre": f["nombre"], "tipo": f["tipo"], "tamano": f["tamano"],
                "mime": f["mime"], "modificado": f["modificado"], "version": f["version"],
                "compartido": f["id"] in compartidos, "enlace": f["id"] in con_enlace,
            })
        salida.sort(key=lambda x: (x["tipo"] != "carpeta", x["nombre"].casefold()))
        return salida

    def listar(self, usuario_id, carpeta_id=0):
        if not carpeta_id:
            hijos = self._hijos(usuario_id, 0)
            return {"carpeta": {"id": 0, "nombre": "Mi nube", "tipo": "carpeta"}, "permiso": "propietario",
                    "ruta": [{"id": 0, "nombre": "Mi nube"}], "hijos": self._decorar(hijos, usuario_id)}
        c = self.exigir(usuario_id, carpeta_id, "ver")
        if c["tipo"] != "carpeta":
            raise ErrorNube("Eso no es una carpeta.")
        camino = self.ancestros(c)
        if c["permiso"] == "propietario":
            ruta = [{"id": 0, "nombre": "Mi nube"}] + [{"id": n["id"], "nombre": n["nombre"]} for n in camino]
        else:
            ids = [n["id"] for n in camino]
            con_permiso = {f["nodo_id"] for f in self.bd.consultar(
                f"buscar compartidos donde usuario_id = ? y nodo_id en {lista(ids)} mostrar nodo_id", [usuario_id, *ids])}
            desde = next(i for i, n in enumerate(camino) if n["id"] in con_permiso)
            ruta = [{"id": "compartido", "nombre": "Compartido conmigo"}] + [
                {"id": n["id"], "nombre": n["nombre"]} for n in camino[desde:]]
        dueno = self.usuario(c["propietario_id"])
        return {"carpeta": {"id": c["id"], "nombre": c["nombre"], "tipo": "carpeta"}, "permiso": c["permiso"],
                "dueno": dueno["visible"] if c["permiso"] != "propietario" else None,
                "ruta": ruta, "hijos": self._decorar(self._hijos(c["propietario_id"], c["id"]), usuario_id)}

    def compartido_conmigo(self, usuario_id):
        filas = self.bd.consultar("""buscar compartidos como c {
            unir nodos como n en n.id = c.nodo_id
            unir usuarios como u en u.id = n.propietario_id
            donde c.usuario_id = ? y n.borrado = 0
            mostrar n.id como id, n.nombre como nombre, n.tipo como tipo, n.tamano como tamano, n.mime como mime,
                    n.modificado como modificado, n.version como version, c.permiso como permiso,
                    u.visible como dueno, c.creado como desde
            ordenar c.creado desc }""", [usuario_id])
        return filas

    def buscar(self, usuario_id, texto, limite=50):
        texto = (texto or "").strip()
        if len(texto) < 2:
            return []
        filas = self.bd.consultar(
            "buscar nodos donde propietario_id = ? y borrado = 0 y minusculas(nombre) contiene ? ordenar modificado desc limite ?",
            [usuario_id, texto.lower(), limite])
        salida = self._decorar(filas, usuario_id)
        por_id = {f["id"]: f for f in filas}
        for s in salida:
            camino = self.ancestros(por_id[s["id"]])[:-1]
            s["donde"] = "/".join(n["nombre"] for n in camino) or "Mi nube"
            s["padre_id"] = por_id[s["id"]]["padre_id"]
        return salida

    def recientes(self, usuario_id, limite=12):
        filas = self.bd.consultar(
            'buscar nodos donde propietario_id = ? y borrado = 0 y tipo = "archivo" ordenar modificado desc limite ?',
            [usuario_id, limite])
        padres = {f["id"]: f["padre_id"] for f in filas}
        salida = self._decorar(filas, usuario_id)
        for s in salida:
            s["padre_id"] = padres[s["id"]]
        return sorted(salida, key=lambda x: -x["modificado"])

    # -------------------------------------------------------- crear y subir

    def crear_carpeta(self, usuario_id, carpeta_id, nombre, existe_ok=False):
        nombre = limpiar_nombre(nombre)
        propietario, padre = self.carpeta_destino(usuario_id, carpeta_id)
        with self.bd.transaccion():
            ya = self._hijo(propietario, padre, nombre)
            if ya:
                if existe_ok and ya["tipo"] == "carpeta":
                    return ya
                raise Conflicto(f"Ya hay algo que se llama «{nombre}» aquí.")
            ahora = ahora_ms()
            fila = self.bd.una(
                'insertar en nodos { propietario_id: ?, padre_id: ?, nombre: ?, tipo: "carpeta", creado: ?, modificado: ? } devolver *',
                [propietario, padre, nombre, ahora, ahora])
        self._avisar(propietario, padre)
        return fila

    def asegurar_carpetas(self, usuario_id, carpeta_id, partes):
        """Crea (si hace falta) la cadena de subcarpetas y devuelve el id de la última."""
        actual = carpeta_id
        for parte in partes:
            actual = self.crear_carpeta(usuario_id, actual, parte, existe_ok=True)["id"]
        return actual

    def preparar_subida(self, usuario_id, carpeta_id, nombre, tamano, base=None):
        """Comprueba antes de recibir nada: permiso, nombre, espacio y conflicto. Devuelve lo que habrá que pisar."""
        nombre = limpiar_nombre(nombre)
        propietario, padre = self.carpeta_destino(usuario_id, carpeta_id)
        ya = self._hijo(propietario, padre, nombre)
        if ya and ya["tipo"] == "carpeta":
            raise Conflicto(f"Ya hay una carpeta que se llama «{nombre}».")
        self.comprobar_espacio(propietario, tamano - (ya["tamano"] if ya else 0))
        if base is not None and ya and ya["huella"] != base:
            raise Conflicto("El archivo cambió en la nube desde la última vez.")
        return {"propietario": propietario, "padre": padre, "nombre": nombre, "anterior": ya}

    def registrar_archivo(self, usuario_id, carpeta_id, nombre, huella, tamano, modificado=None, base=None):
        """Apunta en el árbol un archivo cuyo bloque ya está en el almacén. Si existe, es una versión nueva."""
        nombre = limpiar_nombre(nombre)
        propietario, padre = self.carpeta_destino(usuario_id, carpeta_id)
        modificado = int(modificado or ahora_ms())
        with self.bd.transaccion():
            ya = self._hijo(propietario, padre, nombre)
            if ya and ya["tipo"] == "carpeta":
                raise Conflicto(f"Ya hay una carpeta que se llama «{nombre}».")
            if base is not None and ya and ya["huella"] not in (base, huella):
                raise Conflicto("El archivo cambió en la nube desde la última vez.")
            self.comprobar_espacio(propietario, tamano - (ya["tamano"] if ya else 0))
            if ya:
                if ya["huella"] == huella and ya["tamano"] == tamano:
                    return ya
                fila = self.bd.una(
                    "actualizar nodos poner huella = ?, tamano = ?, modificado = ?, version = version + 1 donde id = ? devolver *",
                    [huella, tamano, modificado, ya["id"]])
            else:
                fila = self.bd.una(
                    'insertar en nodos { propietario_id: ?, padre_id: ?, nombre: ?, tipo: "archivo", tamano: ?, huella: ?, mime: ?, creado: ?, modificado: ? } devolver *',
                    [propietario, padre, nombre, tamano, huella, adivinar_mime(nombre), ahora_ms(), modificado])
        self._avisar(propietario, padre)
        return fila

    def tiene_bloque(self, propietario_id, huella):
        """¿Ya tiene esta cuenta un archivo con ese contenido? Entonces se «sube» sin mandar nada.

        Solo dentro de la misma cuenta: si valiera entre cuentas, cualquiera
        podría averiguar si otra persona tiene un archivo concreto.
        """
        return bool(self.bd.valor("buscar nodos donde propietario_id = ? y huella = ? mostrar id limite 1",
                                  [propietario_id, huella])) and self.almacen.existe(huella)

    # ---------------------------------------------------- renombrar y mover

    def renombrar(self, usuario_id, nodo_id, nombre):
        n = self.exigir(usuario_id, nodo_id, "editar")
        nombre = limpiar_nombre(nombre)
        if nombre == n["nombre"]:
            return n
        with self.bd.transaccion():
            if self._hijo(n["propietario_id"], n["padre_id"], nombre):
                raise Conflicto(f"Ya hay algo que se llama «{nombre}» aquí.")
            fila = self.bd.una("actualizar nodos poner nombre = ?, modificado = ? donde id = ? devolver *",
                               [nombre, ahora_ms(), nodo_id])
        self._avisar(n["propietario_id"], n["padre_id"])
        return fila

    def mover(self, usuario_id, nodo_id, destino_id):
        n = self.exigir(usuario_id, nodo_id, "editar")
        propietario, padre = self.carpeta_destino(usuario_id, destino_id)
        if not destino_id and n["propietario_id"] != usuario_id:
            raise SinPermiso("Solo se puede mover dentro de la misma nube.")
        if propietario != n["propietario_id"]:
            raise SinPermiso("Solo se puede mover dentro de la misma nube.")
        if padre == n["padre_id"]:
            return n
        if padre and any(a["id"] == n["id"] for a in self.ancestros(self.nodo(padre))):
            raise ErrorNube("No se puede meter una carpeta dentro de sí misma.")
        with self.bd.transaccion():
            if self._hijo(propietario, padre, n["nombre"]):
                raise Conflicto(f"En el destino ya hay algo que se llama «{n['nombre']}».")
            fila = self.bd.una("actualizar nodos poner padre_id = ? donde id = ? devolver *", [padre, nodo_id])
        self._avisar(propietario, n["padre_id"], padre)
        return fila

    # -------------------------------------------------------------- papelera

    def descendientes(self, nodo_id, incluir_borrados=False):
        """Ids de todo lo que cuelga de un nodo (sin él), a lo ancho."""
        salida, pendientes = [], [nodo_id]
        filtro = "" if incluir_borrados else " y borrado = 0"
        while pendientes:
            filas = self.bd.consultar(f"buscar nodos donde padre_id en {lista(pendientes)}{filtro} mostrar id, tipo",
                                      pendientes)
            pendientes = [f["id"] for f in filas if f["tipo"] == "carpeta"]
            salida.extend(f["id"] for f in filas)
        return salida

    def a_papelera(self, usuario_id, nodo_id):
        n = self.exigir(usuario_id, nodo_id, "editar")
        ids = [nodo_id] + (self.descendientes(nodo_id) if n["tipo"] == "carpeta" else [])
        ahora = ahora_ms()
        with self.bd.transaccion():
            for i in range(0, len(ids), 500):
                trozo = ids[i:i + 500]
                self.bd.ejecutar(f"actualizar nodos poner borrado = ?, papelera_de = ? donde id en {lista(trozo)}",
                                 [ahora, nodo_id, *trozo])
        self._avisar(n["propietario_id"], n["padre_id"])
        return len(ids)

    def papelera(self, usuario_id):
        filas = self.bd.consultar(
            "buscar nodos donde propietario_id = ? y borrado > 0 y papelera_de = id ordenar borrado desc", [usuario_id])
        salida = []
        for f in filas:
            dentro = self.bd.valor("buscar nodos donde papelera_de = ? mostrar contar()", [f["id"]])
            salida.append({"id": f["id"], "nombre": f["nombre"], "tipo": f["tipo"], "tamano": f["tamano"],
                           "mime": f["mime"], "borrado": f["borrado"], "elementos": dentro,
                           "caduca": f["borrado"] + DIAS_PAPELERA * 86400 * 1000})
        return salida

    def _de_mi_papelera(self, usuario_id, nodo_id):
        n = self.nodo(nodo_id)
        if not n or n["propietario_id"] != usuario_id or not n["borrado"] or n["papelera_de"] != n["id"]:
            raise NoEncontrado("Eso no está en tu papelera.")
        return n

    def recuperar(self, usuario_id, nodo_id):
        n = self._de_mi_papelera(usuario_id, nodo_id)
        with self.bd.transaccion():
            padre = n["padre_id"]
            if padre:
                p = self.nodo(padre)
                if not p or p["borrado"]:
                    padre = 0  # su carpeta ya no está: vuelve a la raíz
            ocupados = {h["nombre"] for h in self._hijos(usuario_id, padre)}
            nombre = nombre_libre(n["nombre"], ocupados)
            # Primero el nombre libre (aún en la papelera) y luego de vuelta: al revés chocaría con el «unico»
            self.bd.ejecutar("actualizar nodos poner padre_id = ?, nombre = ? donde id = ?", [padre, nombre, nodo_id])
            self.bd.ejecutar("actualizar nodos poner borrado = 0, papelera_de = 0 donde papelera_de = ?", [nodo_id])
        self._avisar(usuario_id, padre)
        return self.nodo(nodo_id)

    def eliminar(self, usuario_id, nodo_id):
        self._de_mi_papelera(usuario_id, nodo_id)
        with self.bd.transaccion():
            ids = [f["id"] for f in self.bd.consultar("buscar nodos donde papelera_de = ? mostrar id", [nodo_id])]
            self._borrar_filas(ids)
        self.avisos.cambio([usuario_id], ["papelera"])

    def vaciar_papelera(self, usuario_id):
        with self.bd.transaccion():
            ids = [f["id"] for f in self.bd.consultar(
                "buscar nodos donde propietario_id = ? y borrado > 0 mostrar id", [usuario_id])]
            self._borrar_filas(ids)
        self.avisos.cambio([usuario_id], ["papelera"])
        return len(ids)

    def _borrar_filas(self, ids):
        for i in range(0, len(ids), 500):
            trozo = ids[i:i + 500]
            self.bd.ejecutar(f"borrar de nodos donde id en {lista(trozo)}", trozo)

    # ============================================================ compartir

    def compartir(self, usuario_id, nodo_id, con, permiso):
        n = self.exigir(usuario_id, nodo_id, "propietario")
        if permiso not in ("ver", "editar"):
            raise ErrorNube("El permiso es «ver» o «editar».")
        otro = self.usuario_por_nombre(con)
        if otro is None:
            raise NoEncontrado(f"No hay ninguna cuenta que se llame «{con}».")
        if otro["id"] == usuario_id:
            raise ErrorNube("Ya es tuyo.")
        self.bd.ejecutar(
            "insertar o actualizar en compartidos { nodo_id: ?, usuario_id: ?, permiso: ?, por_id: ?, creado: ? }",
            [nodo_id, otro["id"], permiso, usuario_id, ahora_ms()])
        self.avisos.cambio([otro["id"]], ["compartido"])
        self.avisos.enviar([otro["id"]], "compartido", {"nombre": n["nombre"], "de": self.usuario(usuario_id)["visible"]})
        self._avisar(usuario_id, n["padre_id"])
        return self.acceso(usuario_id, nodo_id)

    def _compartido_mio(self, usuario_id, compartido_id):
        c = self.bd.una("buscar compartidos donde id = ?", [compartido_id])
        if c is None:
            raise NoEncontrado("Ese permiso ya no existe.")
        self.exigir(usuario_id, c["nodo_id"], "propietario")
        return c

    def cambiar_permiso(self, usuario_id, compartido_id, permiso):
        if permiso not in ("ver", "editar"):
            raise ErrorNube("El permiso es «ver» o «editar».")
        c = self._compartido_mio(usuario_id, compartido_id)
        self.bd.ejecutar("actualizar compartidos poner permiso = ? donde id = ?", [permiso, compartido_id])
        self.avisos.cambio([c["usuario_id"]], ["compartido", c["nodo_id"]])
        return self.acceso(usuario_id, c["nodo_id"])

    def dejar_de_compartir(self, usuario_id, compartido_id):
        """Lo quita su dueño, o la persona con la que se compartió («quitar de mi lista»)."""
        c = self.bd.una("buscar compartidos donde id = ?", [compartido_id])
        if c is None:
            raise NoEncontrado("Ese permiso ya no existe.")
        if c["usuario_id"] != usuario_id:
            self.exigir(usuario_id, c["nodo_id"], "propietario")
        self.bd.ejecutar("borrar de compartidos donde id = ?", [compartido_id])
        n = self.nodo(c["nodo_id"])
        self.avisos.cambio([c["usuario_id"]], ["compartido", c["nodo_id"]])
        self._avisar(n["propietario_id"], n["padre_id"])

    def salir_de_compartido(self, usuario_id, nodo_id):
        c = self.bd.una("buscar compartidos donde nodo_id = ? y usuario_id = ?", [nodo_id, usuario_id])
        if c is None:
            raise NoEncontrado("Eso no está compartido contigo.")
        self.dejar_de_compartir(usuario_id, c["id"])

    def acceso(self, usuario_id, nodo_id):
        n = self.exigir(usuario_id, nodo_id, "propietario")
        personas = self.bd.consultar("""buscar compartidos como c {
            unir usuarios como u en u.id = c.usuario_id
            donde c.nodo_id = ?
            mostrar c.id como id, u.nombre como usuario, u.visible como visible, c.permiso como permiso, c.creado como desde
            ordenar c.creado }""", [nodo_id])
        enlaces = [self._enlace_publico(e) for e in self.bd.consultar(
            "buscar enlaces donde nodo_id = ? ordenar creado desc", [nodo_id])]
        # Lo que viene de arriba: si una carpeta que lo contiene está compartida, esto también
        heredado = []
        camino = self.ancestros(n)[:-1]
        if camino:
            ids = [a["id"] for a in camino]
            heredado = self.bd.consultar(f"""buscar compartidos como c {{
                unir usuarios como u en u.id = c.usuario_id
                unir nodos como n en n.id = c.nodo_id
                donde c.nodo_id en {lista(ids)}
                mostrar u.visible como visible, c.permiso como permiso, n.nombre como desde_carpeta }}""", ids)
        return {"nodo": {"id": n["id"], "nombre": n["nombre"], "tipo": n["tipo"]},
                "personas": personas, "heredado": heredado, "enlaces": enlaces}

    def buscar_personas(self, usuario_id, texto):
        """Para el desplegable de compartir: hace falta escribir 3 letras del usuario exacto."""
        texto = normalizar_usuario(texto)
        if len(texto) < 3:
            return []
        return self.bd.consultar(
            "buscar usuarios donde nombre empieza por ? y id != ? mostrar nombre, visible ordenar nombre limite 6",
            [texto, usuario_id])

    # ============================================================== enlaces

    def _enlace_publico(self, e):
        ahora = ahora_ms()
        vivo = (not e["caduca"] or e["caduca"] > ahora) and (not e["max_descargas"] or e["descargas"] < e["max_descargas"])
        return {"id": e["id"], "pista": e["pista"], "permiso": e["permiso"], "con_clave": bool(e["clave_hash"]),
                "caduca": e["caduca"], "max_descargas": e["max_descargas"], "descargas": e["descargas"],
                "creado": e["creado"], "vivo": vivo, "nodo_id": e["nodo_id"]}

    def crear_enlace(self, usuario_id, nodo_id, permiso="descargar", dias=0, clave="", max_descargas=0):
        n = self.exigir(usuario_id, nodo_id, "propietario")
        if permiso not in ("descargar", "subir"):
            raise ErrorNube("El enlace es para «descargar» o para «subir».")
        if permiso == "subir" and n["tipo"] != "carpeta":
            raise ErrorNube("Solo se pueden pedir archivos en una carpeta.")
        dias, max_descargas = int(dias or 0), int(max_descargas or 0)
        if not 0 <= dias <= 365 or not 0 <= max_descargas <= 100000:
            raise ErrorNube("Caducidad entre 0 y 365 días; descargas entre 0 y 100 000.")
        token = secrets.token_urlsafe(18)
        clave_hash = clave_sal = None
        if clave:
            if len(clave) < 4:
                raise ErrorNube("La contraseña del enlace necesita al menos 4 caracteres.")
            clave_hash, clave_sal = hash_clave(clave)
        ahora = ahora_ms()
        e = self.bd.una("""insertar en enlaces { token_hash: ?, pista: ?, nodo_id: ?, creador_id: ?, permiso: ?,
                           clave_hash: ?, clave_sal: ?, caduca: ?, max_descargas: ?, creado: ? } devolver *""",
                        [hash_token(token), token[:6], nodo_id, usuario_id, permiso, clave_hash, clave_sal,
                         ahora + dias * 86400000 if dias else 0, max_descargas, ahora])
        self._avisar(usuario_id, n["padre_id"])
        return token, self._enlace_publico(e)

    def quitar_enlace(self, usuario_id, enlace_id):
        e = self.bd.una("buscar enlaces donde id = ?", [enlace_id])
        if e is None:
            raise NoEncontrado("Ese enlace ya no existe.")
        n = self.exigir(usuario_id, e["nodo_id"], "propietario")
        self.bd.ejecutar("borrar de enlaces donde id = ?", [enlace_id])
        self._avisar(usuario_id, n["padre_id"])

    def mis_enlaces(self, usuario_id):
        filas = self.bd.consultar("""buscar enlaces como e {
            unir nodos como n en n.id = e.nodo_id
            donde e.creador_id = ? y n.borrado = 0
            mostrar e.*, n.nombre como nombre, n.tipo como tipo
            ordenar e.creado desc }""", [usuario_id])
        salida = []
        for f in filas:
            p = self._enlace_publico(f)
            p.update(nombre=f["nombre"], tipo=f["tipo"])
            salida.append(p)
        return salida

    def enlace(self, token):
        """El enlace y su nodo, si sigue valiendo. Si no, NoEncontrado (sin decir por qué a quien no es dueño)."""
        e = self.bd.una("buscar enlaces donde token_hash = ?", [hash_token(token or "")])
        if e is None:
            raise NoEncontrado("Este enlace no existe o lo han quitado.")
        p = self._enlace_publico(e)
        if not p["vivo"]:
            raise ErrorNube("Este enlace ha caducado.", 410)
        n = self.nodo(e["nodo_id"])
        if not n or n["borrado"]:
            raise NoEncontrado("Lo que compartía este enlace ya no está.")
        return e, n

    def clave_enlace_ok(self, e, clave):
        return not e["clave_hash"] or clave_correcta(clave or "", e["clave_hash"], e["clave_sal"])

    def contar_descarga(self, enlace_id):
        self.bd.ejecutar("actualizar enlaces poner descargas = descargas + 1 donde id = ?", [enlace_id])

    def dentro_de(self, raiz, nodo_id):
        """El nodo, si cuelga de «raiz» (para navegar dentro de un enlace a una carpeta)."""
        if not nodo_id or int(nodo_id) == raiz["id"]:
            return raiz
        n = self.nodo(int(nodo_id))
        if not n or n["borrado"] or not any(a["id"] == raiz["id"] for a in self.ancestros(n)):
            raise NoEncontrado("Eso no está en este enlace.")
        return n

    def listar_enlace(self, raiz, carpeta):
        camino = self.ancestros(carpeta)
        desde = next(i for i, n in enumerate(camino) if n["id"] == raiz["id"])
        hijos = self._hijos(carpeta["propietario_id"], carpeta["id"])
        return {"ruta": [{"id": n["id"], "nombre": n["nombre"]} for n in camino[desde:]],
                "hijos": [{"id": h["id"], "nombre": h["nombre"], "tipo": h["tipo"], "tamano": h["tamano"],
                           "mime": h["mime"], "modificado": h["modificado"]}
                          for h in sorted(hijos, key=lambda x: (x["tipo"] != "carpeta", x["nombre"].casefold()))]}

    # ============================================================== equipos

    def crear_equipo(self, usuario_id, nombre):
        nombre = (nombre or "").strip()[:60] or "Mi equipo"
        token = "scl_" + secrets.token_urlsafe(32)
        fila = self.bd.una("insertar en equipos { usuario_id: ?, nombre: ?, token_hash: ?, creado: ? } devolver id, nombre, creado",
                           [usuario_id, nombre, hash_token(token), ahora_ms()])
        self.avisos.enviar([usuario_id], "equipos", {})
        return token, fila

    def equipo_por_token(self, token):
        e = self.bd.una("buscar equipos donde token_hash = ?", [hash_token(token or "")])
        if e is None:
            raise ErrorNube("Este equipo ya no tiene acceso. Vuelve a conectarlo.", 401)
        return e, self.usuario(e["usuario_id"])

    def equipo_visto(self, equipo_id, carpetas=None):
        if carpetas is None:
            self.bd.ejecutar("actualizar equipos poner visto = ? donde id = ?", [ahora_ms(), equipo_id])
        else:
            self.bd.ejecutar("actualizar equipos poner visto = ?, carpetas = ? donde id = ?",
                             [ahora_ms(), carpetas, equipo_id])

    def equipos(self, usuario_id):
        return self.bd.consultar("buscar equipos donde usuario_id = ? mostrar id, nombre, creado, visto, carpetas ordenar creado",
                                 [usuario_id])

    def quitar_equipo(self, usuario_id, equipo_id):
        n = self.bd.ejecutar("borrar de equipos donde id = ? y usuario_id = ?", [equipo_id, usuario_id])
        self.avisos.enviar([usuario_id], "equipos", {})
        return n

    # ======================================================= rutas (sockets)

    def resolver(self, usuario_id, ruta):
        """El nodo de la ruta «/a/b» en la nube propia; None es la raíz. NoEncontrado si no está."""
        padre = None
        for parte in partes_ruta(ruta):
            n = self._hijo(usuario_id, padre["id"] if padre else 0, parte)
            if n is None:
                raise NoEncontrado(f"No existe «{ruta}».")
            padre = n
        return padre

    def arbol(self, usuario_id, raiz_id=0):
        """Todo lo que hay debajo de una carpeta propia, con rutas relativas a ella."""
        salida = []
        pendientes = [(raiz_id, "")]
        while pendientes:
            padre_id, prefijo = pendientes.pop()
            for h in self._hijos(usuario_id, padre_id):
                ruta = f"{prefijo}{h['nombre']}"
                salida.append({"ruta": ruta, "tipo": h["tipo"], "tam": h["tamano"], "huella": h["huella"],
                               "mod": h["modificado"], "version": h["version"], "id": h["id"]})
                if h["tipo"] == "carpeta":
                    pendientes.append((h["id"], ruta + "/"))
        salida.sort(key=lambda x: x["ruta"])
        return salida

    # ============================================================ historial

    def apuntar(self, usuario_id, sentido, canal, nombre, bytes_, ms):
        self.bd.ejecutar(
            "insertar en historial { usuario_id: ?, sentido: ?, canal: ?, nombre: ?, bytes: ?, ms: ?, momento: ? }",
            [usuario_id, sentido, canal, nombre[:255], int(bytes_), int(ms), ahora_ms()])

    def actividad(self, usuario_id, dias=14):
        desde = ahora_ms() - dias * 86400000
        filas = self.bd.consultar(
            "buscar historial donde usuario_id = ? y momento > ? mostrar sentido, canal, bytes, momento", [usuario_id, desde])
        ultimas = self.bd.consultar(
            "buscar historial donde usuario_id = ? ordenar momento desc limite 8 mostrar sentido, canal, nombre, bytes, ms, momento",
            [usuario_id])
        return {"filas": filas, "ultimas": ultimas}

    # =========================================================== barrendero

    def barrer(self):
        """Vacía lo que lleva más de 30 días en la papelera y los bloques que ya nadie usa."""
        limite = ahora_ms() - DIAS_PAPELERA * 86400000
        with self.bd.transaccion():
            viejos = [f["id"] for f in self.bd.consultar("buscar nodos donde borrado > 0 y borrado < ? mostrar id", [limite])]
            self._borrar_filas(viejos)
        # El conjunto de huellas en uso se lee con el cerrojo de la base, pero el
        # borrado de ficheros va fuera: una subida que termine justo ahora puede
        # haber colocado un bloque nuevo que aún no está en la base, así que
        # solo se borran los que llevan un rato sin usarse (ver Almacen.barrer).
        en_uso = {f["huella"] for f in self.bd.consultar("buscar nodos donde huella no es nulo mostrar distintos huella")}
        bloques, liberados = self.almacen.barrer(en_uso)
        return {"papelera": len(viejos), "bloques": bloques, "bytes": liberados}
