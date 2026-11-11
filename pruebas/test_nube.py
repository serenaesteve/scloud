"""La lógica de la nube: cuentas, permisos, papelera, enlaces y cuotas."""

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path

from scloud.nube import (Conflicto, ErrorNube, NoEncontrado, Nube, SinEspacio, SinPermiso, limpiar_nombre,
                         normalizar_usuario)


def subir(nube, uid, carpeta, nombre, datos):
    huella = nube.almacen.guardar_flujo(io.BytesIO(datos).read, len(datos))
    return nube.registrar_archivo(uid, carpeta, nombre, huella, len(datos))


class BaseNube(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.nube = Nube(Path(self.tmp.name), cuota=10_000)
        self.ana = self.nube.crear_usuario("ana", "Ana", "contraseña-1")["id"]
        self.beto = self.nube.crear_usuario("beto", "Beto", "contraseña-2")["id"]

    def tearDown(self):
        self.nube.cerrar()
        self.tmp.cleanup()


class Cuentas(BaseNube):
    def test_entrar_y_fallar(self):
        self.assertEqual(self.nube.entrar("ana", "contraseña-1")["nombre"], "ana")
        with self.assertRaises(ErrorNube) as e:
            self.nube.entrar("ana", "mal")
        self.assertEqual(e.exception.estado, 401)

    def test_freno_tras_muchos_fallos(self):
        for _ in range(8):
            with self.assertRaises(ErrorNube):
                self.nube.entrar("ana", "mal", origen="1.2.3.4")
        with self.assertRaises(ErrorNube) as e:
            self.nube.entrar("ana", "contraseña-1", origen="1.2.3.4")
        self.assertEqual(e.exception.estado, 429)
        # Desde otro sitio se sigue pudiendo entrar
        self.assertTrue(self.nube.entrar("ana", "contraseña-1", origen="5.6.7.8"))

    def test_nombres_normalizados(self):
        self.assertEqual(normalizar_usuario("Lucía Pérez"), "lucia.perez")
        with self.assertRaises(Conflicto):
            self.nube.crear_usuario("ANA", "Otra", "contraseña-3")

    def test_la_contrasena_no_se_guarda(self):
        fila = self.nube.bd.una("buscar usuarios donde nombre = ?", ["ana"])
        self.assertNotIn("contraseña-1", str(fila))

    def test_cambiar_clave_cierra_sesiones(self):
        antes = self.nube.usuario(self.ana)["generacion"]
        self.nube.cambiar_clave(self.ana, "contraseña-1", "otra-clave-9")
        self.assertGreater(self.nube.usuario(self.ana)["generacion"], antes)
        self.assertTrue(self.nube.entrar("ana", "otra-clave-9"))


class Nombres(unittest.TestCase):
    def test_no_se_sale_del_arbol(self):
        for malo in ["", ".", "..", "a/b", "a\\b", "x" * 300]:
            with self.assertRaises(ErrorNube, msg=malo):
                limpiar_nombre(malo)
        self.assertEqual(limpiar_nombre("  Facturas 2026 "), "Facturas 2026")


class Arbol(BaseNube):
    def test_carpetas_y_versiones(self):
        c = self.nube.crear_carpeta(self.ana, 0, "Fotos")
        subir(self.nube, self.ana, c["id"], "a.txt", b"uno")
        v2 = subir(self.nube, self.ana, c["id"], "a.txt", b"dos")
        self.assertEqual(v2["version"], 2)
        with self.assertRaises(Conflicto):
            self.nube.crear_carpeta(self.ana, 0, "Fotos")
        self.assertEqual([h["nombre"] for h in self.nube.listar(self.ana, c["id"])["hijos"]], ["a.txt"])

    def test_no_meter_una_carpeta_en_si_misma(self):
        a = self.nube.crear_carpeta(self.ana, 0, "A")
        b = self.nube.crear_carpeta(self.ana, a["id"], "B")
        with self.assertRaises(ErrorNube):
            self.nube.mover(self.ana, a["id"], b["id"])

    def test_cuota(self):
        subir(self.nube, self.ana, 0, "grande", b"x" * 9_000)
        with self.assertRaises(SinEspacio):
            self.nube.preparar_subida(self.ana, 0, "otro", 2_000)
        # Pisar un archivo solo cuenta la diferencia
        self.nube.preparar_subida(self.ana, 0, "grande", 9_500)

    def test_papelera_con_carpeta_entera(self):
        c = self.nube.crear_carpeta(self.ana, 0, "Viaje")
        sub = self.nube.crear_carpeta(self.ana, c["id"], "dia 1")
        subir(self.nube, self.ana, sub["id"], "f.txt", b"hola")
        self.assertEqual(self.nube.a_papelera(self.ana, c["id"]), 3)
        self.assertEqual(self.nube.listar(self.ana, 0)["hijos"], [])
        self.assertEqual([p["elementos"] for p in self.nube.papelera(self.ana)], [3])
        # Mientras, alguien crea otra «Viaje»: al recuperar no se pisa
        self.nube.crear_carpeta(self.ana, 0, "Viaje")
        r = self.nube.recuperar(self.ana, c["id"])
        self.assertEqual(r["nombre"], "Viaje (2)")
        self.assertEqual(len(self.nube.arbol(self.ana)), 4)

    def test_eliminar_libera_bloques(self):
        f = subir(self.nube, self.ana, 0, "f.txt", b"contenido")
        self.nube.a_papelera(self.ana, f["id"])
        self.nube.eliminar(self.ana, f["id"])
        self.nube.almacen.barrer({h for h in []}, edad_bloque=-1)
        self.assertFalse(self.nube.almacen.existe(f["huella"]))


class Permisos(BaseNube):
    def setUp(self):
        super().setUp()
        self.fotos = self.nube.crear_carpeta(self.ana, 0, "Fotos")
        self.dentro = self.nube.crear_carpeta(self.ana, self.fotos["id"], "2026")
        self.foto = subir(self.nube, self.ana, self.dentro["id"], "playa.jpg", b"jpg")

    def test_sin_compartir_no_existe(self):
        with self.assertRaises(NoEncontrado):
            self.nube.listar(self.beto, self.fotos["id"])

    def test_ver_vale_para_lo_de_dentro(self):
        self.nube.compartir(self.ana, self.fotos["id"], "beto", "ver")
        self.assertEqual(self.nube.permiso(self.beto, self.foto), "ver")
        datos = self.nube.listar(self.beto, self.dentro["id"])
        self.assertEqual(datos["ruta"][0]["id"], "compartido")
        with self.assertRaises(SinPermiso):
            self.nube.crear_carpeta(self.beto, self.dentro["id"], "nueva")
        with self.assertRaises(SinPermiso):
            self.nube.a_papelera(self.beto, self.foto["id"])

    def test_editar_y_la_cuota_es_del_dueno(self):
        self.nube.compartir(self.ana, self.fotos["id"], "beto", "editar")
        f = subir(self.nube, self.beto, self.dentro["id"], "de-beto.txt", b"12345")
        self.assertEqual(f["propietario_id"], self.ana)
        self.assertEqual(self.nube.usado(self.beto), 0)
        # Pero no puede compartirlo con otros ni sacarlo a su nube
        with self.assertRaises(SinPermiso):
            self.nube.compartir(self.beto, self.fotos["id"], "ana", "ver")
        with self.assertRaises(SinPermiso):
            self.nube.mover(self.beto, f["id"], 0)

    def test_dejar_de_compartir(self):
        self.nube.compartir(self.ana, self.fotos["id"], "beto", "ver")
        self.assertEqual(len(self.nube.compartido_conmigo(self.beto)), 1)
        self.nube.salir_de_compartido(self.beto, self.fotos["id"])
        self.assertEqual(self.nube.compartido_conmigo(self.beto), [])
        self.assertIsNone(self.nube.permiso(self.beto, self.foto))


class Enlaces(BaseNube):
    def test_enlace_con_clave_y_limite(self):
        f = subir(self.nube, self.ana, 0, "f.txt", b"hola")
        token, e = self.nube.crear_enlace(self.ana, f["id"], clave="1234", max_descargas=1)
        enlace, nodo = self.nube.enlace(token)
        self.assertFalse(self.nube.clave_enlace_ok(enlace, "mal"))
        self.assertTrue(self.nube.clave_enlace_ok(enlace, "1234"))
        self.nube.contar_descarga(enlace["id"])
        with self.assertRaises(ErrorNube) as err:
            self.nube.enlace(token)
        self.assertEqual(err.exception.estado, 410)
        # En la base no está el token, solo su huella
        self.assertNotIn(token, str(self.nube.bd.consultar("buscar enlaces")))

    def test_dentro_del_enlace_no_se_sale(self):
        c = self.nube.crear_carpeta(self.ana, 0, "Pública")
        otra = subir(self.nube, self.ana, 0, "secreto.txt", b"no")
        token, _ = self.nube.crear_enlace(self.ana, c["id"])
        _, raiz = self.nube.enlace(token)
        with self.assertRaises(NoEncontrado):
            self.nube.dentro_de(raiz, otra["id"])

    def test_buzon_solo_en_carpetas(self):
        f = subir(self.nube, self.ana, 0, "f.txt", b"hola")
        with self.assertRaises(ErrorNube):
            self.nube.crear_enlace(self.ana, f["id"], permiso="subir")


class Concurrencia(BaseNube):
    def test_muchos_hilos_crean_la_misma_carpeta(self):
        """Ocho hilos piden a la vez «crear Fotos si no existe»: tiene que quedar una sola."""
        barrera = threading.Barrier(8)
        ids = []

        def crear():
            barrera.wait()
            ids.append(self.nube.crear_carpeta(self.ana, 0, "Fotos", existe_ok=True)["id"])

        hilos = [threading.Thread(target=crear) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.nube.listar(self.ana, 0)["hijos"]), 1)

    def test_el_semaforo_pone_en_cola(self):
        """Con hueco para 1 por cuenta, la segunda transferencia espera en cola hasta que acaba la primera."""
        from scloud.transferencias import Registro
        reg = Registro(self.nube.avisos, limite_global=4, limite_usuario=1)
        dentro, soltar, estados = threading.Event(), threading.Event(), []

        def primera():
            with reg.turno(self.ana, "a", "subida", "web", 1):
                dentro.set()
                soltar.wait(5)

        h = threading.Thread(target=primera)
        h.start()
        dentro.wait(5)
        def segunda_fn():
            with reg.turno(self.ana, "b", "subida", "web", 1):
                pass

        segunda = threading.Thread(target=segunda_fn)
        segunda.start()
        segunda.join(0.3)
        estados = {t["nombre"]: t["estado"] for t in reg.de(self.ana)}
        self.assertEqual(estados, {"a": "en curso", "b": "en cola"})
        soltar.set()
        h.join()
        segunda.join(3)
        self.assertFalse(segunda.is_alive())
        self.assertEqual({t["nombre"]: t["estado"] for t in reg.de(self.ana)}["b"], "hecha")

    def test_esperar_despierta_con_un_cambio(self):
        gen = self.nube.avisos.generacion(self.ana)
        resultado = []
        h = threading.Thread(target=lambda: resultado.append(self.nube.avisos.esperar(self.ana, gen, 5)))
        h.start()
        self.nube.crear_carpeta(self.ana, 0, "Nueva")
        h.join(2)
        self.assertFalse(h.is_alive())
        self.assertNotEqual(resultado[0], gen)


if __name__ == "__main__":
    unittest.main()
