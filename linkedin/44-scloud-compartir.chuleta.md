# 44 · scloud: compartir archivos
scloud.serenaesteve.com

VÍDEO (30 s)
Antes: dos cuentas abiertas (navegador normal + ventana de incógnito)
1. Cuenta 1: compartir carpeta con cuenta 2 → permiso «ver»
2. Cuenta 2: aviso → Compartido → ve y descarga, no puede subir
3. Cuenta 1 cambia a «editar» → cuenta 2 sube → aparece al momento en cuenta 1

a) INTRO
- Hoy os enseño cómo se comparte en scloud
- Problema: compartir = donde más falla (ver lo que no debe, borrar, no saber quién tiene acceso) · acabamos usando correo o WhatsApp

b) SOLUCIÓN
- Compartir con otra cuenta: ver o editar
- Aviso al momento · sección Compartido separada
- Cambiar permiso o dejar de compartir → al instante
- Sin cuenta: enlace (contraseña, caducidad, límite de descargas) · buzón para recibir

c) TECNOLOGÍAS
- Permisos comprobados en el servidor en cada petición
- Se heredan: carpeta compartida = todo lo de dentro
- Sin permiso = ni sabe que existe
- Server-Sent Events (avisos en directo)
- Enlaces con código aleatorio · contraseña cifrada
- Python + Flask · SBD

d) CIERRE
- Trabajar en equipo sin perder el control
- Lo importante: decidir quién accede a cada cosa
