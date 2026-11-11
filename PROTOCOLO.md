# SCL/1 · el protocolo de los equipos

Es el idioma en que el cliente de escritorio (`python3 -m scloud …`) habla con el servidor. Va sobre **TCP**, cifrado con **TLS**, en el puerto **8105**. La web no lo usa: va por HTTP con Flask.

## Por qué un protocolo propio y no HTTP

La web ya tiene HTTP. Los equipos necesitan tres cosas que en HTTP piden rodeos:

1. **Mantener una conexión abierta** y mandar muchas órdenes por ella sin volver a saludar.
2. **Reanudar** una subida cortada a partir del último byte que llegó.
3. **Quedarse esperando** a que cambie algo en la nube sin preguntar cada segundo («esperar»).

Con un protocolo de líneas eso son diez órdenes y un `readline()`.

## Formato

Cada mensaje es **una línea de JSON** terminada en `\n`, de 1 MB como mucho. Si una orden lleva contenido, los bytes van **en crudo justo después** de la línea, y la línea dice cuántos son. No hay base64 ni trozos.

```
→ {"orden":"subir","ruta":"Fotos/playa.jpg","tam":812345,"huella":"9f2c…","mod":1794400000000}
← {"ok":true,"desde":0}
→ (812345 bytes)
← {"ok":true,"version":1}
```

Toda respuesta lleva `"ok"`. Si es `false`, trae `"error"` (en español, para enseñarlo tal cual) y `"codigo"` (como en HTTP: 401, 404, 409…). Un error no cierra la conexión, salvo que queden bytes a medias en el socket.

## Saludo y seguridad

| Orden | Datos | Respuesta |
|---|---|---|
| `entrar` | `usuario`, `clave`, `equipo` | `token` del equipo nuevo. La contraseña no se guarda en el equipo. |
| `hola` | `token`, `version: 1` | `usuario`, `visible`, `equipo`, `gen` |

Hasta que no se saluda, cualquier otra orden devuelve 401. El servidor guarda del token solo su SHA-256. Desde la web se puede quitar un equipo y su token deja de valer al momento.

El certificado TLS es autofirmado (lo crea `openssl` la primera vez). El cliente **fija su huella** SHA-256, como hace SSH. La web enseña la huella y la orden de conexión ya la lleva. Si un día el servidor presenta otra, el cliente se niega a seguir sin mandar nada.

## Órdenes

| Orden | Datos | Respuesta | Notas |
|---|---|---|---|
| `espacio` | — | `usado`, `cuota` | |
| `listar` | `ruta` | `hijos` | una carpeta |
| `arbol` | `ruta`, `sincroniza?` | `entradas` [`ruta`, `tipo`, `tam`, `huella`, `mod`, `version`], `gen` | todo lo de debajo, con rutas relativas |
| `carpeta` | `ruta` | — | crea las que falten (como `mkdir -p`) |
| `subir` | `ruta`, `tam`, `huella`, `mod`, `base?` | `desde` · luego `version`, `reanudada` | ver abajo |
| `bajar` | `ruta`, `desde` | `tam`, `huella`, `desde`, `mod` + bytes | |
| `mover` | `de`, `a` | — | renombra y/o cambia de carpeta |
| `borrar` | `ruta`, `huella?` | — | a la papelera; si `huella` no coincide, 409 |
| `esperar` | `gen`, `segundos` (≤ 55) | `gen` | *long polling* |
| `adios` | — | — | cierra |

### Subir

1. El cliente calcula la **huella SHA-256** del archivo y la manda con el tamaño.
2. Si esa cuenta ya tiene un archivo con esa huella, el servidor contesta `{"ok":true,"instantanea":true}` y no hace falta mandar nada. Solo vale dentro de la misma cuenta: si valiera entre cuentas, cualquiera podría averiguar si otra persona tiene un archivo concreto.
3. Si no, el servidor **espera turno** (máximo 4 transferencias por cuenta y 12 en total) y contesta `desde`: 0, o los bytes que ya tenía de un intento anterior que se cortó.
4. El cliente manda los bytes desde ahí. El servidor comprueba la huella **del archivo entero** antes de darlo por bueno. Si no cuadra, devuelve 422 y lo tira.
5. `base` es la huella que el cliente cree que hay ahora en la nube. Si ha cambiado entre medias, devuelve 409 (conflicto) y no pisa nada. Con `base: ""` dice que no espera que exista.

### Esperar

El cliente dice qué **generación** conoce. Cada cuenta tiene un número que sube con cada cambio en su nube. El hilo del servidor se duerme en una `threading.Condition` hasta que el número cambia o pasan los segundos. Cualquier hilo que cambie algo (una subida web, otro equipo, alguien con permiso de edición) hace `notify_all()`.

## Ejemplo con `openssl s_client`

```
$ openssl s_client -quiet -connect scloud.serenaesteve.com:8105
{"orden":"hola","token":"scl_…","version":1}
{"ok":true,"usuario":"ana","visible":"Ana","equipo":"portátil","gen":7}
{"orden":"listar","ruta":"/"}
{"ok":true,"hijos":[{"id":1,"nombre":"Fotos","tipo":"carpeta",…}]}
{"orden":"esperar","gen":7,"segundos":30}
                                   ← (aquí se queda hasta que algo cambie)
{"ok":true,"gen":8}
```
