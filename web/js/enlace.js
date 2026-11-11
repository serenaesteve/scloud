// Página pública de un enlace: descargar lo compartido o mandar archivos a un buzón.

import { $, api, aviso, crear, esc, fechaFutura, icono, ICONO_TIPO, idAleatorio, tamano, tipoDe } from "./util.js";

const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).at(-1) || "");
const base = `/api/e/${encodeURIComponent(token)}`;
const tarjeta = $("#tarjeta");

cargar();

async function cargar(carpeta) {
  let d;
  try {
    d = await api(carpeta ? `${base}?carpeta=${carpeta}` : base);
  } catch (e) {
    document.title = "Enlace no disponible · scloud";
    tarjeta.innerHTML = `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-nube"/></svg>
      <h2>${e.estado === 410 ? "Este enlace ha caducado" : "Este enlace no funciona"}</h2>
      <p>${esc(e.message)} Pide a quien te lo mandó que te pase otro.</p></div>`;
    return;
  }
  document.title = `${d.nombre} · scloud`;
  if (!d.abierto) return pedirClave(d);
  if (d.permiso === "subir") return buzon(d);
  descarga(d);
}

function cabeza(d, texto) {
  const tipo = tipoDe(d);
  const meta = [
    d.tipo === "archivo" ? tamano(d.tamano) : "",
    d.caduca ? `caduca ${fechaFutura(d.caduca)}` : "",
    d.quedan != null ? `${d.quedan} ${d.quedan === 1 ? "descarga" : "descargas"} disponibles` : "",
  ].filter(Boolean);
  return `<div class="publico-cabeza">
    <span class="tipo ${tipo}">${icono(ICONO_TIPO[tipo])}</span>
    <p class="publico-de">${texto}</p>
    <h1>${esc(d.nombre)}</h1>
    ${meta.length ? `<p class="publico-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("<span aria-hidden=\"true\">·</span>")}</p>` : ""}
  </div>`;
}

function pedirClave(d) {
  tarjeta.innerHTML = `${cabeza(d, `${esc(d.de)} te ha compartido esto con contraseña`)}
    <form class="formulario" id="form-clave">
      <label class="campo"><span>Contraseña del enlace</span><input type="password" name="clave" required autocomplete="off"></label>
      <p class="error-form" role="alert" hidden></p>
      <button class="boton primario ancho" type="submit">${icono("candado")}<span>Abrir</span></button>
    </form>`;
  $("#form-clave input").focus();
  $("#form-clave").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`${base}/clave`, { metodo: "POST", datos: { clave: e.currentTarget.clave.value } });
      cargar();
    } catch (err) {
      const p = $(".error-form");
      p.textContent = err.message;
      p.hidden = false;
    }
  });
}

function descarga(d) {
  const esCarpeta = d.tipo === "carpeta";
  let lista = "";
  if (esCarpeta) {
    const migas = d.ruta.length > 1 ? `<nav class="migas" aria-label="Estás en">${d.ruta.map((m, i) =>
      `${i ? '<span class="sep">/</span>' : ""}<a href="#" data-carpeta="${m.id}">${esc(m.nombre)}</a>`).join("")}</nav>` : "";
    lista = `<div class="publico-lista">${migas}${d.hijos.length ? `<ul class="lista">${d.hijos.map((h, i) => {
      const tipo = tipoDe(h);
      return `<li class="fila" data-i="${i}">
        <span class="marca-sel"><span class="tipo ${tipo}">${icono(ICONO_TIPO[tipo])}</span></span>
        <span class="nombre-col"><span class="nombre">${h.tipo === "carpeta"
          ? `<button class="abrir" type="button" data-carpeta="${h.id}">${esc(h.nombre)}</button>`
          : `<a href="${base}/bajar?id=${h.id}" download>${esc(h.nombre)}</a>`}</span>
          <span class="sub movil">${h.tipo === "carpeta" ? "carpeta" : tamano(h.tamano)}</span></span>
        <span class="tam der">${h.tipo === "carpeta" ? "—" : tamano(h.tamano)}</span>
        <a class="mas-acciones" href="${base}/bajar?id=${h.id}" download aria-label="Descargar ${esc(h.nombre)}">${icono("bajar")}</a>
      </li>`;
    }).join("")}</ul>` : `<p class="tenue vacia-enlace">Esta carpeta está vacía.</p>`}</div>`;
  }
  tarjeta.innerHTML = `${cabeza(d, `${esc(d.de)} te ha compartido ${esCarpeta ? "una carpeta" : "un archivo"}`)}
    <div class="publico-acciones">
      <a class="boton primario" href="${base}/bajar${d.carpeta && d.ruta?.length > 1 ? `?id=${d.carpeta}` : ""}" download>${icono("bajar")}<span>${esCarpeta ? "Descargar todo en ZIP" : "Descargar"}</span></a>
    </div>
    ${lista}
    <p class="publico-pie">Compartido con scloud. No hace falta cuenta para descargar.</p>`;
  tarjeta.onclick = (e) => {
    const c = e.target.closest("[data-carpeta]");
    if (c) { e.preventDefault(); cargar(+c.dataset.carpeta); }
  };
}

