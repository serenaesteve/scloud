"""TLS para el canal de los equipos.

El certificado se crea la primera vez con la orden «openssl» en un
subproceso. Es autofirmado: no hay ninguna autoridad que lo avale, así que
el cliente no puede comprobarlo como hace un navegador. En su lugar fija su
huella (SHA-256 del certificado), como hace SSH con las claves de los
servidores: la web enseña la huella al conectar un equipo y el cliente la
guarda; si un día el servidor presenta otra, el cliente se niega a seguir.
"""

import hashlib
import ssl
import subprocess
from pathlib import Path


def asegurar_certificado(carpeta):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    cert, clave = carpeta / "cert.pem", carpeta / "clave.pem"
    if not (cert.exists() and clave.exists()):
        proceso = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
             "-keyout", str(clave), "-out", str(cert), "-days", "3650", "-subj", "/CN=scloud"],
            capture_output=True, text=True, timeout=60)
        if proceso.returncode != 0:
            raise RuntimeError(f"openssl no pudo crear el certificado: {proceso.stderr.strip()}")
        clave.chmod(0o600)
    return cert, clave


def huella_pem(cert):
    der = ssl.PEM_cert_to_DER_cert(Path(cert).read_text())
    return formatear(hashlib.sha256(der).hexdigest())


def formatear(hexa):
    return ":".join(hexa[i:i + 2] for i in range(0, len(hexa), 2)).upper()


def contexto_servidor(cert, clave):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, clave)
    return ctx


def contexto_cliente():
    """Sin autoridades: la comprobación la hace el cliente con la huella fijada."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def huella_conexion(sock_tls):
    return formatear(hashlib.sha256(sock_tls.getpeercert(binary_form=True)).hexdigest())
