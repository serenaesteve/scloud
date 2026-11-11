# 43 · scloud

**Enlace:** https://scloud.serenaesteve.com

**Antes de grabar:** scloud abierto con mi cuenta.

**Qué enseño en el vídeo (unos 30 segundos):**
1. Arrastro dos archivos y una carpeta → las gotas se van llenando mientras suben
2. Creo una carpeta y abro la vista previa de un archivo
3. Lo mando a la papelera y lo recupero

---

**a) Introducción**

Os presento scloud, una nube para guardar tus archivos y tenerlos en cualquier sitio, parecida a Google Drive o Dropbox. Las usamos todos los días, pero nuestros archivos acaban en servidores de grandes empresas sobre los que no tenemos ningún control, y yo quería entender cómo funciona por dentro un servicio así haciéndolo desde cero.

**b) La solución**

En scloud te creas una cuenta y subes archivos o carpetas enteras arrastrándolos a la web, varios a la vez, viendo cómo avanza cada uno. Puedes organizarlos en carpetas, buscarlos, ver una vista previa sin descargarlos, moverlos y recuperarlos de la papelera durante 30 días.

Todo se actualiza en directo: si subes algo desde otra pestaña o desde otro dispositivo, aparece al momento sin recargar. Y además hay un programa para el ordenador que mantiene una carpeta sincronizada con la nube.

**c) Especificaciones tecnológicas**

El servidor está hecho en Python con Flask y los datos se guardan en SBD, mi propio motor de bases de datos. Cada archivo se guarda según su huella, así que si subes dos veces el mismo archivo solo ocupa una vez. Las actualizaciones en directo usan Server-Sent Events, y el programa de escritorio se comunica con el servidor por sockets con una conexión cifrada. La interfaz es HTML, CSS y JavaScript, con mi estilo de siempre, esta vez en tonos de cielo. Está publicada en mi servidor con HTTPS, con un espacio limitado por cuenta y copias de seguridad diarias.

**d) Cierre**

scloud es para quien quiere guardar sus archivos sin depender de las grandes plataformas. En la página de privacidad explico claramente qué se guarda y cómo, porque si alguien te confía sus archivos, tiene que saberlo. En la siguiente publicación os enseño cómo se comparten los archivos entre cuentas.

#Cloud #Python #Flask #DesarrolloWeb #DAM
