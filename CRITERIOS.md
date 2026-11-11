# scloud · criterios de evaluación (0490 Programación de servicios y procesos)

scloud es una nube privada de archivos: subir y bajar varios archivos a la vez, carpetas, compartir con otras cuentas, enlaces de descarga, permisos y sincronización de carpetas. Hay una web con **Flask** y un cliente de escritorio que habla con el servidor por **sockets** con un protocolo propio (SCL/1, [PROTOCOLO.md](PROTOCOLO.md)), y por dentro todo funciona con **threading**.

Se prueba con 39 pruebas automáticas (`python3 -m unittest discover -s pruebas -t .`), un recorrido de 12 pasos con Chromium ([pruebas/navegador.py](pruebas/navegador.py)) y un experimento de medida ([experimentos/latencia.py](experimentos/latencia.py)).

---

## RA2 · Programación multihilo

### a) Cuándo conviene usar varios hilos

En scloud casi todo el tiempo se pasa **esperando**: a que lleguen bytes por la red, a que el disco escriba o a que alguien cambie algo. Mientras un hilo espera, los demás trabajan. Hay cuatro casos:

| Dónde | Por qué hilos |
|---|---|
| Servidor web | Una subida de 2 GB tarda minutos; sin hilos, nadie más podría ni listar una carpeta |
| Servidor de equipos | Cada equipo mantiene su conexión abierta (incluida una dormida en «esperar») |
| Cliente | Mientras se sube un archivo, se puede ir subiendo otro: se tapa la latencia (medido en j) |
| Sincronización | Vigilar la nube y vigilar la carpeta local a la vez |

### b) Crear, iniciar y terminar hilos

- `threading.Thread(target=…, name=…, daemon=True).start()`: el aceptador y los hilos de equipo en [servidor_equipos.py](scloud/servidor_equipos.py), los obreros y el pintor en [cliente.py](scloud/cliente.py) y los vigías en [sincro.py](scloud/sincro.py).
- **Terminar bien**: ningún hilo se mata desde fuera. Se le avisa con un `threading.Event` (`parar`, `cancelar`) que el hilo mira en su bucle. El aceptador usa `settimeout(0.5)` en `accept()` para poder mirar el aviso. Los hilos que esperan en la `Condition` se despiertan con `notify_all()` al parar.
- `join(timeout=0.2)` en bucle en `Lote.ejecutar`: el hilo principal vuelve cada poco, y con Ctrl+C levanta `cancelar` para que los obreros acaben su trozo y paren.
- Los hilos tienen **nombre** (`equipo-12`, `transferencia-3`, `vigía-nube`), y la sala de máquinas de la web los enseña en directo.

### c) Aplicaciones con varios hilos

Las tres partes lo son: la web (un hilo por petición), el servidor de equipos (uno por conexión) y el cliente (N obreros, un pintor y dos vigías).

### d) Estados de un hilo

Cada transferencia pasa por **en cola → en curso → hecha / error / cancelada**, y se ve en la «lluvia» de la web. «En cola» es un hilo **bloqueado** en un semáforo. «En curso» está **en ejecución** o bloqueado en E/S. El hilo de un equipo que manda `esperar` está **dormido** en una `Condition` hasta que lo despiertan o pasa el tiempo. Los obreros del cliente **terminan** solos cuando la cola se vacía (`queue.Empty`).

### e) Compartir información entre hilos

| Qué | Cómo | Dónde |
|---|---|---|
| Cola de trabajos del cliente | `queue.PriorityQueue` (los archivos pequeños primero) | `Lote` |
| Avisos a las pestañas | Una `queue.Queue` por pestaña: productor (quien cambia algo) y consumidor (el hilo SSE) | `Avisos.suscribir/enviar` |
| Transferencias en curso | `dict` protegido con `Lock` | `Registro` |
| Progreso en el cliente | Campos de cada `Trabajo` escritos con `Lock`, leídos por el pintor | `Lote._dibujar` |
| Hay cambios | `threading.Event` que levantan los dos vigías | `Sincronizador.hay_cambios` |

### f) Sincronización

