"""python3 -m scloud <orden>

Servidor:
  servir                    arranca la web (Flask) y el servidor de equipos (sockets)
  usuario NOMBRE            crea una cuenta desde la consola (con el servidor parado)

Equipo (cliente de escritorio):
  entrar                    conecta este equipo con usuario y contraseña
  conectar TOKEN            conecta este equipo con el token que da la web
  ls [RUTA]                 lista una carpeta de tu nube
  subir LOCAL… [--a RUTA]   sube archivos y carpetas, varios a la vez
  bajar RUTA… [--a DIR]     baja archivos y carpetas, varios a la vez
  sincronizar DIR           mantiene una carpeta igual en el equipo y en la nube
"""

import argparse
import getpass
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .protocolo import PUERTO


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m scloud", description="scloud, tu nube privada.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="orden", required=True)

    s = sub.add_parser("servir", help="arranca el servidor")
    s.add_argument("--datos", default="datos")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--puerto-web", type=int, default=8104)
    s.add_argument("--host-equipos", default="0.0.0.0")
    s.add_argument("--puerto-equipos", type=int, default=PUERTO)
    s.add_argument("--sin-tls", action="store_true", help="canal de equipos sin cifrar (solo para depurar)")
    s.add_argument("--cuota", type=float, default=5, help="GB por cuenta nueva")
    s.add_argument("--registro", choices=["abierto", "cerrado"], default="abierto")
    s.add_argument("--reserva", type=float, default=20, help="GB del disco que scloud nunca ocupa")
    s.add_argument("--publico", action="store_true", help="detrás de un proxy HTTPS: cookies Secure y X-Forwarded-For")
    s.add_argument("--ver-peticiones", action="store_true", help="escribe cada petición web en la salida")

    u = sub.add_parser("usuario", help="crea una cuenta desde la consola")
    u.add_argument("nombre")
    u.add_argument("--visible", default="")
    u.add_argument("--admin", action="store_true")
    u.add_argument("--datos", default="datos")

    def conexion(a, token=False):
        a.add_argument("--servidor", default="127.0.0.1")
        a.add_argument("--puerto", type=int, default=PUERTO)
        a.add_argument("--huella", help="huella del certificado que enseña la web (recomendado)")
        a.add_argument("--sin-tls", action="store_true")
        a.add_argument("--equipo", default=os.uname().nodename)

    e = sub.add_parser("entrar", help="conecta este equipo con usuario y contraseña")
    e.add_argument("--usuario")
    conexion(e)
    c = sub.add_parser("conectar", help="conecta este equipo con el token de la web")
    c.add_argument("token")
    conexion(c)

    ls = sub.add_parser("ls", help="lista una carpeta de la nube")
    ls.add_argument("ruta", nargs="?", default="/")

    su = sub.add_parser("subir", help="sube archivos o carpetas")
    su.add_argument("locales", nargs="+")
    su.add_argument("--a", default="/", help="carpeta de la nube (por defecto, la raíz)")
    su.add_argument("--hilos", type=int, default=4)

    ba = sub.add_parser("bajar", help="baja archivos o carpetas")
    ba.add_argument("remotas", nargs="+")
    ba.add_argument("--a", default=".", help="carpeta del equipo")
    ba.add_argument("--hilos", type=int, default=4)

    si = sub.add_parser("sincronizar", help="sincroniza una carpeta")
    si.add_argument("local")
    si.add_argument("--remota", help="carpeta de la nube (por defecto, una con el mismo nombre)")
    si.add_argument("--una-vez", action="store_true", help="una sola pasada, sin quedarse vigilando")
    si.add_argument("--hilos", type=int, default=4)
    si.add_argument("--intervalo", type=float, default=2.0)

    a = p.parse_args(argv)
    return globals()["orden_" + a.orden](a)


# ------------------------------------------------------------------ servidor

def orden_servir(a):
    import logging

    from werkzeug.serving import make_server

    from .nube import GB, Nube
    from .servidor_equipos import ServidorEquipos
    from .web import crear_app

    if not a.ver_peticiones:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    datos = Path(a.datos).resolve()
    nube = Nube(datos, cuota=int(a.cuota * GB), registro_abierto=a.registro == "abierto",
                reserva_disco=int(a.reserva * GB))
    equipos = ServidorEquipos(nube, a.host_equipos, a.puerto_equipos,
                              carpeta_tls=None if a.sin_tls else datos / "tls").arrancar()
    app = crear_app(nube, equipos, publico=a.publico)
    web = make_server(a.host, a.puerto_web, app, threaded=True)  # un hilo por petición
    web.daemon_threads = True

    parar = threading.Event()

    def barrendero():
        """Cada hora: papelera vieja, bloques sin dueño y temporales abandonados."""
        while not parar.wait(3600):
            try:
                r = nube.barrer()
                if any(r.values()):
                    print(f"[barrendero] {r}", flush=True)
            except Exception as e:  # que un fallo no mate el hilo
                print(f"[barrendero] error: {e}", file=sys.stderr, flush=True)

    threading.Thread(target=barrendero, name="barrendero", daemon=True).start()

    def al_parar(*_):
        parar.set()
        threading.Thread(target=web.shutdown, name="apagado", daemon=True).start()

    signal.signal(signal.SIGTERM, al_parar)
    signal.signal(signal.SIGINT, al_parar)

    print(f"scloud · web en http://{a.host}:{a.puerto_web} · equipos en {a.host_equipos}:{equipos.puerto}"
          f" ({'TLS ' + equipos.huella if equipos.huella else 'sin TLS'})", flush=True)
    try:
        web.serve_forever(poll_interval=0.5)
    finally:
        parar.set()
        equipos.parar()
        nube.cerrar()
        print("scloud parado.", flush=True)