// ------------------------------------------------------------------ buzón

function buzon(d) {
  tarjeta.innerHTML = `${cabeza(d, `${esc(d.de)} te pide archivos para la carpeta`)}
    <div class="buzon" id="buzon">
      ${icono("subir")}
      <p>Arrastra aquí lo que quieras mandar, o elígelo. Solo ${esc(d.de)} podrá verlo; tú no verás lo que han mandado otras personas.</p>
      <label class="boton primario">${icono("archivo")}<span>Elegir archivos</span><input type="file" multiple hidden id="elegir"></label>
    </div>
    <ul class="envios" id="envios"></ul>
    <p class="publico-pie">Si mandas un archivo con un nombre que ya existe, se guarda con otro nombre: nadie pisa lo de nadie.</p>`;
  const zona = $("#buzon");
  $("#elegir").addEventListener("change", (e) => { mandar([...e.target.files]); e.target.value = ""; });
  zona.addEventListener("dragover", (e) => { e.preventDefault(); zona.classList.add("encima"); });
  zona.addEventListener("dragleave", () => zona.classList.remove("encima"));
  zona.addEventListener("drop", (e) => { e.preventDefault(); zona.classList.remove("encima"); mandar([...e.dataTransfer.files]); });
}

const cola = [];
let activos = 0;

function mandar(archivos) {
  for (const file of archivos) {
    const li = crear(`<li class="gota-fila"><span class="gota-nivel" aria-hidden="true"><span class="agua"></span></span>
      <span class="info"><b>${esc(file.name)}</b><small>esperando…</small></span><span></span></li>`);
    $("#envios").prepend(li);
    cola.push({ file, li, id: idAleatorio() });
  }
  siguiente();
}

function siguiente() {
  while (activos < 2 && cola.length) {
    const t = cola.shift();
    activos++;
    const xhr = new XMLHttpRequest();
    const gota = $(".gota-nivel", t.li), estado = $("small", t.li);
    xhr.open("PUT", `${base}/subir?nombre=${encodeURIComponent(t.file.name)}`);
    xhr.setRequestHeader("X-Scloud", "1");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      const p = e.total ? e.loaded / e.total : 0;
      gota.style.setProperty("--p", p);
      estado.textContent = `${Math.floor(p * 100)} % · ${tamano(e.loaded)} de ${tamano(e.total)}`;
    };
    const fin = () => { activos--; siguiente(); };
    xhr.onload = () => {
      let r = {};
      try { r = JSON.parse(xhr.responseText); } catch { /* vacío */ }
      if (xhr.status === 201) {
        gota.style.setProperty("--p", 1);
        gota.classList.add("lista");
        estado.textContent = r.nombre !== t.file.name ? `entregado como «${r.nombre}»` : `entregado · ${tamano(t.file.size)}`;
      } else {
        gota.classList.add("fallo");
        estado.textContent = r.error || `no se pudo mandar (${xhr.status})`;
        aviso(`«${t.file.name}»: ${r.error || "no se pudo mandar"}`, { error: true });
      }
      fin();
    };
    xhr.onerror = () => { gota.classList.add("fallo"); estado.textContent = "se cortó la conexión"; fin(); };
    xhr.send(t.file);
  }
}
