"""El servidor de sockets (SCL/1 con TLS), el cliente, la sincronización y la web (Flask)."""

import hashlib
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from scloud import cliente
from scloud.cliente import Conexion, Cuenta, Lote
from scloud.nube import Nube
from scloud.protocolo import ErrorRemoto
from scloud.servidor_equipos import ServidorEquipos
from scloud.sincro import Sincronizador
from scloud.web import crear_app


class ConServidor(unittest.TestCase):
    """Un scloud entero en un puerto libre, con TLS."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.nube = Nube(self.tmp / "datos")
        self.nube.crear_usuario("ana", "Ana", "contraseña-1")
        self.servidor = ServidorEquipos(self.nube, "127.0.0.1", 0, carpeta_tls=self.tmp / "datos" / "tls").arrancar()
        self.cuenta, _ = cliente.entrar("127.0.0.1", self.servidor.puerto, "ana", "contraseña-1", "pruebas",
                                        ruta=self.tmp / "cuenta.json")

    def tearDown(self):
        self.servidor.parar()
        self.nube.cerrar()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def conexion(self):
        con = Conexion(self.cuenta)
        con.hola()
        return con

    def archivo(self, nombre, datos):
        ruta = self.tmp / "locales" / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)
        return ruta


class Sockets(ConServidor):
    def test_la_huella_del_certificado_se_fija(self):
        self.assertEqual(self.cuenta.huella, self.servidor.huella)
        falsa = Cuenta("127.0.0.1", self.servidor.puerto, self.cuenta.token, "AA:" * 31 + "AA", ruta=self.tmp / "x.json")
        with self.assertRaises(ConnectionError):
            Conexion(falsa)

    def test_sin_saludar_no_se_puede(self):
        con = Conexion(self.cuenta)
        with self.assertRaises(ErrorRemoto) as e:
            con.orden("listar", ruta="/")
        self.assertEqual(e.exception.codigo, 401)
        con.cerrar()

    def test_token_quitado_ya_no_entra(self):
        uid = self.nube.usuario_por_nombre("ana")["id"]
        for e in self.nube.equipos(uid):
            self.nube.quitar_equipo(uid, e["id"])
        with self.assertRaises(ErrorRemoto):
            self.conexion()

    def test_subir_y_bajar(self):
        datos = os.urandom(700_000)
        con = self.conexion()
        r = con.subir(self.archivo("a.bin", datos), "Carpeta/a.bin")
        self.assertEqual(r["version"], 1)
        con.bajar("Carpeta/a.bin", self.tmp / "bajado.bin")
        self.assertEqual((self.tmp / "bajado.bin").read_bytes(), datos)
        # Otra vez lo mismo: no se manda nada
        self.assertTrue(con.subir(self.archivo("copia.bin", datos), "otra.bin")["instantanea"])
        con.cerrar()

    def test_subida_reanudada(self):
        """Se corta a mitad; la siguiente vez el servidor dice desde dónde seguir."""
        datos = os.urandom(1_000_000)
        local = self.archivo("r.bin", datos)
        huella = hashlib.sha256(datos).hexdigest()
        con = self.conexion()
        r = con.orden("subir", ruta="r.bin", tam=len(datos), huella=huella, mod=0)
        self.assertEqual(r["desde"], 0)
        con.canal.enviar_bytes(datos[:400_000])
        con.canal.cerrar()  # ¡corte!
        time.sleep(0.3)
        con = self.conexion()
        r = con.subir(local, "r.bin")
        self.assertTrue(r["reanudada"])
        con.bajar("r.bin", self.tmp / "r2.bin")
        self.assertEqual((self.tmp / "r2.bin").read_bytes(), datos)
        con.cerrar()

    def test_huella_falsa_se_rechaza(self):
        con = self.conexion()
        r = con.orden("subir", ruta="m.bin", tam=5, huella="0" * 64, mod=0)
        con.canal.enviar_bytes(b"hola!")
        with self.assertRaises(ErrorRemoto) as e:
            con.canal.respuesta()
        self.assertEqual(e.exception.codigo, 422)
        # La conexión sigue sirviendo
        self.assertEqual(con.orden("listar", ruta="/")["hijos"], [])
        con.cerrar()

    def test_lote_en_paralelo(self):
        lote = Lote(self.cuenta, hilos=4, pintar=False, salida=io.StringIO())
        esperados = {}
        for i in range(12):
            datos = os.urandom(50_000 + i)
            esperados[f"lote/{i}.bin"] = datos
            lote.subir(self.archivo(f"{i}.bin", datos), f"lote/{i}.bin")
        trabajos = lote.ejecutar()
        self.assertTrue(all(t.estado == "hecha" for t in trabajos))
        arbol = {e["ruta"]: e["huella"] for e in self.conexion().orden("arbol", ruta="lote")["entradas"]}
        self.assertEqual(arbol, {k.split("/")[1]: hashlib.sha256(v).hexdigest() for k, v in esperados.items()})

    def test_conflicto_si_cambio_en_la_nube(self):
        con = self.conexion()
        con.subir(self.archivo("c1", b"version 1"), "c.txt")
        con.subir(self.archivo("c2", b"version 2"), "c.txt")
        with self.assertRaises(ErrorRemoto) as e:
            con.subir(self.archivo("c3", b"version 3"), "c.txt", base=hashlib.sha256(b"version 1").hexdigest())
        self.assertEqual(e.exception.codigo, 409)
        con.cerrar()

    def test_esperar_se_despierta(self):
        vigia = self.conexion()
        gen = vigia.hola()["gen"]
        resultado = []
        h = threading.Thread(target=lambda: resultado.append(vigia.orden("esperar", gen=gen, segundos=10)["gen"]))
        h.start()
        time.sleep(0.2)
        self.conexion().orden("carpeta", ruta="Nueva")
        h.join(3)
        self.assertNotEqual(resultado[0], gen)
        vigia.cerrar()

    def test_muchos_clientes_a_la_vez(self):
        """Un hilo por conexión: 20 clientes suben a la vez y todos terminan."""
        errores = []

        def uno(i):
            try:
                con = self.conexion()
                con.subir(self.archivo(f"m{i}", os.urandom(30_000)), f"multi/{i}.bin")
                con.cerrar()
            except Exception as e:
                errores.append(e)

        hilos = [threading.Thread(target=uno, args=(i,)) for i in range(20)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(30)
        self.assertEqual(errores, [])
        self.assertEqual(len(self.conexion().orden("listar", ruta="multi")["hijos"]), 20)


class Sincronizacion(ConServidor):
    def test_dos_equipos_y_un_conflicto(self):
        uno, dos = self.tmp / "uno", self.tmp / "dos"
        s1 = Sincronizador(self.cuenta, uno, "/Sinc", avisar=lambda *_: None, equipo="uno")
        s2 = Sincronizador(self.cuenta, dos, "/Sinc", avisar=lambda *_: None, equipo="dos")
        (uno / "sub").mkdir(parents=True)
        (uno / "nota.txt").write_text("hola")
        (uno / "sub" / "x.txt").write_text("x")
        (uno / "vacia").mkdir()
        s1.ronda()
        s2.ronda()
        self.assertEqual((dos / "nota.txt").read_text(), "hola")
        self.assertTrue((dos / "vacia").is_dir())
        # Borrar en uno → desaparece en dos
        (uno / "sub" / "x.txt").unlink()
        s1.ronda()
        s2.ronda()
        self.assertFalse((dos / "sub" / "x.txt").exists())
        # Los dos cambian la misma nota → se guardan las dos versiones
        (uno / "nota.txt").write_text("de uno")
        (dos / "nota.txt").write_text("de dos")
        s1.ronda()
        s2.ronda()
        s1.ronda()
        for carpeta in (uno, dos):
            nombres = sorted(p.name for p in carpeta.iterdir() if not p.name.startswith("."))
            self.assertIn("nota.txt", nombres)
            self.assertTrue(any("conflicto de dos" in n for n in nombres), nombres)
        self.assertEqual((uno / "nota.txt").read_text(), (dos / "nota.txt").read_text())

    def test_si_la_carpeta_de_la_nube_desaparece_no_se_borra_lo_local(self):
        local = self.tmp / "local"
        s = Sincronizador(self.cuenta, local, "/Mia", avisar=lambda *_: None)
        (local / "importante.txt").write_text("no me borres")
        s.ronda()
        uid = self.nube.usuario_por_nombre("ana")["id"]
        self.nube.a_papelera(uid, self.nube.resolver(uid, "/Mia")["id"])
        s.ronda()
        self.assertTrue((local / "importante.txt").exists())
        self.assertEqual([e["ruta"] for e in self.conexion().orden("arbol", ruta="/Mia")["entradas"]], ["importante.txt"])


class Web(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.nube = Nube(self.tmp / "datos")
        self.app = crear_app(self.nube)
        self.web = self.app.test_client()
        self.h = {"X-Scloud": "1"}
        r = self.web.post("/api/registro", json={"usuario": "ana", "nombre": "Ana", "clave": "contraseña-1"}, headers=self.h)
        self.assertEqual(r.status_code, 201)

    def tearDown(self):
        self.nube.cerrar()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def subir(self, ruta, datos, carpeta=0):
        return self.web.put(f"/api/subir?carpeta={carpeta}&ruta={ruta}", data=datos, headers=self.h)

    def test_sin_cabecera_no_se_cambia_nada(self):
        r = self.web.post("/api/carpeta", json={"padre": 0, "nombre": "x"})
        self.assertEqual(r.status_code, 403)

    def test_subir_bajar_y_rango(self):
        datos = os.urandom(10_000)
        r = self.subir("Docs/a.bin", datos)
        self.assertEqual(r.status_code, 201)
        id_ = r.get_json()["id"]
        self.assertEqual(self.web.get(f"/api/bajar/{id_}").data, datos)
        parcial = self.web.get(f"/api/bajar/{id_}", headers={"Range": "bytes=100-199"})
        self.assertEqual(parcial.status_code, 206)
        self.assertEqual(parcial.data, datos[100:200])
        self.assertEqual(self.web.get(f"/api/bajar/{id_}").headers["Content-Security-Policy"], "sandbox")

    def test_zip_de_una_carpeta(self):
        self.subir("Viaje/dia 1/a.txt", b"uno")
        self.subir("Viaje/b.txt", b"dos")
        carpeta = self.web.get("/api/carpeta/0").get_json()["hijos"][0]["id"]
        z = zipfile.ZipFile(io.BytesIO(self.web.get(f"/api/bajar/{carpeta}").data))
        self.assertEqual(sorted(z.namelist()), ["Viaje/", "Viaje/b.txt", "Viaje/dia 1/", "Viaje/dia 1/a.txt"])
        self.assertEqual(z.read("Viaje/dia 1/a.txt"), b"uno")

    def test_enlace_con_clave_y_buzon(self):
        self.subir("Pública/f.txt", b"hola")
        carpeta = self.web.get("/api/carpeta/0").get_json()["hijos"][0]["id"]
        url = self.web.post(f"/api/nodo/{carpeta}/enlace", json={"clave": "abcd"}, headers=self.h).get_json()["url"]
        token = url.rsplit("/", 1)[1]
        anonimo = self.app.test_client()
        self.assertEqual(anonimo.get(f"/api/e/{token}/bajar").status_code, 401)
        self.assertNotIn("hijos", anonimo.get(f"/api/e/{token}").get_json())
        self.assertEqual(anonimo.post(f"/api/e/{token}/clave", json={"clave": "mal"}, headers=self.h).status_code, 401)
        self.assertEqual(anonimo.post(f"/api/e/{token}/clave", json={"clave": "abcd"}, headers=self.h).status_code, 200)
        self.assertEqual([h["nombre"] for h in anonimo.get(f"/api/e/{token}").get_json()["hijos"]], ["f.txt"])
        # Buzón: dos personas mandan un archivo con el mismo nombre y no se pisan
        buzon = self.web.post(f"/api/nodo/{carpeta}/enlace", json={"permiso": "subir"}, headers=self.h).get_json()["url"].rsplit("/", 1)[1]
        for contenido in (b"de uno", b"de otro"):
            r = anonimo.put(f"/api/e/{buzon}/subir?nombre=f.txt", data=contenido, headers=self.h)
            self.assertEqual(r.status_code, 201)
        nombres = sorted(h["nombre"] for h in self.web.get(f"/api/carpeta/{carpeta}").get_json()["hijos"])
        self.assertEqual(nombres, ["f (2).txt", "f (3).txt", "f.txt"])
        # Y por el buzón no se puede bajar nada
        self.assertEqual(anonimo.get(f"/api/e/{buzon}/bajar").status_code, 403)

    def test_html_subido_no_se_ejecuta(self):
        id_ = self.subir("malo.html", b"<script>alert(1)</script>").get_json()["id"]
        r = self.web.get(f"/api/bajar/{id_}?ver=1")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(r.headers["Content-Security-Policy"], "sandbox")

    def test_otra_cuenta_no_ve_nada(self):
        id_ = self.subir("privado.txt", b"secreto").get_json()["id"]
        otro = self.app.test_client()
        otro.post("/api/registro", json={"usuario": "beto", "nombre": "Beto", "clave": "contraseña-2"}, headers=self.h)
        self.assertEqual(otro.get(f"/api/bajar/{id_}").status_code, 404)
        self.assertEqual(otro.patch(f"/api/nodo/{id_}", json={"nombre": "mio"}, headers=self.h).status_code, 404)


if __name__ == "__main__":
    unittest.main()