def orden_usuario(a):
    from .nube import Nube
    nube = Nube(Path(a.datos).resolve())
    clave = getpass.getpass("Contraseña: ")
    if clave != getpass.getpass("Otra vez: "):
        raise SystemExit("No coinciden.")
    u = nube.crear_usuario(a.nombre, a.visible, clave, rol="admin" if a.admin else "usuario")
    nube.cerrar()
    print(f"Creada la cuenta «{u['nombre']}».")


# -------------------------------------------------------------------- equipo

def orden_entrar(a):
    from .cliente import entrar
    usuario = a.usuario or input("Usuario: ")
    clave = getpass.getpass("Contraseña: ")
    cuenta, r = entrar(a.servidor, a.puerto, usuario, clave, a.equipo, a.huella, not a.sin_tls)
    _saludo(cuenta, r, a.huella)


def orden_conectar(a):
    from .cliente import conectar_token
    cuenta, r = conectar_token(a.servidor, a.puerto, a.token, a.huella, not a.sin_tls)
    _saludo(cuenta, r, a.huella)


def _saludo(cuenta, r, huella_dada):
    print(f"Conectado como {r['visible']} ({r['usuario']}). Este equipo se llama «{r['equipo']}».")
    if cuenta.tls and not huella_dada:
        print(f"Huella del servidor: {cuenta.huella}\n"
              "Compárala con la que sale en la web (Equipos). Si un día cambia, el cliente se negará a conectar.")
    print(f"Datos guardados en {cuenta.ruta}")


def _conexion():
    from .cliente import Conexion, Cuenta
    con = Conexion(Cuenta.cargar())
    con.hola()
    return con


def orden_ls(a):
    from .cliente import legible
    con = _conexion()
    try:
        hijos = con.orden("listar", ruta=a.ruta)["hijos"]
        espacio = con.orden("espacio")
    finally:
        con.cerrar()
    for h in hijos:
        fecha = time.strftime("%d/%m/%Y %H:%M", time.localtime(h["modificado"] / 1000))
        if h["tipo"] == "carpeta":
            print(f"  {'carpeta':>10}  {fecha}  {h['nombre']}/")
        else:
            print(f"  {legible(h['tamano']):>10}  {fecha}  {h['nombre']}")
    print(f"{len(hijos)} elementos · usas {legible(espacio['usado'])} de {legible(espacio['cuota'])}")


def orden_subir(a):
    from .cliente import Conexion, Cuenta, Lote
    from .sincro import ignorado, unir
    cuenta = Cuenta.cargar()
    lote = Lote(cuenta, hilos=a.hilos)
    carpetas = []
    for local in map(Path, a.locales):
        if local.is_dir():
            base = local.resolve().parent
            carpetas.append(unir(a.a, local.resolve().name))
            for raiz, dirs, ficheros in os.walk(local):
                dirs[:] = [d for d in dirs if not ignorado(d)]
                for d in dirs:
                    carpetas.append(unir(a.a, Path(raiz, d).resolve().relative_to(base).as_posix()))
                for f in ficheros:
                    if not ignorado(f):
                        ruta = Path(raiz, f)
                        lote.subir(ruta, unir(a.a, ruta.resolve().relative_to(base).as_posix()))
        elif local.is_file():
            lote.subir(local, unir(a.a, local.name))
        else:
            print(f"✗ no existe: {local}", file=sys.stderr)
    if carpetas:  # también las vacías
        con = Conexion(cuenta)
        con.hola()
        for c in carpetas:
            con.orden("carpeta", ruta=c)
        con.cerrar()
    _resultado(lote.ejecutar())


def orden_bajar(a):
    from .cliente import Conexion, Cuenta, Lote
    from .protocolo import ErrorRemoto
    from .sincro import unir
    cuenta = Cuenta.cargar()
    lote = Lote(cuenta, hilos=a.hilos)
    con = Conexion(cuenta)
    con.hola()
    try:
        for remota in a.remotas:
            remota = "/" + unir(remota)
            nombre = remota.rstrip("/").split("/")[-1]
            try:
                arbol = con.orden("arbol", ruta=remota)["entradas"]
            except ErrorRemoto:
                arbol = None  # no es una carpeta: es un archivo
            if arbol is None:
                lote.bajar(remota, Path(a.a) / nombre)
                continue
            (Path(a.a) / nombre).mkdir(parents=True, exist_ok=True)
            for e in arbol:
                destino = Path(a.a) / nombre / e["ruta"]
                if e["tipo"] == "carpeta":
                    destino.mkdir(parents=True, exist_ok=True)
                else:
                    lote.bajar(unir(remota, e["ruta"]), destino, e["tam"])
    finally:
        con.cerrar()
    _resultado(lote.ejecutar())


def _resultado(trabajos):
    errores = [t for t in trabajos if t.estado != "hecha"]
    if errores:
        sys.exit(1)


def orden_sincronizar(a):
    from .cliente import Cuenta
    from .sincro import Sincronizador
    cuenta = Cuenta.cargar()
    local = Path(a.local).resolve()
    remota = a.remota or "/" + local.name

    def avisar(texto):
        print(f"{time.strftime('%H:%M:%S')}  {texto}", flush=True)

    s = Sincronizador(cuenta, local, remota, hilos=a.hilos, avisar=avisar, intervalo=a.intervalo)
    if a.una_vez:
        r = s.ronda()
        avisar("hecho · " + (", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in r.items() if v) or "ya estaba igual"))
        return
    avisar(f"sincronizando {local} ⇄ nube:{s.remota}  (Ctrl+C para parar)")
    try:
        s.vigilar()
    except KeyboardInterrupt:
        s.parar.set()
        avisar("parado")


if __name__ == "__main__":
    sys.exit(main())