- **`RLock` de la base**: SBD ejecuta las órdenes de una en una, así que todo el acceso pasa por un cerrojo reentrante. Una transacción lo coge de principio a fin ([bd.py](scloud/bd.py)). Reentrante porque una transacción llama a otras funciones que también lo cogen.
- **Comprobar y luego insertar, dentro de la transacción**: «crea la carpeta si no existe» con 8 hilos a la vez deja una sola carpeta (`test_muchos_hilos_crean_la_misma_carpeta`).
- **`BoundedSemaphore`**: como mucho 12 transferencias a la vez en el servidor y 4 por cuenta. El resto espera «en cola» (`test_el_semaforo_pone_en_cola`). Se cogen **siempre en el mismo orden** (primero el de la cuenta y luego el global) para que no pueda haber interbloqueo.
- **`Condition` con `wait_for`**: «esperar» se despierta cuando cambia la generación de la cuenta ([avisos.py](scloud/avisos.py)).
- **`Event`**: cancelar una transferencia desde otra pestaña y parar los hilos.

### g) Prioridad

En CPython no se puede dar prioridad a un hilo (el planificador es del sistema y el GIL va por turnos). La prioridad se pone **en el trabajo**: el `Lote` usa una `PriorityQueue` ordenada por tamaño. En una carpeta con cien notas y un vídeo, las notas aparecen enseguida en la nube y el vídeo va al final. `Trabajo` es un `dataclass(order=True)` con `(prioridad, orden)` como clave, para que dos del mismo tamaño salgan por orden de llegada.

### h) Depurar y documentar

Hilos con nombre, la sala de máquinas (hilos vivos agrupados por tipo, conexiones con lo que hace cada una, colas y órdenes a la base), las pruebas de concurrencia y este documento.

### i) Contexto de ejecución

Todos los hilos de scloud comparten el proceso: la memoria, la base abierta y el almacén. Cada uno tiene su pila y sus variables locales. Por eso la sesión de cada conexión (`Sesion`) es un objeto que solo usa su hilo, y lo común (la nube, los registros) está protegido.

### j) Bibliotecas multihilo de Python

`threading` (Thread, Lock, RLock, Condition, Event, BoundedSemaphore y Barrier en las pruebas), `queue` (Queue y PriorityQueue) y `socketserver.ThreadingMixIn`, que es lo que usa Werkzeug con `threaded=True`. No se usa `concurrent.futures`: el `Lote` necesita reintentos con la misma conexión por hilo, y con un pool genérico la conexión no se podría reutilizar. **Medida** ([experimentos/latencia.py](experimentos/latencia.py), 60 archivos de 200 KB con TLS):

| hilos | sin retraso | +40 ms por sentido |
|---:|---:|---:|
| 1 | 0,45 s | 5,63 s |
| 2 | 0,66 s | 3,28 s |
| 4 | 1,10 s | **1,97 s** |
| 8 | 1,36 s | 1,90 s |

En la misma máquina, más hilos **empeoran**: cada uno paga su saludo TLS y todos compiten por el GIL y por el cerrojo de la base, y no hay espera que tapar. Con latencia, 4 hilos van **2,9 veces más rápido**. Con 8 ya no mejora, porque el servidor solo deja 4 transferencias por cuenta (el semáforo de f).

### k) Problemas de compartir información

- **Condición de carrera** al crear carpetas o subir el mismo nombre a la vez: resuelta con la transacción.
- **Interbloqueo** con dos semáforos: resuelto con el orden fijo.
- **Archivo a medias**: lo que llega se escribe en `temporal/` y solo se mueve a su sitio con `os.replace` (atómico) cuando la huella cuadra.
- **El barrendero contra una subida**: un bloque recién llegado aún no está en la base. El barrendero solo borra bloques sin uso **de más de una hora** ([almacen.py](scloud/almacen.py)).
- **Pestaña dormida**: su cola tiene tope (500) y, si se llena, se descartan avisos de esa pestaña sin frenar a las demás.

---

## RA3 · Sockets

### a) y b) Escenarios, cliente y servidor

