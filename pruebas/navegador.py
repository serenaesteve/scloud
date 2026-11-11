"""Recorrido con un navegador de verdad (Chromium, con Playwright).

    /root/life-dashboard/.venv/bin/python pruebas/navegador.py [--capturas DIR]

Arranca un scloud temporal, crea dos cuentas y hace lo que haría una
persona: subir archivos y una carpeta entera, crear carpetas, compartir,
crear un enlace con contraseña, abrirlo sin sesión, mandar archivos a un
buzón, recibir en directo lo que sube un equipo por sockets, mover, mandar a
la papelera y recuperar. Falla si algo no aparece o si la consola del
navegador da errores.
"""

import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def png(ruta, ancho=320, alto=200, color=(170, 190, 245)):
    filas = b"".join(b"\x00" + bytes(color) * ancho for _ in range(alto))
    def trozo(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))
    ruta.write_bytes(b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
                     + trozo(b"IDAT", zlib.compress(filas)) + trozo(b"IEND", b""))


def api(base, ruta, datos=None, metodo=None, cookie=None):
    req = urllib.request.Request(base + ruta, method=metodo or ("POST" if datos is not None else "GET"),
                                 data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Content-Type": "application/json", "X-Scloud": "1", **({"Cookie": cookie} if cookie else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()), r.headers.get("Set-Cookie", "").split(";")[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--capturas", help="carpeta donde guardar capturas")
    a = p.parse_args()
    capturas = Path(a.capturas) if a.capturas else None
    if capturas:
        capturas.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="scloud-nav-"))
    pw, pe = puerto_libre(), puerto_libre()
    servidor = subprocess.Popen([sys.executable, "-m", "scloud", "servir", "--datos", str(tmp / "datos"), "--puerto-web", str(pw),
                                 "--puerto-equipos", str(pe), "--host-equipos", "127.0.0.1"], cwd=RAIZ,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{pw}"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/").close()
            break
        except OSError:
            time.sleep(.1)

    # Archivos de ejemplo
    muestras = tmp / "muestras"
    (muestras / "Lisboa" / "dia 2").mkdir(parents=True)
    png(muestras / "tranvia.png")
    png(muestras / "Lisboa" / "miradouro.png", color=(250, 200, 170))
    (muestras / "Lisboa" / "dia 2" / "notas.txt").write_text("Pastéis de Belém a las 10.\nLuego, Alfama.\n")
    (muestras / "presupuesto.csv").write_text("concepto;euros\nvuelo;84\nhotel;210\n")
    (muestras / "factura-marzo.csv").write_text("cliente;importe\nlibreria;42\n")
    (muestras / "grande.bin").write_bytes(os.urandom(3 * 1024 * 1024))

    errores = []
    hecho = []
    try:
        with sync_playwright() as play:
            nav = play.chromium.launch()
            ctx = nav.new_context(viewport={"width": 1440, "height": 900}, locale="es-ES", accept_downloads=True)
            pag = ctx.new_page()
            pag.on("console", lambda m: m.type == "error" and errores.append(m.text))
            pag.on("pageerror", lambda e: errores.append(str(e)))
            # Los avisos rojos de la propia app también cuentan como fallo
            ctx.add_init_script("""new MutationObserver(() => document.querySelectorAll('.aviso.error').forEach((a) => {
                if (!a.dataset.visto) { a.dataset.visto = 1; console.error('[aviso rojo] ' + a.textContent); } }))
              .observe(document, { childList: true, subtree: true });""")

            def foto(nombre, pagina=None, completa=False):
                if capturas:
                    (pagina or pag).screenshot(path=str(capturas / f"{nombre}.png"), full_page=completa)

            # ---------------------------------------------------- crear cuenta
            pag.goto(base + "/")
            expect(pag.locator("#acceso")).to_be_visible()
            foto("01-acceso")
            pag.click('button[data-modo="registro"]')
            pag.fill('[name="nombre"]', "Ana López")
            pag.fill('[name="usuario"]', "ana")
            pag.fill('[name="clave"]', "contraseña-ana")
            pag.click('#form-acceso button[type="submit"]')
            expect(pag.locator("#app")).to_be_visible()
            expect(pag.locator("#titulo")).to_have_text("Mi nube")
            foto("02-nube-vacia")
            hecho.append("crear cuenta")

            # ------------------------------------------------------ subir
            pag.set_input_files("#elegir-archivos", [str(muestras / "tranvia.png"), str(muestras / "presupuesto.csv"),
                                                     str(muestras / "grande.bin")])
            expect(pag.locator(".fila", has_text="grande.bin")).to_be_visible(timeout=15000)
            expect(pag.locator(".fila", has_text="presupuesto.csv")).to_be_visible()
            expect(pag.locator("#lluvia-titulo")).to_have_text("Todo listo", timeout=15000)
            foto("03-subidos")
            pag.set_input_files("#elegir-carpeta", str(muestras / "Lisboa"))
            expect(pag.locator(".fila", has_text="Lisboa")).to_be_visible(timeout=15000)
            hecho.append("subir archivos y una carpeta entera")

            # ------------------------------------------------ nueva carpeta
            pag.click('[data-accion="carpeta"]')
            pag.fill("#form-texto input", "Facturas")
            pag.click('#dialogo button[type="submit"]')
            expect(pag.locator(".fila", has_text="Facturas")).to_be_visible()
            hecho.append("nueva carpeta")

            # ------------------------------------- entrar en una carpeta
            pag.locator(".fila", has_text="Lisboa").locator(".abrir").click()
            expect(pag.locator("#titulo")).to_have_text("Lisboa")
            expect(pag.locator(".fila", has_text="dia 2")).to_be_visible()
            expect(pag.locator(".fila", has_text="miradouro.png")).to_be_visible()
            pag.locator(".fila", has_text="dia 2").locator(".abrir").click()
            expect(pag.locator(".fila", has_text="notas.txt")).to_be_visible()
            pag.locator(".fila", has_text="notas.txt").locator(".abrir").click()
            expect(pag.locator(".vista-previa pre")).to_contain_text("Pastéis de Belém")
            foto("04-vista-previa")
            pag.keyboard.press("Escape")
            expect(pag.locator("#dialogo")).not_to_be_visible()
            pag.click("#migas a >> text=Mi nube")
            expect(pag.locator("#titulo")).to_have_text("Mi nube")
            hecho.append("navegar y ver un texto")

            # ------------------------------------------------- compartir
            api(base, "/api/registro", {"usuario": "beto", "nombre": "Beto Ruiz", "clave": "contraseña-beto"})
            pag.locator(".fila", has_text="Lisboa").locator(".mas-acciones").click()
            foto("05-menu")
            pag.click('#menu-nodo [data-op="compartir"]')
            pag.fill("#dialogo .entrada", "bet")
            expect(pag.locator(".sugerencias button", has_text="Beto Ruiz")).to_be_visible()
            pag.click(".sugerencias button")
            pag.select_option('#dialogo .agregar select', "editar")
            pag.click('#dialogo .agregar button[type="submit"]')
            expect(pag.locator(".persona", has_text="Beto Ruiz")).to_be_visible()
            hecho.append("compartir con otra cuenta")

            # --------------------------------------------- enlace con clave
            pag.click(".crear-enlace summary")
            pag.fill('.opciones-enlace [name="clave"]', "lisboa")
            pag.click('.opciones-enlace button[type="submit"]')
            url_enlace = pag.locator(".resultado-enlace input").input_value()
            assert "/e/" in url_enlace, url_enlace
            foto("06-compartir")
            pag.keyboard.press("Escape")
            expect(pag.locator(".fila", has_text="Lisboa").locator(".marcas svg")).to_have_count(2)
            hecho.append("crear un enlace con contraseña")

            # ------------------------------ abrir el enlace sin sesión
            anonimo = nav.new_context(viewport={"width": 390, "height": 844}, locale="es-ES", is_mobile=True, has_touch=True)
            otra = anonimo.new_page()
            otra.on("console", # el 401 es el de la contraseña equivocada que la prueba escribe a propósito
            lambda m: m.type == "error" and "401" not in m.text and errores.append("[enlace] " + m.text))
            otra.goto(url_enlace)
            expect(otra.locator("#form-clave")).to_be_visible()
            otra.fill('#form-clave input', "mal")
            otra.click('#form-clave button')
            expect(otra.locator(".error-form")).to_contain_text("no es la contraseña")
            otra.fill('#form-clave input', "lisboa")
            otra.click('#form-clave button')
            expect(otra.locator(".fila", has_text="miradouro.png")).to_be_visible()
            foto("07-enlace-movil", otra)
            with otra.expect_download() as descarga:
                otra.click(".publico-acciones a")
            assert descarga.value.suggested_filename == "Lisboa.zip"
            hecho.append("abrir el enlace sin cuenta y bajar el ZIP")

            # ------------------------------------------------- buzón
            pag.locator(".fila", has_text="Facturas").locator(".mas-acciones").click()
            pag.click('#menu-nodo [data-op="compartir"]')
            pag.click(".crear-enlace summary")
            pag.select_option('.opciones-enlace [name="permiso"]', "subir")
            pag.click('.opciones-enlace button[type="submit"]')
            url_buzon = pag.locator(".resultado-enlace input").input_value()
            pag.keyboard.press("Escape")
            otra.goto(url_buzon)
            expect(otra.locator("#buzon")).to_be_visible()
            otra.set_input_files("#elegir", str(muestras / "factura-marzo.csv"))
            expect(otra.locator(".envios small")).to_contain_text("entregado")
            foto("08-buzon-movil", otra)
            expect(pag.locator(".aviso", has_text="Te han mandado")).to_be_visible()
            hecho.append("mandar un archivo a un buzón (y el aviso en directo)")
            anonimo.close()

            # ------------------------------------ lo que sube un equipo, en directo
            r, cookie = api(base, "/api/entrar", {"usuario": "ana", "clave": "contraseña-ana"})
            equipo, _ = api(base, "/api/equipos", {"nombre": "portátil"}, cookie=cookie)
            env = {**os.environ, "SCLOUD_CUENTA": str(tmp / "cuenta.json")}
            orden = equipo["orden"].split()
            orden[orden.index("--puerto") + 1] = str(pe)
            orden[orden.index("--servidor") + 1] = "127.0.0.1"
            subprocess.run([sys.executable] + orden[1:], cwd=RAIZ, env=env, check=True, capture_output=True)
            (muestras / "desde-el-portatil.txt").write_text("hola desde el equipo")
            subprocess.run([sys.executable, "-m", "scloud", "subir", str(muestras / "desde-el-portatil.txt")],
                           cwd=RAIZ, env=env, check=True, capture_output=True)
            fila = pag.locator(".fila", has_text="desde-el-portatil.txt")
            expect(fila).to_be_visible(timeout=5000)
            expect(fila).to_have_class("fila llega")
            hecho.append("ver en directo lo que sube un equipo por sockets")

            # --------------------------------------- mover y papelera
            pag.locator(".fila", has_text="presupuesto.csv").first.locator(".casilla").check(force=True)
            pag.locator(".fila", has_text="tranvia.png").locator(".casilla").check(force=True)
            expect(pag.locator("#seleccion")).to_be_visible()
            foto("09-seleccion")
            pag.click('[data-sel="mover"]')
            pag.click('.carpetas-mover button:has-text("Facturas")')
            pag.click("[data-aqui]")
            expect(pag.locator(".fila", has_text="tranvia.png")).to_have_count(0)
            pag.locator(".fila", has_text="grande.bin").locator(".mas-acciones").click()
            pag.click('#menu-nodo [data-op="papelera"]')
            expect(pag.locator(".fila", has_text="grande.bin")).to_have_count(0)
            pag.click('.aviso button:has-text("Deshacer")')
            expect(pag.locator(".fila", has_text="grande.bin")).to_be_visible()
            hecho.append("mover dos archivos y deshacer un borrado")

            # ------------------------------------ otras vistas y temas
            pag.click('.espacios a[data-vista="equipos"]')
            expect(pag.locator(".equipo", has_text="portátil")).to_be_visible()
            expect(pag.locator("#sala-cifras .cifra").first).to_be_visible()
            foto("10-equipos", completa=True)
            pag.click('.espacios a[data-vista="enlaces"]')
            expect(pag.locator(".fila")).to_have_count(2)
            pag.click('.espacios a[data-vista="nube"]')
            pag.click('[data-accion="modo"]')
            expect(pag.locator(".cuadricula")).to_be_visible()
            pag.wait_for_timeout(400)
            foto("11-cuadricula")
            pag.click('[data-accion="modo"]')
            pag.emulate_media(color_scheme="dark")
            pag.wait_for_timeout(300)
            foto("12-oscuro")
            pag.emulate_media(color_scheme="light")
            hecho.append("equipos, sala de máquinas, enlaces y cuadrícula")

            # --------------------------------- la otra cuenta ve lo compartido
            beto = nav.new_context(viewport={"width": 390, "height": 844}, locale="es-ES", is_mobile=True, has_touch=True)
            bp = beto.new_page()
            bp.on("pageerror", lambda e: errores.append("[beto] " + str(e)))
            bp.goto(base + "/")
            bp.fill('[name="usuario"]', "beto")
            bp.fill('[name="clave"]', "contraseña-beto")
            bp.click('#form-acceso button[type="submit"]')
            bp.click('.espacios a[data-vista="compartido"]')
            expect(bp.locator(".fila", has_text="Lisboa")).to_be_visible()
            bp.locator(".fila", has_text="Lisboa").locator(".abrir").click()
            expect(bp.locator("#nota")).to_contain_text("de Ana López")
            foto("13-compartido-movil", bp)
            bp.set_input_files("#elegir-archivos", str(muestras / "tranvia.png"))
            expect(bp.locator(".fila", has_text="tranvia.png")).to_be_visible(timeout=10000)
            hecho.append("la otra cuenta abre la carpeta y sube algo (permiso de edición)")
            beto.close()

            nav.close()
    finally:
        servidor.terminate()
        salida = servidor.communicate(timeout=10)[0]
        shutil.rmtree(tmp, ignore_errors=True)  # cuentas y archivos de prueba: no se dejan en /tmp

    for h in hecho:
        print(f"✓ {h}")
    if errores:
        print("\nErrores en la consola del navegador:")
        for e in errores:
            print("  ✗", e)
        print("\n--- servidor ---\n" + salida[-3000:])
        sys.exit(1)
    print(f"\n{len(hecho)} pasos sin errores en la consola.")


if __name__ == "__main__":
    main()
