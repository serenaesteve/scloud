"""La web: Flask sirve la página y una API JSON.

Flask atiende cada petición en su propio hilo (servidor de Werkzeug con
ThreadingMixIn). Algunas peticiones viven mucho: una subida de 2 GB o la
pestaña escuchando /api/eventos. Por eso todo lo compartido está en la nube,
protegido con cerrojos, y nada vive en variables globales del módulo.

Seguridad, en corto:
  · la sesión va en una cookie firmada, HttpOnly y SameSite=Lax;
  · lo que cambia algo exige la cabecera «X-Scloud: 1», que un formulario
    de otra web no puede poner sin que el navegador pregunte antes (CSRF);
  · lo que se descarga va con «Content-Security-Policy: sandbox» y nosniff:
    un HTML o un SVG subido no puede ejecutar nada con tu sesión.
"""

import io
import json
import mimetypes
import queue
import re
import secrets
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory, session
from werkzeug.exceptions import ClientDisconnected

from .almacen import Cancelada
from .bd import ahora_ms
from .nube import ErrorNube, NoEncontrado, nombre_libre, partes_ruta

WEB = Path(__file__).resolve().parent.parent / "web"
TROZO = 256 * 1024
EN_LINEA = re.compile(r"^(image/(png|jpeg|gif|webp|avif)|application/pdf|audio/.*|video/.*|text/plain)$")