Hay dos clientes: el navegador (HTTP) y el cliente de escritorio (SCL/1). El servidor es un solo proceso con los dos servicios. El cliente de escritorio es quien abre la conexión, saluda y pide; el servidor escucha, comprueba permisos y contesta.

### c) y d) Bibliotecas y tipos de socket

`socket` con `AF_INET` y `SOCK_STREAM` (TCP: fiable y en orden, que es lo que necesita un archivo) y `ssl` para el cifrado. No se usa UDP: perder un trozo de un archivo no es aceptable. `TCP_NODELAY` en el cliente, porque las órdenes son líneas cortas y Nagle las retrasaría. `SO_REUSEADDR` en el servidor, para poder reiniciar sin esperar el `TIME_WAIT`.

### e) Cliente

[cliente.py](scloud/cliente.py): `socket.create_connection`, TLS, `sendall` y `makefile("rb")` para leer por líneas (`readline`) y por bloques (`read1`). Órdenes: `ls`, `subir`, `bajar`, `sincronizar`.

### f) Servidor

[servidor_equipos.py](scloud/servidor_equipos.py): `bind`, `listen(64)`, `accept` en un hilo propio y un hilo por conexión. Se comprueba con las pruebas de `test_red.py` y a mano con `openssl s_client` (ejemplo en PROTOCOLO.md).

### g) y h) Intercambiar información

SCL/1: una línea de JSON y, si hace falta, bytes en crudo. Subir con reanudación (`desde`), bajar con reanudación (`.scl-parcial`), huella SHA-256 comprobada en los dos sentidos, `base` para detectar conflictos y `esperar` para enterarse de cambios.

### i) Hilos para varios clientes

Un hilo por conexión. Prueba: `test_muchos_clientes_a_la_vez` (20 clientes subiendo a la vez). Además, el cliente abre varias conexiones a la vez (un `Lote` con 4 hilos son 4 conexiones).

### j) Modelos de comunicación

- **Cliente-servidor petición-respuesta**: casi todo.
- **Long polling**: `esperar` (el servidor contesta cuando hay algo).
- **Push del servidor**: Server-Sent Events en la web (`/api/eventos`), una conexión HTTP que no se cierra y por la que el servidor manda avisos.
- **Productor-consumidor**: las colas de avisos y la cola del lote.

### k) Depurar y documentar

PROTOCOLO.md, la sala de máquinas (cada conexión con su hilo y lo que hace) y las pruebas.

---

## RA4 · Servicios en red

### a), b) y c) Protocolos estándar

| Protocolo | Para qué | Biblioteca |
|---|---|---|
| HTTP/1.1 | La web y su API | Flask (Werkzeug) |
| `Range` / 206 | Descargas reanudables y vídeo con saltos | A mano en `_enviar_archivo` |
| Server-Sent Events | Avisos en directo | Respuesta en streaming de Flask |
| ZIP | Bajar carpetas | `zipfile` escribiendo en un «tubo», sin pasar por disco |
| TLS | Cifrado del canal de equipos | `ssl` |

Usar los estándares da clientes gratis: el navegador sabe reanudar una descarga con `Range`, `EventSource` se reconecta solo, `curl` habla con la API y `openssl s_client` habla con el canal de equipos. El protocolo de equipos es propio por lo que explica PROTOCOLO.md (conexión persistente, reanudación de subidas, espera).

### d) y e) Servicios probados con clientes

El navegador (recorrido con Playwright), `curl`, el cliente de escritorio y el cliente de pruebas de Flask (`test_client`).

### f) Varios clientes a la vez

Web: un hilo por petición (`make_server(threaded=True)`). Equipos: un hilo por conexión. Los avisos llegan a todas las pestañas y equipos de una cuenta y a las cuentas con las que se comparte la carpeta.

### g) Disponibilidad

- **Límites**: semáforos (12 en total y 4 por cuenta), cuota por cuenta y una reserva de 20 GB de disco que scloud nunca toca, porque la máquina es de todos los servicios.
- **Nada se queda colgado**: `settimeout(120)` en las conexiones de equipo, latidos SSE cada 20 s y `timeout` en Apache.
- **Recuperarse**: reintentos con espera creciente en el cliente (3) y reanudación desde el último byte. El vigía de la nube vuelve a conectar si el servidor se reinicia.
- **systemd**: `Restart=on-failure`, `TasksMax`, `MemoryMax` y parada limpia con `SIGTERM`.
- **Copia diaria** del índice (14 días).

