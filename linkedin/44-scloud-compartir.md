# 44 · scloud: compartir archivos

**Enlace:** https://scloud.serenaesteve.com

**Antes de grabar:** dos cuentas abiertas a la vez, una en el navegador normal y otra en una ventana de incógnito.

**Qué enseño en el vídeo (unos 30 segundos):**
1. Cuenta 1: comparto una carpeta con la cuenta 2 con permiso «ver»
2. Cuenta 2: le llega el aviso → Compartido → abre la carpeta, puede ver y descargar pero no subir
3. Cuenta 1 cambia el permiso a «editar» → la cuenta 2 sube un archivo y aparece al momento en la cuenta 1

---

**a) Introducción**

Hoy os enseño cómo se comparten archivos en scloud, mi nube de archivos. Compartir parece sencillo, pero es donde más cosas pueden salir mal: que alguien vea lo que no debe, que borre lo que no le toca o que no sepas quién tiene acceso a tus carpetas. Y muchas veces acabamos mandando archivos por correo o por WhatsApp porque es más fácil.

**b) La solución**

En scloud puedes compartir un archivo o una carpeta con otra cuenta eligiendo qué puede hacer: solo ver y descargar, o también editar, que le deja subir archivos, crear carpetas y renombrar. A la otra persona le llega un aviso al momento y lo encuentra en su sección de Compartido, separado de lo suyo. Si cambias el permiso o dejas de compartir, se aplica en ese mismo instante.

Para quien no tiene cuenta hay dos opciones más: un enlace de descarga, que puede llevar contraseña, fecha de caducidad y un límite de descargas, y un buzón, para que alguien te mande archivos sin poder ver lo que ya tienes.

**c) Especificaciones tecnológicas**

Los permisos se comprueban siempre en el servidor, en cada petición, y se heredan: si compartes una carpeta, se comparte todo lo que hay dentro. Quien no tiene permiso sobre algo ni siquiera puede saber que existe. Los avisos en directo usan Server-Sent Events, y los enlaces públicos llevan un código aleatorio difícil de adivinar, con la contraseña guardada cifrada. Todo esto está hecho en Python con Flask y guardado en SBD, mi motor de bases de datos.

**d) Cierre**

Esto permite trabajar en equipo sobre los mismos archivos sin perder el control de quién puede hacer qué. Y me ha enseñado que en un servicio así lo más importante no es subir y bajar archivos, sino decidir bien quién puede acceder a cada cosa.

#Cloud #Seguridad #Python #Flask #DAM