def crear_app(nube, equipos=None, publico=False):
    app = Flask(__name__, static_folder=None)
    app.secret_key = _clave_sesion(nube.datos)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=publico,
                      SESSION_COOKIE_NAME="scloud", PERMANENT_SESSION_LIFETIME=30 * 86400, JSON_AS_ASCII=False)
    reg = nube.transferencias
    altas = {}
    altas_cerrojo = threading.Lock()
    intentos_enlace = {}

    # ----------------------------------------------------------- común

    @app.errorhandler(ErrorNube)
    def _error_nube(e):
        return jsonify(error=str(e)), e.estado

    @app.before_request
    def _csrf():
        if request.path.startswith("/api/") and request.method not in ("GET", "HEAD") \
                and request.headers.get("X-Scloud") != "1":
            return jsonify(error="Falta la cabecera X-Scloud."), 403

    @app.after_request
    def _cabeceras(r):
        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("Referrer-Policy", "same-origin")
        if r.mimetype == "text/html":
            r.headers.setdefault("Content-Security-Policy",
                                 "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                                 "style-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if request.path.startswith("/api/"):
            r.headers.setdefault("Cache-Control", "no-store")
        return r

    def yo():
        uid = session.get("u")
        if not uid:
            raise ErrorNube("Tienes que entrar.", 401)
        u = nube.usuario(uid)
        if u is None or u["generacion"] != session.get("g"):
            session.clear()
            raise ErrorNube("La sesión ha caducado. Vuelve a entrar.", 401)
        return u

    def cuerpo():
        return request.get_json(silent=True) or {}

    def ip():
        # Detrás de Apache, la IP real es la ÚLTIMA de X-Forwarded-For (la que añade él);
        # las de delante las puede escribir cualquiera.
        if publico and request.headers.get("X-Forwarded-For"):
            return request.headers["X-Forwarded-For"].split(",")[-1].strip()
        return request.remote_addr or "?"

    # --------------------------------------------------------- páginas

    @app.get("/")
    def portada():
        return send_from_directory(WEB, "index.html")

    @app.get("/privacidad")
    def privacidad():
        return send_from_directory(WEB, "privacidad.html")

    @app.get("/e/<token>")
    def pagina_enlace(token):
        return send_from_directory(WEB, "enlace.html")

    @app.get("/<path:ruta>")
    def estaticos(ruta):
        if ruta.startswith("api/"):
            abort(404)
        r = send_from_directory(WEB, ruta)
        if ruta.startswith(("fuentes/",)):
            r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return r

    # ---------------------------------------------------------- cuenta

    @app.post("/api/registro")
    def registro():
        if not nube.registro_abierto:
            raise ErrorNube("Aquí no se pueden crear cuentas desde la web.", 403)
        d = cuerpo()
        with altas_cerrojo:
            ahora = time.time()
            recientes = [t for t in altas.get(ip(), []) if t > ahora - 3600]
            if len(recientes) >= 5:
                raise ErrorNube("Se han creado muchas cuentas desde aquí. Prueba dentro de un rato.", 429)
        u = nube.crear_usuario(d.get("usuario", ""), d.get("nombre", ""), d.get("clave", ""))
        with altas_cerrojo:
            altas.setdefault(ip(), []).append(time.time())
        _abrir_sesion(u)
        return jsonify(_yo_json(u)), 201

    @app.post("/api/entrar")
    def entrar():
        d = cuerpo()
        u = nube.entrar(d.get("usuario", ""), d.get("clave", ""), origen=ip())
        _abrir_sesion(u)
        return jsonify(_yo_json(u))

    def _abrir_sesion(u):
        session.clear()
        session.permanent = True
        session["u"], session["g"] = u["id"], u["generacion"]

    def _yo_json(u):
        e = nube.espacio(u["id"])
        return {"usuario": u["nombre"], "visible": u["visible"], "rol": u["rol"], **e,
                "registro": nube.registro_abierto}

    @app.post("/api/salir")
    def salir():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/yo")
    def api_yo():
        # Sin sesión no es un error: es la portada. 200 para no ensuciar la consola.
        if not session.get("u"):
            return jsonify(sesion=False, registro=nube.registro_abierto)
        try:
            return jsonify(sesion=True, **_yo_json(yo()))
        except ErrorNube:
            return jsonify(sesion=False, registro=nube.registro_abierto)

    @app.post("/api/clave")
    def cambiar_clave():
        u = yo()
        d = cuerpo()
        nube.cambiar_clave(u["id"], d.get("actual"), d.get("nueva"))
        _abrir_sesion(nube.usuario(u["id"]))  # las demás sesiones quedan cerradas; esta no
        return jsonify(ok=True)

    @app.post("/api/borrar-cuenta")
    def borrar_cuenta():
        u = yo()
        nube.borrar_cuenta(u["id"], cuerpo().get("clave", ""))
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/cerrar-sesiones")
    def cerrar_sesiones():
        u = yo()
        nube.cerrar_sesiones(u["id"])
        _abrir_sesion(nube.usuario(u["id"]))
        return jsonify(ok=True)

    # ----------------------------------------------------------- árbol

    @app.get("/api/carpeta/<int:id_>")
    def carpeta(id_):
        return jsonify(nube.listar(yo()["id"], id_))

    @app.get("/api/compartido")
    def compartido():
        return jsonify(nube.compartido_conmigo(yo()["id"]))

    @app.get("/api/recientes")
    def recientes():
        return jsonify(nube.recientes(yo()["id"]))

    @app.get("/api/buscar")
    def buscar():
        return jsonify(nube.buscar(yo()["id"], request.args.get("q", "")))

    @app.post("/api/carpeta")
    def nueva_carpeta():
        d = cuerpo()
        fila = nube.crear_carpeta(yo()["id"], int(d.get("padre") or 0), d.get("nombre", ""))
        return jsonify(id=fila["id"], nombre=fila["nombre"]), 201

    @app.patch("/api/nodo/<int:id_>")
    def cambiar_nodo(id_):
        u, d = yo(), cuerpo()
        if "nombre" in d:
            nube.renombrar(u["id"], id_, d["nombre"])
        if "padre" in d:
            nube.mover(u["id"], id_, int(d["padre"] or 0))
        return jsonify(ok=True)

    @app.post("/api/mover")
    def mover_varios():
        u, d = yo(), cuerpo()
        errores = []
        for id_ in d.get("ids", [])[:500]:
            try:
                nube.mover(u["id"], int(id_), int(d.get("destino") or 0))
            except ErrorNube as e:
                errores.append(str(e))
        return jsonify(ok=not errores, errores=errores)

    @app.post("/api/a-papelera")
    def a_papelera():
        u, d = yo(), cuerpo()
        total = 0
        for id_ in d.get("ids", [])[:500]:
            total += nube.a_papelera(u["id"], int(id_))
        return jsonify(ok=True, elementos=total)

    @app.get("/api/papelera")
    def papelera():
        return jsonify(nube.papelera(yo()["id"]))

    @app.post("/api/papelera/<int:id_>/recuperar")
    def recuperar(id_):
        n = nube.recuperar(yo()["id"], id_)
        return jsonify(ok=True, padre=n["padre_id"], nombre=n["nombre"])

    @app.delete("/api/papelera/<int:id_>")
    def eliminar(id_):
        nube.eliminar(yo()["id"], id_)
        return jsonify(ok=True)

    @app.delete("/api/papelera")
    def vaciar():
        return jsonify(ok=True, elementos=nube.vaciar_papelera(yo()["id"]))

    # ---------------------------------------------------------- subir

    @app.put("/api/subir")
    def subir():
        u = yo()
        tam = request.content_length
        if tam is None:
            raise ErrorNube("Falta Content-Length.", 411)
        partes = partes_ruta(request.args.get("ruta", ""))
        if not partes:
            raise ErrorNube("Falta el nombre del archivo.")
        carpeta_id = nube.asegurar_carpetas(u["id"], int(request.args.get("carpeta") or 0), partes[:-1])
        nombre = partes[-1]
        nube.preparar_subida(u["id"], carpeta_id, nombre, tam)
        id_cliente = request.headers.get("X-Scl-Id", "")
        id_cliente = "w" + id_cliente if re.fullmatch(r"[A-Za-z0-9_-]{6,40}", id_cliente) else None
        inicio = time.time()
        try:
            with reg.turno(u["id"], nombre, "subida", "web", tam, origen="navegador", id_cliente=id_cliente) as t:
                huella = nube.almacen.guardar_flujo(request.stream.read, tam, lambda n: reg.avanzar(t, n),
                                                    cancelada=t.cancelar.is_set)
        except Cancelada:
            raise ErrorNube("Subida cancelada.", 499)
        except (ConnectionError, ClientDisconnected):
            raise ErrorNube("La subida se cortó a mitad.", 400)
        mod = request.args.get("mod", type=int)
        fila = nube.registrar_archivo(u["id"], carpeta_id, nombre, huella, tam, mod)
        nube.apuntar(u["id"], "subida", "web", nombre, tam, (time.time() - inicio) * 1000)
        return jsonify(id=fila["id"], nombre=fila["nombre"], version=fila["version"], carpeta=carpeta_id), 201

    # ---------------------------------------------------------- bajar

    def _enviar_archivo(n, usuario_id, canal, en_linea=False, enlace_id=None):
        tam = n["tamano"]
        inicio, fin, parcial = 0, tam - 1, False
        rango = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("Range", ""))
        if rango and tam:
            a, b = rango.groups()
            if a:
                inicio, fin = int(a), min(int(b) if b else tam - 1, tam - 1)
            elif b:
                inicio, fin = max(0, tam - int(b)), tam - 1
            if inicio > fin or inicio >= tam:
                return Response(status=416, headers={"Content-Range": f"bytes */{tam}"})
            parcial = True
        largo = fin - inicio + 1 if tam else 0
        if enlace_id and not parcial and not en_linea:
            nube.contar_descarga(enlace_id)

        def generar_silencioso():
            # Vistas previas y miniaturas: no son descargas, no se apuntan en la bandeja
            with nube.almacen.abrir(n["huella"]) as f:
                f.seek(inicio)
                queda = largo
                while queda > 0 and (trozo := f.read(min(TROZO, queda))):
                    queda -= len(trozo)
                    yield trozo

        def generar():
            empezado = time.time()
            with reg.turno(usuario_id, n["nombre"], "bajada", canal, largo,
                           origen="enlace público" if canal == "enlace" else "navegador") as t:
                with nube.almacen.abrir(n["huella"]) as f:
                    f.seek(inicio)
                    queda = largo
                    while queda > 0:
                        if t.cancelar.is_set():
                            return
                        trozo = f.read(min(TROZO, queda))
                        if not trozo:
                            break
                        queda -= len(trozo)
                        reg.avanzar(t, len(trozo))
                        yield trozo
            if largo > 64 * 1024 or not parcial:
                nube.apuntar(usuario_id, "bajada", canal, n["nombre"], largo, (time.time() - empezado) * 1000)

        mime = n["mime"] or "application/octet-stream"
        inline = en_linea and EN_LINEA.match(mime)
        cabeceras = {
            "Content-Length": str(largo), "Accept-Ranges": "bytes", "ETag": f'"{n["huella"]}"',
            "Content-Disposition": _disposicion("inline" if inline else "attachment", n["nombre"]),
            "Content-Security-Policy": "sandbox", "Cache-Control": "private, no-cache",
        }
        if parcial:
            cabeceras["Content-Range"] = f"bytes {inicio}-{fin}/{tam}"
        if mime == "text/plain":
            mime = "text/plain; charset=utf-8"
        return Response(generar_silencioso() if en_linea else generar(), status=206 if parcial else 200,
                        mimetype=mime, headers=cabeceras,
                        direct_passthrough=True)

    def _enviar_zip(nombre_zip, raices, usuario_id, canal, enlace_id=None):
        """Un ZIP hecho al vuelo, sin escribirlo antes en disco."""
        entradas = []
        for raiz in raices:
            if raiz["tipo"] == "archivo":
                entradas.append((raiz["nombre"], raiz))
                continue
            pendientes = [(raiz, raiz["nombre"] + "/")]
            entradas.append((raiz["nombre"] + "/", None))
            while pendientes:
                carpeta, prefijo = pendientes.pop()
                for h in nube._hijos(carpeta["propietario_id"], carpeta["id"]):
                    if h["tipo"] == "carpeta":
                        entradas.append((prefijo + h["nombre"] + "/", None))
                        pendientes.append((h, prefijo + h["nombre"] + "/"))
                    else:
                        entradas.append((prefijo + h["nombre"], h))
        total = sum(n["tamano"] for _, n in entradas if n)
        if enlace_id:
            nube.contar_descarga(enlace_id)

        def generar():
            tubo = _Tubo()
            with reg.turno(usuario_id, nombre_zip, "bajada", canal, total,
                           origen="enlace público" if canal == "enlace" else "navegador") as t:
                with zipfile.ZipFile(tubo, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                    for ruta, n in entradas:
                        if n is None:
                            zf.writestr(zipfile.ZipInfo(ruta, _fecha_zip(time.time() * 1000)), b"")
                            continue
                        info = zipfile.ZipInfo(ruta, _fecha_zip(n["modificado"]))
                        info.file_size = n["tamano"]
                        with nube.almacen.abrir(n["huella"]) as f, zf.open(info, "w", force_zip64=n["tamano"] > 2 ** 31) as dst:
                            while trozo := f.read(TROZO):
                                if t.cancelar.is_set():
                                    return
                                dst.write(trozo)
                                reg.avanzar(t, len(trozo))
                                yield tubo.sacar()
                        yield tubo.sacar()
                yield tubo.sacar()

        return Response(generar(), mimetype="application/zip", headers={
            "Content-Disposition": _disposicion("attachment", nombre_zip), "Cache-Control": "private, no-cache"})

    @app.get("/api/bajar/<int:id_>")
    def bajar(id_):
        u = yo()
        n = nube.exigir(u["id"], id_, "ver")
        if n["tipo"] == "carpeta":
            return _enviar_zip(n["nombre"] + ".zip", [n], u["id"], "web")
        return _enviar_archivo(n, u["id"], "web", en_linea=request.args.get("ver") == "1")

    @app.get("/api/zip")
    def bajar_varios():
        u = yo()
        ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()][:500]
        if not ids:
            raise ErrorNube("No has elegido nada.")
        nodos = [nube.exigir(u["id"], i, "ver") for i in ids]
        return _enviar_zip(f"scloud {datetime.now():%Y-%m-%d %H.%M}.zip", nodos, u["id"], "web")

    # --------------------------------------------------- compartir y enlaces

    @app.get("/api/nodo/<int:id_>/acceso")
    def acceso(id_):
        return jsonify(nube.acceso(yo()["id"], id_))

    @app.post("/api/nodo/<int:id_>/compartir")
    def compartir(id_):
        d = cuerpo()
        return jsonify(nube.compartir(yo()["id"], id_, d.get("usuario", ""), d.get("permiso", "ver")))

    @app.patch("/api/compartido/<int:id_>")
    def cambiar_permiso(id_):
        return jsonify(nube.cambiar_permiso(yo()["id"], id_, cuerpo().get("permiso")))

    @app.delete("/api/compartido/<int:id_>")
    def quitar_permiso(id_):
        nube.dejar_de_compartir(yo()["id"], id_)
        return jsonify(ok=True)

    @app.delete("/api/compartido/nodo/<int:nodo_id>")
    def salir_compartido(nodo_id):
        nube.salir_de_compartido(yo()["id"], nodo_id)
        return jsonify(ok=True)

    @app.get("/api/personas")
    def personas():
        return jsonify(nube.buscar_personas(yo()["id"], request.args.get("q", "")))

    @app.post("/api/nodo/<int:id_>/enlace")
    def crear_enlace(id_):
        d = cuerpo()
        token, e = nube.crear_enlace(yo()["id"], id_, d.get("permiso", "descargar"), d.get("dias") or 0,
                                     d.get("clave") or "", d.get("max_descargas") or 0)
        # El enlace completo solo se enseña ahora: en la base solo queda su huella
        return jsonify(url=f"{request.host_url}e/{token}", **e), 201

    @app.delete("/api/enlace/<int:id_>")
    def quitar_enlace(id_):
        nube.quitar_enlace(yo()["id"], id_)
        return jsonify(ok=True)

    @app.get("/api/enlaces")
    def enlaces():
        return jsonify(nube.mis_enlaces(yo()["id"]))

    # ------------------------------------------------- enlaces públicos

    def _enlace(token, exigir_clave=True):
        e, n = nube.enlace(token)
        if exigir_clave and e["clave_hash"] and not session.get(f"e{e['id']}"):
            raise ErrorNube("Este enlace pide contraseña.", 401)
        return e, n

    @app.get("/api/e/<token>")
    def ver_enlace(token):
        e, n = _enlace(token, exigir_clave=False)
        dueno = nube.usuario(n["propietario_id"])
        datos = {"nombre": n["nombre"], "tipo": n["tipo"], "tamano": n["tamano"], "mime": n["mime"],
                 "permiso": e["permiso"], "de": dueno["visible"], "caduca": e["caduca"],
                 "con_clave": bool(e["clave_hash"]), "abierto": not e["clave_hash"] or bool(session.get(f"e{e['id']}")),
                 "quedan": (e["max_descargas"] - e["descargas"]) if e["max_descargas"] else None}
        if datos["abierto"] and n["tipo"] == "carpeta" and e["permiso"] == "descargar":
            carpeta = nube.dentro_de(n, request.args.get("carpeta", type=int))
            if carpeta["tipo"] != "carpeta":
                raise ErrorNube("Eso no es una carpeta.")
            datos.update(nube.listar_enlace(n, carpeta), carpeta=carpeta["id"])
        return jsonify(datos)

    @app.post("/api/e/<token>/clave")
    def clave_enlace(token):
        e, _ = _enlace(token, exigir_clave=False)
        clave_intentos = (ip(), e["id"])
        fallos, hasta = intentos_enlace.get(clave_intentos, (0, 0))
        if hasta > time.time():
            raise ErrorNube("Demasiados intentos. Espera un poco.", 429)
        if not nube.clave_enlace_ok(e, cuerpo().get("clave", "")):
            fallos += 1
            intentos_enlace[clave_intentos] = (fallos, time.time() + 60 if fallos >= 6 else 0)
            raise ErrorNube("Esa no es la contraseña.", 401)
        intentos_enlace.pop(clave_intentos, None)
        session[f"e{e['id']}"] = True
        return jsonify(ok=True)

    @app.get("/api/e/<token>/bajar")
    def bajar_enlace(token):
        e, raiz = _enlace(token)
        if e["permiso"] != "descargar":
            raise ErrorNube("Este enlace es para mandar archivos, no para bajarlos.", 403)
        n = nube.dentro_de(raiz, request.args.get("id", type=int))
        if n["tipo"] == "carpeta":
            return _enviar_zip(n["nombre"] + ".zip", [n], raiz["propietario_id"], "enlace", enlace_id=e["id"])
        return _enviar_archivo(n, raiz["propietario_id"], "enlace", en_linea=request.args.get("ver") == "1",
                               enlace_id=e["id"])

    @app.put("/api/e/<token>/subir")
    def subir_enlace(token):
        e, carpeta = _enlace(token)
        if e["permiso"] != "subir":
            raise ErrorNube("Este enlace no deja subir archivos.", 403)
        tam = request.content_length
        if tam is None:
            raise ErrorNube("Falta Content-Length.", 411)
        dueno = carpeta["propietario_id"]
        ocupados = {h["nombre"] for h in nube._hijos(dueno, carpeta["id"])}
        partes = partes_ruta(request.args.get("nombre", ""))
        if not partes:
            raise ErrorNube("Falta el nombre del archivo.")
        nombre = nombre_libre(partes[-1], ocupados)  # en un buzón nadie pisa lo de otro
        nube.comprobar_espacio(dueno, tam)
        inicio = time.time()
        try:
            with reg.turno(dueno, nombre, "subida", "enlace", tam, origen="enlace público") as t:
                huella = nube.almacen.guardar_flujo(request.stream.read, tam, lambda n: reg.avanzar(t, n),
                                                    cancelada=t.cancelar.is_set)
        except Cancelada:
            raise ErrorNube("Subida cancelada.", 499)
        except (ConnectionError, ClientDisconnected):
            raise ErrorNube("La subida se cortó a mitad.", 400)
        nube.registrar_archivo(dueno, carpeta["id"], nombre, huella, tam)
        nube.apuntar(dueno, "subida", "enlace", nombre, tam, (time.time() - inicio) * 1000)
        nube.avisos.enviar([dueno], "buzon", {"nombre": nombre, "carpeta": carpeta["nombre"]})
        return jsonify(ok=True, nombre=nombre), 201

    # ------------------------------------------- transferencias y avisos

    @app.get("/api/transferencias")
    def transferencias():
        return jsonify(reg.de(yo()["id"]))

    @app.post("/api/transferencias/<id_>/cancelar")
    def cancelar(id_):
        return jsonify(ok=reg.cancelar(yo()["id"], id_))

    @app.get("/api/eventos")
    def eventos():
        """Server-Sent Events: una conexión abierta por pestaña, alimentada por su cola."""
        u = yo()
        cola = nube.avisos.suscribir(u["id"])

        def generar():
            try:
                yield f"event: hola\ndata: {json.dumps({'transferencias': reg.de(u['id'])})}\n\n"
                while True:
                    try:
                        tipo, datos = cola.get(timeout=20)
                    except queue.Empty:
                        yield ": sigo aquí\n\n"  # latido: así el proxy no corta y se nota si la pestaña se fue
                        continue
                    yield f"event: {tipo}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"
            finally:
                nube.avisos.baja(u["id"], cola)

        return Response(generar(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/actividad")
    def actividad():
        return jsonify(nube.actividad(yo()["id"]))

    # ---------------------------------------------------------- equipos

    def _servidor_equipos():
        if equipos is None:
            return None
        host = request.host.split(":")[0]
        return {"host": host, "puerto": equipos.puerto, "huella": equipos.huella, "tls": equipos.huella is not None}

    @app.get("/api/equipos")
    def lista_equipos():
        u = yo()
        conectados = {}
        if equipos:
            for c in equipos.conexiones():
                if c["usuario"] == u["nombre"]:
                    conectados[c["equipo"]] = conectados.get(c["equipo"], 0) + 1
        filas = nube.equipos(u["id"])
        for f in filas:
            f["conexiones"] = conectados.get(f["nombre"], 0)
        return jsonify(equipos=filas, servidor=_servidor_equipos())

    @app.post("/api/equipos")
    def nuevo_equipo():
        u = yo()
        token, fila = nube.crear_equipo(u["id"], cuerpo().get("nombre", ""))
        s = _servidor_equipos() or {}
        orden = f"python3 -m scloud conectar {token} --servidor {s.get('host', 'localhost')} --puerto {s.get('puerto', 8105)}"
        if s.get("huella"):
            orden += f" --huella {s['huella']}"
        else:
            orden += " --sin-tls"
        return jsonify(token=token, orden=orden, **fila), 201

    @app.delete("/api/equipos/<int:id_>")
    def quitar_equipo(id_):
        nube.quitar_equipo(yo()["id"], id_)
        return jsonify(ok=True)

    @app.get("/api/sala")
    def sala():
        """La sala de máquinas: hilos vivos, conexiones y colas, en directo."""
        u = yo()
        hilos = {}
        for h in threading.enumerate():
            if "process_request" in h.name:
                tipo = "petición web"  # los hilos que crea Werkzeug (ThreadingMixIn), uno por petición
            elif h.name == "MainThread":
                tipo = "principal"
            else:
                tipo = re.sub(r"[-_ ]?\d+.*$", "", h.name) or h.name
            hilos[tipo] = hilos.get(tipo, 0) + 1
        conexiones = equipos.conexiones() if equipos else []
        if u["rol"] != "admin":
            conexiones = [c for c in conexiones if c["usuario"] == u["nombre"]]
            for c in conexiones:
                c.pop("direccion", None)
        return jsonify(hilos=sorted(hilos.items(), key=lambda x: -x[1]), total_hilos=threading.active_count(),
                       conexiones=conexiones, transferencias=reg.resumen(), pestanas=nube.avisos.pestanas(),
                       ordenes_bd=nube.bd.ordenes, espera_bd_ms=round(nube.bd.espera_total * 1000),
                       atendidas=equipos.atendidas if equipos else 0)

    return app


# ------------------------------------------------------------------ ayudas

class _Tubo(io.RawIOBase):
    """Un fichero de solo escritura que guarda lo escrito hasta que alguien lo saca."""

    def __init__(self):
        self._trozos, self._pos = [], 0

    def writable(self):
        return True

    def seekable(self):
        return False

    def write(self, b):
        self._trozos.append(bytes(b))
        self._pos += len(b)
        return len(b)

    def tell(self):
        return self._pos

    def flush(self):
        pass

    def sacar(self):
        datos = b"".join(self._trozos)
        self._trozos = []
        return datos


def _fecha_zip(ms):
    t = time.localtime(max(ms / 1000, 315532800))  # el ZIP no conoce fechas antes de 1980
    return t[:6]


def _disposicion(tipo, nombre):
    from urllib.parse import quote
    ascii_ = nombre.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
    return f"{tipo}; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre)}"


def _clave_sesion(datos):
    ruta = Path(datos) / "clave-sesion"
    if not ruta.exists():
        ruta.write_bytes(secrets.token_bytes(32))
        ruta.chmod(0o600)
    return ruta.read_bytes()


mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")