### h) Documentación

README.md, PROTOCOLO.md y este documento.

---

## RA5 · Seguridad

### a) Programación segura

- **Rutas**: `limpiar_nombre` rechaza `/`, `\`, `..` y los caracteres de control. Los archivos se guardan por su huella, nunca con el nombre que da el usuario, así que es imposible escribir fuera de `datos/`.
- **Contenido subido**: se sirve con `Content-Security-Policy: sandbox` y `nosniff`. Un HTML o un SVG subido no ejecuta nada con tu sesión (`test_html_subido_no_se_ejecuta`). Solo se ven en línea las imágenes, el PDF, el audio, el vídeo y el texto plano.
- **CSRF**: lo que cambia algo exige la cabecera `X-Scloud: 1` y la cookie es `SameSite=Lax`.
- **XSS**: todo lo que pinta la web pasa por `esc()`, y la CSP de la página no admite scripts en línea.
- **Fuerza bruta**: 8 fallos seguidos desde el mismo sitio → 30 s de espera. En un enlace con contraseña, 6 fallos → 60 s. Como mucho, 5 cuentas nuevas por hora y por IP.
- **Misma respuesta exista o no**: entrar con un usuario que no existe tarda lo mismo (se calcula un scrypt igual), y lo que no puedes ver da 404, no 403.

### b) y e) Criptografía para lo guardado

- **Contraseñas** de cuenta y de enlace: scrypt (n = 2¹⁴, r = 8, p = 1) con sal propia, comparadas con `hmac.compare_digest`.
- **Tokens** de equipos y de enlaces: aleatorios (`secrets`). En la base solo queda su SHA-256: quien lea la base no puede entrar ni abrir los enlaces.
- **Integridad**: SHA-256 de cada archivo, comprobado al subir y al bajar.
- **Lo que no hay** (y lo dice la página de privacidad): cifrado de extremo a extremo de los archivos. El servidor puede leerlos.

### c) y d) Políticas de acceso y roles

| Rol sobre un nodo | Puede |
|---|---|
| propietario | todo: compartir, enlaces, mover fuera, borrar |
| editar | subir, crear carpetas, renombrar, mover dentro y mandar a la papelera |
| ver | abrir y descargar |
| (nada) | ni saber que existe (404) |

Un permiso sobre una carpeta **se hereda** en todo lo que hay dentro (`Nube.permiso` sube por los antepasados). Lo que sube alguien con permiso de edición cuenta en la cuota del dueño. Los enlaces añaden caducidad, contraseña, límite de descargas y el modo buzón (subir sin ver). En el servidor hay además un rol `admin`, que ve las conexiones de todos en la sala de máquinas.

### f) y g) Proteger lo que viaja

- Web: HTTPS con Let's Encrypt en Apache, `Strict-Transport-Security` y cookie `Secure`.
- Equipos: **TLS 1.2 o más** con un certificado autofirmado que se crea con `openssl` en un subproceso ([tls.py](scloud/tls.py)). El cliente **fija la huella** del certificado: la web la enseña y la orden de conexión la lleva. Si cambia, el cliente no manda nada (`test_la_huella_del_certificado_se_fija`).

### h) Depurar y documentar

Las pruebas de seguridad están en `test_nube.py` y `test_red.py`, y [privacidad.html](web/privacidad.html) cuenta qué se guarda y quién lo puede ver.

---

## RA1 · Varios procesos (de paso)

scloud no reparte su trabajo entre procesos (eso lo hice en Cobertura y en schat). Aquí lanza un subproceso: `openssl req …` con `subprocess.run`, que **espera** a que termine, recoge su **código de salida** y su salida de errores y, si falla, para con el mensaje de openssl ([tls.py](scloud/tls.py)). El servicio corre como un proceso propio, con su usuario y sus límites en systemd.
