// scloud · la aplicación web.

import { Lluvia } from "./lluvia.js";
import {
  $, $$, api, aviso, copiar, esc, escalonar, fecha, fechaFutura, fechaLarga, guardado, guardar,
  icono, ICONO_TIPO, iniciales, tamano, tipoDe,
} from "./util.js";

const estado = {
  yo: null, vista: null, carpeta: 0, datos: null, items: new Map(), sel: new Set(),
  modo: guardado("scloud-modo", "lista"), vistos: new Set(), eventos: null, sala: null, permiso: "propietario",
};
const lluvia = new Lluvia({ alSubir: () => { refrescoSuave(); actualizarYo(); } });

// ================================================================ arranque

aplicarTema();
arrancar();

async function arrancar() {
  try {
    const yo = await api("/api/yo");
    if (!yo.sesion) {
      $('.conmutador [data-modo="registro"]').hidden = !yo.registro;
      return mostrarAcceso();
    }
    estado.yo = yo;
    mostrarApp();
  } catch (e) {
    mostrarAcceso(e.message);
  }
}

document.addEventListener("sin-sesion", () => {
  estado.eventos?.close();
  estado.eventos = null;
  mostrarAcceso("Tu sesión ha caducado. Vuelve a entrar.");
});

// ================================================================== acceso

let modoAcceso = "entrar";

function mostrarAcceso(mensaje = "") {
  $("#app").hidden = true;
  $("#acceso").hidden = false;
  document.title = "scloud · tu nube privada";
  const error = $(".error-form");
  error.hidden = !mensaje;
  error.textContent = mensaje;
  $('#form-acceso [name="usuario"]').focus();
}

$(".conmutador").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-modo]");
  if (b) cambiarModoAcceso(b.dataset.modo);
});

function cambiarModoAcceso(modo) {
  modoAcceso = modo;
  const registro = modo === "registro";
  $(".conmutador").dataset.modo = modo;
  $$(".conmutador button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.modo === modo)));
  $$("#form-acceso .solo-registro").forEach((el) => { el.hidden = !registro; });
  $('#form-acceso [name="clave"]').autocomplete = registro ? "new-password" : "current-password";
  $("#form-acceso button[type=submit] span").textContent = registro ? "Crear mi nube" : "Entrar en mi nube";
  $(".error-form").hidden = true;
}

$("#form-acceso").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.currentTarget, boton = $("button[type=submit]", f), error = $(".error-form", f);
  const datos = Object.fromEntries(new FormData(f));
  if ((datos.usuario || "").trim().length < 3) return mostrarError("Escribe tu usuario (al menos 3 caracteres).");
  if ((datos.clave || "").length < (modoAcceso === "registro" ? 8 : 1)) {
    return mostrarError(modoAcceso === "registro" ? "La contraseña necesita al menos 8 caracteres." : "Escribe tu contraseña.");
  }
  boton.classList.add("cargando");
  try {
    estado.yo = await api(modoAcceso === "registro" ? "/api/registro" : "/api/entrar", { metodo: "POST", datos });
    f.reset();
    mostrarApp();
    if (modoAcceso === "registro") aviso(`Bienvenida a tu nube, ${estado.yo.visible}. Arrastra archivos aquí para empezar.`, { ms: 6000 });
  } catch (err) {
    mostrarError(err.message);
  } finally {
    boton.classList.remove("cargando");
  }
  function mostrarError(texto) { error.textContent = texto; error.hidden = false; }
});

// ===================================================================== app

function mostrarApp() {
  $("#acceso").hidden = true;
  $("#app").hidden = false;
  pintarYo();
  conectarEventos();
  ruta();
}

function pintarYo() {
  const yo = estado.yo;
  $("#avatar").textContent = iniciales(yo.visible);
  $("#menu-nombre").textContent = yo.visible;
  $("#menu-usuario").textContent = `@${yo.usuario}`;
  const p = yo.cuota ? yo.usado / yo.cuota : 0;
  $("#cuota .gota-nivel").style.setProperty("--p", Math.max(p, yo.usado ? .04 : 0));
  $("#cuota-usado").textContent = tamano(yo.usado);
  $("#cuota-total").textContent = `de ${tamano(yo.cuota)}`;
  $("#cuota").setAttribute("aria-label", `Usas ${tamano(yo.usado)} de ${tamano(yo.cuota)}`);
}

let temporizadorYo;
function actualizarYo() {
  clearTimeout(temporizadorYo);
  temporizadorYo = setTimeout(async () => {
    try { estado.yo = await api("/api/yo"); pintarYo(); } catch { /* sin sesión: ya avisa api() */ }
  }, 400);
}

$("#cuota").addEventListener("click", () => {
  const { usado, cuota } = estado.yo;
  aviso(`Usas ${tamano(usado)} de ${tamano(cuota)} (${Math.round((usado / cuota) * 1000) / 10} %). Quedan ${tamano(cuota - usado)} libres.`);
});

// ================================================================== eventos

function conectarEventos() {
  if (estado.eventos) return;
  const es = new EventSource("/api/eventos");
  estado.eventos = es;
  const leer = (e) => { try { return JSON.parse(e.data); } catch { return {}; } };
  es.addEventListener("hola", (e) => leer(e).transferencias?.forEach((t) => lluvia.desdeServidor(t)));
  es.addEventListener("transferencia", (e) => lluvia.desdeServidor(leer(e)));
  es.addEventListener("cambio", (e) => {
    const { carpetas = [] } = leer(e);
    actualizarYo();
    const v = estado.vista;
    if ((v === "nube" && carpetas.includes(estado.carpeta)) || v === "recientes" || v === "papelera" || v === "enlaces"
        || (v === "compartido" && carpetas.includes("compartido")) || v === "buscar") {
      refrescoSuave();
    }
  });
  es.addEventListener("compartido", (e) => {
    const d = leer(e);
    aviso(`${d.de} ha compartido «${d.nombre}» contigo.`, { accion: "Ver", alPulsar: () => { location.hash = "#/compartido"; }, ms: 8000 });
  });
  es.addEventListener("buzon", (e) => {
    const d = leer(e);
    aviso(`Te han mandado «${d.nombre}» a «${d.carpeta}».`, { ms: 7000 });
  });
  es.addEventListener("equipos", () => { if (estado.vista === "equipos") refrescoSuave(); });
}

let temporizadorRefresco;
function refrescoSuave() {
  clearTimeout(temporizadorRefresco);
  temporizadorRefresco = setTimeout(() => ruta({ suave: true }), 160);
}

// =================================================================== rutas

window.addEventListener("hashchange", () => ruta());
window.addEventListener("resize", () => moverGota());

function ruta({ suave = false } = {}) {
  if ($("#app").hidden) return;
  const h = decodeURIComponent(location.hash.slice(1)) || "/";
  const partes = h.split("/").filter(Boolean);
  const anterior = `${estado.vista}:${estado.carpeta}`;
  clearInterval(estado.sala);
  cerrarMenus();
  let vista = partes[0] || "nube";
  let carpeta = 0;
  if (vista === "c") { vista = "nube"; carpeta = +partes[1] || 0; }
  const nueva = `${vista}:${carpeta}`;
  if (nueva !== anterior) { estado.sel.clear(); estado.vistos = new Set(); }
  estado.vista = vista;
  estado.carpeta = carpeta;
  marcarEspacio(vista === "buscar" ? null : vista);
  pintarSeleccion();
  const vistas = { nube: () => vistaNube(carpeta, suave), recientes: () => vistaRecientes(suave), compartido: () => vistaCompartido(suave),
    enlaces: () => vistaEnlaces(suave), equipos: () => vistaEquipos(suave), papelera: () => vistaPapelera(suave),
    buscar: () => vistaBuscar(partes.slice(1).join("/"), suave) };
  (vistas[vista] || vistas.nube)();
  if (!suave && nueva !== anterior) $("#contenido").focus({ preventScroll: true });
}

function marcarEspacio(vista) {
  $$(".espacios a").forEach((a) => {
    if (a.dataset.vista === vista) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  moverGota();
}

function moverGota() {
  const gota = $(".espacios-gota");
  const a = $(".espacios a[aria-current=page]");
  gota.style.opacity = a ? 1 : 0;
  if (!a) return;
  gota.style.width = `${a.offsetWidth}px`;
  gota.style.transform = `translateX(${a.offsetLeft}px)`;
}

function cabecera({ titulo, migas = [], nota = "", acciones = "" }) {
  $("#titulo").textContent = titulo;
  document.title = `${titulo} · scloud`;
  $("#migas").innerHTML = migas.map((m, i) =>
    `${i ? '<span class="sep" aria-hidden="true">/</span>' : ""}<a href="${m.href}"${m.destino !== undefined ? ` data-destino="${m.destino}"` : ""}>${esc(m.nombre)}</a>`).join("");
  $("#migas").hidden = !migas.length;
  $("#nota").innerHTML = nota;
  $("#nota").hidden = !nota;
  $("#acciones").innerHTML = acciones;
}

function esqueleto() {
  $("#vista").innerHTML = `<ul class="esqueleto" aria-label="Cargando">${"<li></li>".repeat(6)}</ul>`;
}

function errorVista(e) {
  $("#vista").innerHTML = `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-nube"/></svg>
    <h2>No se ha podido abrir</h2><p>${esc(e.message)}</p><div class="acciones"><a class="boton" href="#/">Volver a Mi nube</a></div></div>`;
}

// =============================================================== vista nube

const botonModo = () => `<button class="boton icono" data-accion="modo" aria-label="${estado.modo === "lista" ? "Ver en cuadrícula" : "Ver en lista"}">
  ${icono(estado.modo === "lista" ? "cuadricula" : "lista")}</button>`;

async function vistaNube(id, suave) {
  if (!suave) esqueleto();
  let d;
  try { d = await api(`/api/carpeta/${id}`); } catch (e) { if (estado.carpeta === id) errorVista(e); return; }
  if (estado.vista !== "nube" || estado.carpeta !== id) return;
  estado.datos = d;
  estado.permiso = d.permiso;
  const puede = d.permiso !== "ver";
  const migas = d.ruta.slice(0, -1).map((m) => ({
    nombre: m.nombre, href: m.id === "compartido" ? "#/compartido" : m.id ? `#/c/${m.id}` : "#/",
    destino: m.id === "compartido" ? undefined : m.id,
  }));
  let nota = "";
  if (d.dueno) {
    nota = `<span class="pildora acento">${icono("personas")} de ${esc(d.dueno)}</span>
      <span>${puede ? "Puedes subir, renombrar y mover cosas aquí." : "Solo puedes ver y descargar."}</span>`;
  }
  const acciones = [
    puede ? `<button class="boton primario" data-accion="subir" aria-haspopup="menu">${icono("subir")}<span>Subir</span></button>` : "",
    puede ? `<button class="boton" data-accion="carpeta">${icono("carpeta-mas")}<span>Nueva carpeta</span></button>` : "",
    d.permiso === "propietario" && id ? `<button class="boton" data-accion="compartir-actual">${icono("compartir")}<span>Compartir</span></button>` : "",
    d.hijos.length ? botonModo() : "",
  ].join("");
  cabecera({ titulo: d.carpeta.nombre, migas, nota, acciones });
  const items = d.hijos.map((h) => ({ ...h, permiso: d.permiso }));
  pintarLista(items, {
    suave,
    vacio: puede
      ? { titulo: id ? "Esta carpeta está vacía" : "Tu nube está despejada", texto: "Arrastra archivos o carpetas aquí, o súbelos con el botón. También puedes sincronizar una carpeta de tu ordenador desde Equipos.", accion: `<button class="boton primario" data-accion="subir">${icono("subir")}<span>Subir archivos</span></button>` }
      : { titulo: "No hay nada todavía", texto: "Cuando su dueño ponga algo aquí, lo verás al momento." },
  });
}

// ------------------------------------------------------------------ lista

function pintarLista(items, { suave = false, vacio, sub } = {}) {
  estado.items = new Map(items.map((i) => [i.id, i]));
  for (const id of [...estado.sel]) if (!estado.items.has(id)) estado.sel.delete(id);
  pintarSeleccion();
  if (!items.length) {
    $("#vista").innerHTML = `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-nube"/></svg>
      <h2>${esc(vacio.titulo)}</h2><p>${esc(vacio.texto)}</p>${vacio.accion ? `<div class="acciones">${vacio.accion}</div>` : ""}</div>`;
    estado.vistos = new Set();
    return;
  }
  const cuadricula = estado.modo === "cuadricula";
  const filas = items.map((it, i) => fila(it, i, sub)).join("");
  $("#vista").innerHTML = cuadricula
    ? `<ul class="cuadricula" aria-label="Contenido">${filas}</ul>`
    : `<div class="cabecera-lista" aria-hidden="true"><span></span><span>Nombre</span><span class="der">Tamaño</span><span>Modificado</span><span></span></div>
       <ul class="lista${estado.sel.size ? " seleccionando" : ""}" aria-label="Contenido">${filas}</ul>`;
  const nuevos = suave && estado.vistos.size ? items.filter((it) => !estado.vistos.has(it.id)).map((it) => it.id) : [];
  $$(".fila", $("#vista")).forEach((li) => {
    if (suave) li.style.animation = "none";
    if (nuevos.includes(+li.dataset.id)) { li.style.animation = ""; li.classList.add("llega"); }
  });
  if (!suave) escalonar($("#vista"));
  estado.vistos = new Set(items.map((it) => it.id));
}

function fila(it, i, sub) {
  const tipo = tipoDe(it);
  const editable = it.permiso === "propietario" || it.permiso === "editar";
  const mini = estado.modo === "cuadricula" && tipo === "imagen" && it.tamano < 15e6 && !/\.(heic|tiff?|svg)$/i.test(it.nombre)
    ? `<img src="/api/bajar/${it.id}?ver=1" alt="" loading="lazy" decoding="async">` : icono(ICONO_TIPO[tipo]);
  const tam = it.tipo === "carpeta" ? "—" : tamano(it.tamano);
  const extra = sub ? sub(it) : "";
  const movil = [it.tipo === "carpeta" ? "" : tamano(it.tamano), fecha(it.modificado)].filter(Boolean).join(" · ");
  return `<li class="fila" data-id="${it.id}" data-tipo="${it.tipo}" data-i="${i}" aria-selected="${estado.sel.has(it.id)}"${editable ? ' draggable="true"' : ""}>
    <span class="marca-sel"><span class="tipo ${tipo}">${mini}</span>
      <input type="checkbox" class="casilla" aria-label="Elegir ${esc(it.nombre)}"${estado.sel.has(it.id) ? " checked" : ""}></span>
    <span class="nombre-col">
      <span class="nombre"><button class="abrir" type="button" title="${esc(it.nombre)}">${esc(it.nombre)}</button>
        <span class="marcas">${it.compartido ? `<span title="Compartido con otras personas">${icono("personas")}</span>` : ""}${it.enlace ? `<span title="Tiene enlace">${icono("enlace")}</span>` : ""}</span></span>
      ${extra ? `<span class="sub">${extra}</span>` : `<span class="sub movil">${esc(movil)}</span>`}
    </span>
    <span class="tam der">${tam}</span>
    <span class="fecha" title="${it.modificado ? esc(fechaLarga(it.modificado)) : ""}">${fecha(it.modificado)}</span>
    <button class="mas-acciones" type="button" aria-label="Más acciones para ${esc(it.nombre)}" aria-haspopup="menu">${icono("puntos")}</button>
  </li>`;
}

// ------------------------------------------- clics en la lista (delegados)

$("#vista").addEventListener("click", (e) => {
  const li = e.target.closest(".fila[data-id]");
  const accion = e.target.closest("[data-accion]");
  if (accion) return hacerAccion(accion.dataset.accion, accion);
  if (!li) return;
  const it = estado.items.get(+li.dataset.id);
  if (!it) return;
  if (e.target.closest(".mas-acciones")) return menuNodo(it, e.target.closest(".mas-acciones"));
  if (e.target.closest(".abrir")) return abrir(it);
  if (e.target.closest(".casilla")) return;
  if (estado.modo === "cuadricula" && e.target.closest(".marca-sel")) return abrir(it);
});
$("#vista").addEventListener("change", (e) => {
  const c = e.target.closest(".casilla");
  if (!c) return;
  const id = +c.closest(".fila").dataset.id;
  c.checked ? estado.sel.add(id) : estado.sel.delete(id);
  c.closest(".fila").setAttribute("aria-selected", String(c.checked));
  pintarSeleccion();
});
$("#vista").addEventListener("dblclick", (e) => {
  const li = e.target.closest(".fila[data-id]");
  if (li && !e.target.closest("button, input")) abrir(estado.items.get(+li.dataset.id));
});
$("#vista").addEventListener("contextmenu", (e) => {
  const li = e.target.closest(".fila[data-id]");
  if (!li || !estado.items.has(+li.dataset.id)) return;
  e.preventDefault();
  menuNodo(estado.items.get(+li.dataset.id), { x: e.clientX, y: e.clientY });
});
$("#acciones").addEventListener("click", (e) => {
  const b = e.target.closest("[data-accion]");
  if (b) hacerAccion(b.dataset.accion, b);
});

function hacerAccion(accion, boton) {
  const acciones = {
    subir: () => menuSubir(boton),
    carpeta: () => nuevaCarpeta(),
    modo: () => { estado.modo = estado.modo === "lista" ? "cuadricula" : "lista"; guardar("scloud-modo", estado.modo); ruta(); },
    "compartir-actual": () => dialogoCompartir(estado.carpeta),
    vaciar: () => vaciarPapelera(),
    "conectar-equipo": () => conectarEquipo(),
    recuperar: () => recuperar(+boton.dataset.id),
    eliminar: () => eliminar(+boton.dataset.id),
    "quitar-enlace": () => quitarEnlace(+boton.dataset.id),
    "ver-enlace": () => dialogoCompartir(+boton.dataset.id),
    "quitar-equipo": () => quitarEquipo(+boton.dataset.id, boton.dataset.nombre),
  };
  acciones[accion]?.();
}

function abrir(it) {
  if (!it) return;
  if (it.tipo === "carpeta") location.hash = `#/c/${it.id}`;
  else vistaPrevia(it);
}

function descargar(it) {
  const a = document.createElement("a");
  a.href = `/api/bajar/${it.id}`;
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
}

// ================================================================= menús

function cerrarMenus() {
  $("#menu-nodo").hidden = true;
  $("#menu-cuenta").hidden = true;
  $("#avatar").setAttribute("aria-expanded", "false");
}

function abrirMenuEn(menu, ancla) {
  menu.hidden = false;
  const r = ancla instanceof Element ? ancla.getBoundingClientRect() : { left: ancla.x, right: ancla.x, top: ancla.y, bottom: ancla.y };
  const ancho = menu.offsetWidth, alto = menu.offsetHeight;
  let x = Math.min(r.right - ancho, innerWidth - ancho - 12);
  if (x < 12) x = Math.min(r.left, innerWidth - ancho - 12);
  let y = r.bottom + 6;
  if (y + alto > innerHeight - 12) y = Math.max(12, r.top - alto - 6);
  menu.style.left = `${Math.max(12, x)}px`;
  menu.style.top = `${y}px`;
  $("button", menu)?.focus();
}

function menuNodo(it, ancla) {
  const editable = it.permiso === "propietario" || it.permiso === "editar";
  const opciones = [
    ["abrir", it.tipo === "carpeta" ? "Abrir" : "Vista previa", it.tipo === "carpeta" ? "carpeta" : "buscar"],
    ["descargar", it.tipo === "carpeta" ? "Descargar en ZIP" : "Descargar", "bajar"],
    it.permiso === "propietario" ? ["compartir", "Compartir y enlaces…", "compartir"] : null,
    it.padre_id !== undefined ? ["ir", "Abrir su carpeta", "carpeta"] : null,
    "—",
    editable && estado.vista !== "compartido" ? ["renombrar", "Renombrar", "lapiz"] : null,
    editable && estado.vista !== "compartido" ? ["mover", "Mover a…", "mover"] : null,
    editable && estado.vista !== "compartido" ? ["papelera", "A la papelera", "papelera", "peligro"] : null,
    estado.vista === "compartido" ? ["salir", "Quitar de mi lista", "cerrar", "peligro"] : null,
  ].filter(Boolean);
  const menu = $("#menu-nodo");
  menu.innerHTML = opciones.map((o) => o === "—" ? "<hr>" :
    `<button role="menuitem" data-op="${o[0]}"${o[3] ? ` class="${o[3]}"` : ""}>${icono(o[2])}${esc(o[1])}</button>`).join("").replace(/<hr>$/, "");
  menu.onclick = (e) => {
    const b = e.target.closest("[data-op]");
    if (!b) return;
    cerrarMenus();
    ({
      abrir: () => abrir(it), descargar: () => descargar(it), compartir: () => dialogoCompartir(it.id),
      ir: () => { location.hash = it.padre_id ? `#/c/${it.padre_id}` : "#/"; },
      renombrar: () => renombrar(it), mover: () => dialogoMover([it.id]), papelera: () => aPapelera([it.id]),
      salir: () => salirCompartido(it),
    })[b.dataset.op]();
  };
  abrirMenuEn(menu, ancla);
}

function menuSubir(boton) {
  const menu = $("#menu-nodo");
  menu.innerHTML = `<button role="menuitem" data-op="archivos">${icono("archivo")}Archivos</button>
    <button role="menuitem" data-op="carpeta">${icono("carpeta")}Una carpeta entera</button>`;
  menu.onclick = (e) => {
    const b = e.target.closest("[data-op]");
    if (!b) return;
    cerrarMenus();
    $(b.dataset.op === "archivos" ? "#elegir-archivos" : "#elegir-carpeta").click();
  };
  abrirMenuEn(menu, boton);
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu, [aria-haspopup], .mas-acciones")) cerrarMenus();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("#menu-nodo").hidden || !$("#menu-cuenta").hidden) return cerrarMenus();
    if (estado.sel.size && !$("#dialogo").open) { limpiarSeleccion(); }
  }
  if ((e.key === "Delete" || e.key === "Supr") && estado.sel.size && !e.target.closest("input, textarea, dialog")) {
    aPapelera([...estado.sel]);
  }
});

// cuenta
$("#avatar").addEventListener("click", () => {
  const m = $("#menu-cuenta");
  const abrirlo = m.hidden;
  cerrarMenus();
  m.hidden = !abrirlo;
  $("#avatar").setAttribute("aria-expanded", String(abrirlo));
  $("#tema-texto").textContent = temaOscuro() ? "Cielo de día" : "Cielo de noche";
  if (abrirlo) $("button", m).focus();
});
$("#menu-cuenta").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-accion]");
  if (!b) return;
  cerrarMenus();
  if (b.dataset.accion === "salir") {
    await api("/api/salir", { metodo: "POST" }).catch(() => {});
    estado.eventos?.close(); estado.eventos = null;
    location.hash = "";
    mostrarAcceso();
  } else if (b.dataset.accion === "tema") {
    const nuevo = temaOscuro() ? "claro" : "oscuro";
    document.documentElement.dataset.tema = nuevo;
    guardar("scloud-tema", nuevo);
  } else if (b.dataset.accion === "sesiones") {
    if (await confirmar({ titulo: "¿Cerrar las demás sesiones?", texto: "Se cerrará scloud en los demás navegadores donde hayas entrado. Esta pestaña sigue abierta. Tus equipos con el cliente de escritorio no se desconectan: eso se hace en Equipos.", boton: "Cerrarlas" })) {
      await api("/api/cerrar-sesiones", { metodo: "POST" });
      aviso("Listo: solo queda abierta esta sesión.");
    }
  } else if (b.dataset.accion === "clave") {
    cambiarClave();
  } else if (b.dataset.accion === "borrar-cuenta") {
    borrarCuenta();
  }
});

function borrarCuenta() {
  const d = dialogo({
    titulo: "¿Borrar tu cuenta?",
    sub: "Se borrarán para siempre todos tus archivos, la papelera, tus enlaces y tus equipos. Lo que te han compartido otras personas sigue siendo suyo.",
    cuerpo: `<form class="formulario" id="form-borrar">
      <input type="text" name="usuario" autocomplete="username" value="${esc(estado.yo.usuario)}" hidden>
      <label class="campo"><span>Escribe tu contraseña para confirmarlo</span><input type="password" name="clave" autocomplete="current-password" required></label>
      <p class="error-form" role="alert" hidden></p></form>`,
    pie: `<button class="boton" type="button" data-cerrar>Cancelar</button><button class="boton peligro lleno" type="submit" form="form-borrar">Borrar mi cuenta</button>`,
  });
  $('[name="clave"]', d).focus();
  $("#form-borrar").addEventListener("submit", async (e) => {
    e.preventDefault();
    const clave = e.currentTarget.clave.value;
    try {
      await api("/api/borrar-cuenta", { metodo: "POST", datos: { clave } });
      estado.eventos?.close(); estado.eventos = null;
      cerrarDialogo();
      location.hash = "";
      mostrarAcceso();
      aviso("Tu cuenta y todos tus archivos se han borrado.");
    } catch (err) { const p = $(".error-form", d); p.textContent = err.message; p.hidden = false; }
  });
}

function aplicarTema() {
  const t = guardado("scloud-tema", "");
  if (t) document.documentElement.dataset.tema = t;
}
function temaOscuro() {
  const t = document.documentElement.dataset.tema;
  return t ? t === "oscuro" : matchMedia("(prefers-color-scheme: dark)").matches;
}

// ============================================================= selección

function pintarSeleccion() {
  const n = estado.sel.size;
  $("#seleccion").hidden = !n || !["nube", "recientes", "buscar", "compartido"].includes(estado.vista);
  $("#sel-cuenta").textContent = n === 1 ? "1 elegido" : `${n} elegidos`;
  $("#lluvia").classList.toggle("con-seleccion", !$("#seleccion").hidden);
  $(".lista")?.classList.toggle("seleccionando", n > 0);
  const editables = [...estado.sel].every((id) => ["propietario", "editar"].includes(estado.items.get(id)?.permiso));
  $('[data-sel="mover"]').hidden = !editables || estado.vista === "compartido";
  $('[data-sel="papelera"]').hidden = !editables || estado.vista === "compartido";
}

function limpiarSeleccion() {
  estado.sel.clear();
  $$(".fila").forEach((f) => { f.setAttribute("aria-selected", "false"); const c = $(".casilla", f); if (c) c.checked = false; });
  pintarSeleccion();
}

$("#seleccion").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sel]");
  if (!b) return;
  const ids = [...estado.sel];
  ({
    bajar: () => {
      if (ids.length === 1) return descargar(estado.items.get(ids[0]));
      const a = document.createElement("a");
      a.href = `/api/zip?ids=${ids.join(",")}`; a.download = ""; document.body.append(a); a.click(); a.remove();
    },
    mover: () => dialogoMover(ids),
    papelera: () => aPapelera(ids),
    limpiar: () => limpiarSeleccion(),
  })[b.dataset.sel]();
});

// ============================================================= diálogos

function dialogo({ titulo, sub = "", cuerpo = "", pie = "", ancho = false }) {
  const d = $("#dialogo");
  d.className = `dialogo${ancho ? " ancho" : ""}`;
  d.innerHTML = `<div class="dialogo-cabeza"><div><h2 id="dialogo-titulo">${esc(titulo)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>
      <button class="boton icono" type="button" data-cerrar aria-label="Cerrar">${icono("cerrar")}</button></div>
    <div class="dialogo-cuerpo">${cuerpo}</div>${pie ? `<div class="dialogo-pie">${pie}</div>` : ""}`;
  d.setAttribute("aria-labelledby", "dialogo-titulo");
  if (!d.open) d.showModal();
  return d;
}

function cerrarDialogo() {
  const d = $("#dialogo");
  if (!d.open || d.classList.contains("cerrando")) return;
  d.classList.add("cerrando");
  const fin = () => { d.classList.remove("cerrando"); d.close(); d.innerHTML = ""; };
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) fin();
  else d.addEventListener("animationend", fin, { once: true });
}

$("#dialogo").addEventListener("click", (e) => {
  if (e.target === e.currentTarget || e.target.closest("[data-cerrar]")) cerrarDialogo();
});
$("#dialogo").addEventListener("cancel", (e) => { e.preventDefault(); cerrarDialogo(); });

function pedirTexto({ titulo, etiqueta, valor = "", boton = "Guardar", seleccionar }) {
  return new Promise((resolver) => {
    const d = dialogo({
      titulo,
      cuerpo: `<form class="formulario" id="form-texto"><label class="campo"><span>${esc(etiqueta)}</span>
        <input name="texto" value="${esc(valor)}" required maxlength="255" autocomplete="off"></label>
        <p class="error-form" role="alert" hidden></p></form>`,
      pie: `<button class="boton" type="button" data-cerrar>Cancelar</button><button class="boton primario" type="submit" form="form-texto">${esc(boton)}</button>`,
    });
    const input = $("input", d);
    input.focus();
    if (seleccionar) input.setSelectionRange(0, seleccionar);
    else input.select();
    let hecho = false;
    $("#form-texto").addEventListener("submit", (e) => {
      e.preventDefault();
      hecho = true;
      resolver({ texto: input.value.trim(), error: (m) => { const p = $(".error-form", d); p.textContent = m; p.hidden = false; hecho = false; } });
    });
    d.addEventListener("close", () => { if (!hecho) resolver(null); }, { once: true });
  });
}

function confirmar({ titulo, texto, boton = "Sí", peligro = false }) {
  return new Promise((resolver) => {
    const d = dialogo({
      titulo, cuerpo: `<p>${esc(texto)}</p>`,
      pie: `<button class="boton" type="button" data-cerrar>Cancelar</button>
        <button class="boton ${peligro ? "peligro lleno" : "primario"}" type="button" data-si>${esc(boton)}</button>`,
    });
    let si = false;
    $("[data-si]", d).addEventListener("click", () => { si = true; cerrarDialogo(); });
    $("[data-si]", d).focus();
    d.addEventListener("close", () => resolver(si), { once: true });
  });
}

// ------------------------------------------------------------ acciones

async function nuevaCarpeta() {
  let r = await pedirTexto({ titulo: "Nueva carpeta", etiqueta: "Nombre", valor: "", boton: "Crear carpeta" });
  while (r) {
    try {
      const c = await api("/api/carpeta", { metodo: "POST", datos: { padre: estado.carpeta, nombre: r.texto } });
      cerrarDialogo();
      estado.vistos.delete(c.id);
      refrescoSuave();
      return;
    } catch (e) {
      r.error(e.message);
      r = await new Promise((ok) => $("#form-texto").addEventListener("submit", () => ok({ texto: $("#form-texto input").value.trim(), error: r.error }), { once: true }));
    }
  }
}

async function renombrar(it) {
  const punto = it.tipo === "archivo" ? it.nombre.lastIndexOf(".") : -1;
  let r = await pedirTexto({ titulo: `Renombrar «${it.nombre}»`, etiqueta: "Nombre nuevo", valor: it.nombre, boton: "Renombrar", seleccionar: punto > 0 ? punto : undefined });
  while (r) {
    try {
      await api(`/api/nodo/${it.id}`, { metodo: "PATCH", datos: { nombre: r.texto } });
      cerrarDialogo();
      refrescoSuave();
      return;
    } catch (e) {
      r.error(e.message);
      r = await new Promise((ok) => $("#form-texto").addEventListener("submit", () => ok({ texto: $("#form-texto input").value.trim(), error: r.error }), { once: true }));
    }
  }
}

async function aPapelera(ids) {
  const nombres = ids.map((id) => estado.items.get(id)?.nombre).filter(Boolean);
  try {
    await api("/api/a-papelera", { metodo: "POST", datos: { ids } });
  } catch (e) { return aviso(e.message, { error: true }); }
  limpiarSeleccion();
  refrescoSuave();
  actualizarYo();
  const propios = ids.filter((id) => estado.items.get(id)?.permiso === "propietario");
  aviso(nombres.length === 1 ? `«${nombres[0]}» está en la papelera.` : `${nombres.length} elementos a la papelera.`, {
    accion: propios.length === ids.length ? "Deshacer" : undefined,
    alPulsar: async () => {
      for (const id of ids) await api(`/api/papelera/${id}/recuperar`, { metodo: "POST" }).catch(() => {});
      refrescoSuave();
    },
    ms: 7000,
  });
}

async function salirCompartido(it) {
  if (!await confirmar({ titulo: `¿Quitar «${it.nombre}» de tu lista?`, texto: `Dejarás de verlo. ${it.dueno || "Su dueño"} lo seguirá teniendo y te lo puede volver a compartir.`, boton: "Quitar", peligro: true })) return;
  try { await api(`/api/compartido/nodo/${it.id}`, { metodo: "DELETE" }); refrescoSuave(); } catch (e) { aviso(e.message, { error: true }); }
}

async function cambiarClave() {
  const d = dialogo({
    titulo: "Cambiar contraseña",
    sub: "Se cerrarán tus otras sesiones del navegador. Los equipos conectados siguen funcionando.",
    cuerpo: `<form class="formulario" id="form-clave">
      <input type="text" name="usuario" autocomplete="username" value="${esc(estado.yo.usuario)}" hidden>
      <label class="campo"><span>Contraseña actual</span><input type="password" name="actual" autocomplete="current-password" required></label>
      <label class="campo"><span>Contraseña nueva</span><input type="password" name="nueva" autocomplete="new-password" minlength="8" required><small class="ayuda">Al menos 8 caracteres.</small></label>
      <p class="error-form" role="alert" hidden></p></form>`,
    pie: `<button class="boton" type="button" data-cerrar>Cancelar</button><button class="boton primario" type="submit" form="form-clave">Cambiar</button>`,
  });
  $('[name="actual"]', d).focus();
  $("#form-clave").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/clave", { metodo: "POST", datos: Object.fromEntries(new FormData(e.currentTarget)) });
      cerrarDialogo();
      aviso("Contraseña cambiada.");
    } catch (err) { const p = $(".error-form", d); p.textContent = err.message; p.hidden = false; }
  });
}

// ----------------------------------------------------------- vista previa

async function vistaPrevia(it) {
  const tipo = tipoDe(it);
  const url = `/api/bajar/${it.id}?ver=1`;
  const textoPlano = (tipo === "texto" || tipo === "codigo") && /\.(txt|md|csv|log|json|py|js|ts|css|html?|sbd|xml|ya?ml|sh|sql|java|c|cpp|h|rs|go|php|rb)$/i.test(it.nombre);
  let contenido;
  if (tipo === "imagen" && !/\.(heic|tiff?|svg)$/i.test(it.nombre)) contenido = `<img src="${url}" alt="${esc(it.nombre)}">`;
  else if (tipo === "video") contenido = `<video src="${url}" controls preload="metadata"></video>`;
  else if (tipo === "audio") contenido = `<audio src="${url}" controls preload="metadata"></audio>`;
  else if (textoPlano && it.tamano < 512 * 1024) contenido = `<pre>Cargando…</pre>`;
  else contenido = `<div class="vacio"><span class="tipo ${tipo}">${icono(ICONO_TIPO[tipo])}</span><p>Este tipo de archivo no se puede ver aquí. Descárgalo para abrirlo.</p></div>`;
  const d = dialogo({
    titulo: it.nombre, ancho: true,
    sub: `${tamano(it.tamano)} · ${it.modificado ? fechaLarga(it.modificado) : ""}${it.version > 1 ? ` · versión ${it.version}` : ""}`,
    cuerpo: `<div class="vista-previa">${contenido}</div>`,
    pie: `${it.permiso === "propietario" ? `<button class="boton" type="button" data-op="compartir">${icono("compartir")}<span>Compartir</span></button>` : ""}
      ${tipo === "pdf" ? `<a class="boton" href="${url}" target="_blank" rel="noopener">Abrir en otra pestaña</a>` : ""}
      <button class="boton primario" type="button" data-op="descargar">${icono("bajar")}<span>Descargar</span></button>`,
  });
  $('[data-op="descargar"]', d).addEventListener("click", () => descargar(it));
  $('[data-op="compartir"]', d)?.addEventListener("click", () => dialogoCompartir(it.id));
  if (contenido.startsWith("<pre>")) {
    try {
      const r = await fetch(url, { credentials: "same-origin" });
      const texto = await r.text();
      const pre = $(".vista-previa pre", d);
      if (pre) pre.textContent = texto;
    } catch { /* se queda en «Cargando…» */ }
  }
}

// --------------------------------------------------------------- mover

async function dialogoMover(ids) {
  const moviendo = new Set(ids);
  const titulo = ids.length === 1 ? `Mover «${estado.items.get(ids[0])?.nombre ?? ""}»` : `Mover ${ids.length} elementos`;
  let actual = estado.vista === "nube" ? estado.carpeta : 0;
  const d = dialogo({ titulo, sub: "Elige la carpeta de destino.", cuerpo: `<nav class="migas" aria-label="Carpeta"></nav><ul class="carpetas-mover"></ul>`,
    pie: `<button class="boton" type="button" data-cerrar>Cancelar</button><button class="boton primario" type="button" data-aqui>Mover aquí</button>` });

  async function pintar() {
    let datos;
    try { datos = await api(`/api/carpeta/${actual}`); } catch (e) { return aviso(e.message, { error: true }); }
    $(".migas", d).innerHTML = datos.ruta.map((m, i) => `${i ? '<span class="sep">/</span>' : ""}${m.id === "compartido" ? `<span>${esc(m.nombre)}</span>` : `<a href="#" data-ir="${m.id}">${esc(m.nombre)}</a>`}`).join("");
    const carpetas = datos.hijos.filter((h) => h.tipo === "carpeta" && !moviendo.has(h.id));
    $(".carpetas-mover", d).innerHTML = carpetas.length
      ? carpetas.map((c) => `<li><button type="button" data-ir="${c.id}"><span class="tipo carpeta">${icono("carpeta")}</span>${esc(c.nombre)}</button></li>`).join("")
      : `<li class="tenue">No hay subcarpetas aquí.</li>`;
    $("[data-aqui]", d).textContent = `Mover a «${datos.carpeta.nombre}»`;
    $("[data-aqui]", d).disabled = datos.permiso === "ver";
  }
  d.onclick = async (e) => {
    const ir = e.target.closest("[data-ir]");
    if (ir) { e.preventDefault(); actual = +ir.dataset.ir; return pintar(); }
    if (e.target.closest("[data-aqui]")) {
      try {
        const r = await api("/api/mover", { metodo: "POST", datos: { ids, destino: actual } });
        if (r.errores?.length) aviso(r.errores[0], { error: true });
        cerrarDialogo(); limpiarSeleccion(); refrescoSuave();
        if (!r.errores?.length) aviso(ids.length === 1 ? "Movido." : `${ids.length} elementos movidos.`, { accion: "Ir allí", alPulsar: () => { location.hash = actual ? `#/c/${actual}` : "#/"; } });
      } catch (err) { aviso(err.message, { error: true }); }
    }
    if (e.target === d || e.target.closest("[data-cerrar]")) cerrarDialogo();
  };
  pintar();
}

// ----------------------------------------------------------- compartir

async function dialogoCompartir(nodoId) {
  let datos;
  try { datos = await api(`/api/nodo/${nodoId}/acceso`); } catch (e) { return aviso(e.message, { error: true }); }
  const esCarpeta = datos.nodo.tipo === "carpeta";
  const d = dialogo({
    titulo: `Compartir «${datos.nodo.nombre}»`,
    sub: esCarpeta ? "Quien tenga acceso a la carpeta lo tiene también a todo lo que hay dentro." : "",
    cuerpo: `<section class="dialogo-seccion" aria-labelledby="t-personas"><h3 id="t-personas">Personas con cuenta en scloud</h3>
        <form class="agregar" autocomplete="off">
          <input class="entrada" name="usuario" placeholder="Su usuario, por ejemplo: lucia.perez" aria-label="Usuario de la otra persona" autocapitalize="none" spellcheck="false">
          <select name="permiso" aria-label="Permiso"><option value="ver">Puede ver</option><option value="editar">Puede editar</option></select>
          <button class="boton primario" type="submit">Compartir</button>
          <ul class="sugerencias" hidden></ul>
        </form>
        <ul class="personas"></ul><div class="heredado-caja"></div></section>
      <section class="dialogo-seccion" aria-labelledby="t-enlaces"><h3 id="t-enlaces">Enlaces</h3>
        <div class="resultado-enlace"></div>
        <ul class="enlaces-lista"></ul>
        <details class="crear-enlace"><summary class="boton suave">${icono("enlace")}<span>Crear un enlace</span></summary>
          <form class="opciones-enlace formulario">
            ${esCarpeta ? `<label class="campo"><span>Para qué</span><select name="permiso"><option value="descargar">Que puedan ver y descargar</option><option value="subir">Pedir archivos (buzón: suben, pero no ven nada)</option></select></label>` : ""}
            <div class="fila-campos">
              <label class="campo"><span>Caduca</span><select name="dias"><option value="0">Nunca</option><option value="1">En 1 día</option><option value="7" selected>En 7 días</option><option value="30">En 30 días</option></select></label>
              <label class="campo"><span>Máximo de descargas</span><input name="max_descargas" type="number" min="0" max="100000" inputmode="numeric" placeholder="Sin límite"></label>
            </div>
            <label class="campo"><span>Contraseña (opcional)</span><input name="clave" type="text" minlength="4" autocomplete="off" placeholder="Sin contraseña"></label>
            <button class="boton primario" type="submit">Crear enlace</button>
          </form></details>
      </section>`,
  });
  const pintarPersonas = (a) => {
    $(".personas", d).innerHTML = a.personas.length ? a.personas.map((p) => `<li class="persona">
        <span class="avatar">${esc(iniciales(p.visible))}</span>
        <span class="quien"><b>${esc(p.visible)}</b><small>@${esc(p.usuario)}</small></span>
        <select data-permiso="${p.id}" aria-label="Permiso de ${esc(p.visible)}"><option value="ver"${p.permiso === "ver" ? " selected" : ""}>Puede ver</option><option value="editar"${p.permiso === "editar" ? " selected" : ""}>Puede editar</option></select>
        <button class="boton icono quitar" type="button" data-quitar="${p.id}" aria-label="Dejar de compartir con ${esc(p.visible)}">${icono("cerrar")}</button></li>`).join("")
      : `<li class="tenue">Todavía no lo has compartido con nadie.</li>`;
    $(".heredado-caja", d).innerHTML = a.heredado.length
      ? `<p class="heredado">Por estar dentro de otra carpeta compartida también tienen acceso: ${a.heredado.map((h) => `${esc(h.visible)} (${h.permiso === "editar" ? "puede editar" : "puede ver"}, por «${esc(h.desde_carpeta)}»)`).join(", ")}.</p>` : "";
    $(".enlaces-lista", d).innerHTML = a.enlaces.map((e) => `<li class="enlace-fila">
        <span class="detalle">${detallesEnlace(e)}</span>
        <button class="boton suave peligro" type="button" data-quitar-enlace="${e.id}">Quitar</button></li>`).join("");
  };
  pintarPersonas(datos);

  const form = $(".agregar", d), entrada = $(".entrada", form), sug = $(".sugerencias", form);
  let temporizador;
  entrada.addEventListener("input", () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(async () => {
      const q = entrada.value.trim();
      if (q.length < 3) { sug.hidden = true; return; }
      const gente = await api(`/api/personas?q=${encodeURIComponent(q)}`).catch(() => []);
      sug.innerHTML = gente.map((g) => `<li><button type="button" data-usuario="${esc(g.nombre)}">${esc(g.visible)} <small>@${esc(g.nombre)}</small></button></li>`).join("");
      sug.hidden = !gente.length;
    }, 200);
  });
  sug.addEventListener("click", (e) => {
    const b = e.target.closest("[data-usuario]");
    if (b) { entrada.value = b.dataset.usuario; sug.hidden = true; entrada.focus(); }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    sug.hidden = true;
    if (!entrada.value.trim()) return entrada.focus();
    try {
      const a = await api(`/api/nodo/${nodoId}/compartir`, { metodo: "POST", datos: { usuario: entrada.value.trim(), permiso: form.permiso.value } });
      entrada.value = "";
      pintarPersonas(a);
      refrescoSuave();
    } catch (err) { aviso(err.message, { error: true }); }
  });
  d.onchange = async (e) => {
    const s = e.target.closest("[data-permiso]");
    if (!s) return;
    try { pintarPersonas(await api(`/api/compartido/${s.dataset.permiso}`, { metodo: "PATCH", datos: { permiso: s.value } })); }
    catch (err) { aviso(err.message, { error: true }); }
  };
  d.onclick = async (e) => {
    if (e.target === d || e.target.closest("[data-cerrar]")) return cerrarDialogo();
    const q = e.target.closest("[data-quitar]");
    const qe = e.target.closest("[data-quitar-enlace]");
    const cp = e.target.closest("[data-copiar]");
    try {
      if (q) await api(`/api/compartido/${q.dataset.quitar}`, { metodo: "DELETE" });
      if (qe) await api(`/api/enlace/${qe.dataset.quitarEnlace}`, { metodo: "DELETE" });
      if (q || qe) { pintarPersonas(await api(`/api/nodo/${nodoId}/acceso`)); refrescoSuave(); }
    } catch (err) { aviso(err.message, { error: true }); }
    if (cp) {
      const ok = await copiar($(".resultado-enlace input", d).value);
      if (ok) { cp.querySelector("span").textContent = "Copiado"; } else { $(".resultado-enlace input", d).select(); }
    }
  };
  $(".opciones-enlace", d).addEventListener("submit", async (e) => {
    e.preventDefault();
    const formEnlace = e.currentTarget; // tras el await, e.currentTarget ya es null
    const f = Object.fromEntries(new FormData(formEnlace));
    try {
      const r = await api(`/api/nodo/${nodoId}/enlace`, { metodo: "POST", datos: { permiso: f.permiso || "descargar", dias: +f.dias, clave: f.clave, max_descargas: +f.max_descargas || 0 } });
      $(".resultado-enlace", d).innerHTML = `<div class="enlace-caja"><b>Tu enlace${r.permiso === "subir" ? " para pedir archivos" : ""}</b>
        <div class="copiar"><input class="entrada" readonly value="${esc(r.url)}" aria-label="Enlace"><button class="boton primario" type="button" data-copiar>${icono("copiar")}<span>Copiar</span></button></div>
        <small class="ayuda">Guárdalo ahora: scloud solo guarda su huella, así que no podrá enseñártelo otra vez.</small></div>`;
      $(".resultado-enlace input", d).select();
      $(".crear-enlace", d).open = false;
      formEnlace.reset();
      pintarPersonas(await api(`/api/nodo/${nodoId}/acceso`));
      refrescoSuave();
    } catch (err) { aviso(err.message, { error: true }); }
  });
  entrada.focus();
}

function detallesEnlace(e) {
  const partes = [
    `<span class="pildora ${e.vivo ? "acento" : ""}">${e.permiso === "subir" ? `${icono("buzon")} buzón` : `${icono("bajar")} descarga`}</span>`,
    `<span class="mono">…/e/${esc(e.pista)}…</span>`,
    e.con_clave ? `<span class="pildora">${icono("candado")} con contraseña</span>` : "",
    !e.vivo ? `<span class="pildora">caducado</span>` : e.caduca ? `<span>caduca ${esc(fechaFutura(e.caduca))}</span>` : "<span>no caduca</span>",
    e.permiso === "descargar" ? `<span>${e.descargas}${e.max_descargas ? ` de ${e.max_descargas}` : ""} ${e.descargas === 1 ? "descarga" : "descargas"}</span>` : "",
  ];
  return partes.filter(Boolean).join("");
}

// ============================================================ otras vistas

async function vistaRecientes(suave) {
  cabecera({ titulo: "Recientes", nota: "<span>Lo último que ha cambiado en tu nube.</span>", acciones: botonModo() });
  if (!suave) esqueleto();
  try {
    const items = (await api("/api/recientes")).map((i) => ({ ...i, permiso: "propietario" }));
    if (estado.vista !== "recientes") return;
    pintarLista(items, { suave, vacio: { titulo: "Nada reciente", texto: "Cuando subas o cambies archivos, aparecerán aquí." } });
  } catch (e) { errorVista(e); }
}

async function vistaBuscar(q, suave) {
  cabecera({ titulo: `«${q}»`, migas: [{ nombre: "Mi nube", href: "#/" }], nota: "<span>Resultados de la búsqueda en tu nube.</span>" });
  if ($("#buscar").value !== q && document.activeElement !== $("#buscar")) $("#buscar").value = q;
  if (!suave) esqueleto();
  try {
    const items = (await api(`/api/buscar?q=${encodeURIComponent(q)}`)).map((i) => ({ ...i, permiso: "propietario" }));
    if (estado.vista !== "buscar") return;
    pintarLista(items, { suave, sub: (it) => `en ${esc(it.donde)}`, vacio: { titulo: "No hay nada con ese nombre", texto: "Prueba con otra palabra o con solo una parte del nombre." } });
  } catch (e) { errorVista(e); }
}

let temporizadorBuscar;
$("#buscar").addEventListener("input", (e) => {
  clearTimeout(temporizadorBuscar);
  const q = e.target.value.trim();
  temporizadorBuscar = setTimeout(() => {
    if (q.length >= 2) location.hash = `#/buscar/${encodeURIComponent(q)}`;
    else if (!q && estado.vista === "buscar") location.hash = "#/";
  }, 300);
});
$("#form-buscar").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#buscar").value.trim();
  if (q.length >= 2) location.hash = `#/buscar/${encodeURIComponent(q)}`;
});

async function vistaCompartido(suave) {
  cabecera({ titulo: "Compartido conmigo", nota: "<span>Lo que otras personas han compartido contigo.</span>", acciones: botonModo() });
  if (!suave) esqueleto();
  try {
    const items = await api("/api/compartido");
    if (estado.vista !== "compartido") return;
    pintarLista(items, {
      suave,
      sub: (it) => `de ${esc(it.dueno)} · ${it.permiso === "editar" ? "puedes editar" : "solo ver"}`,
      vacio: { titulo: "Aún no te han compartido nada", texto: `Cuando alguien comparta una carpeta o un archivo con @${estado.yo.usuario}, aparecerá aquí al momento.` },
    });
  } catch (e) { errorVista(e); }
}

async function vistaPapelera(suave) {
  if (!suave) esqueleto();
  let items;
  try { items = await api("/api/papelera"); } catch (e) { return errorVista(e); }
  if (estado.vista !== "papelera") return;
  cabecera({
    titulo: "Papelera", nota: "<span>Lo borrado se guarda 30 días. Después se elimina solo.</span>",
    acciones: items.length ? `<button class="boton peligro" data-accion="vaciar">${icono("papelera")}<span>Vaciar papelera</span></button>` : "",
  });
  estado.items = new Map();
  if (!items.length) {
    $("#vista").innerHTML = `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-papelera"/></svg><h2>La papelera está vacía</h2><p>Lo que mandes aquí se puede recuperar durante 30 días.</p></div>`;
    return;
  }
  $("#vista").innerHTML = `<ul class="lista">${items.map((it, i) => {
    const tipo = tipoDe(it);
    return `<li class="fila" data-i="${i}">
      <span class="marca-sel"><span class="tipo ${tipo}">${icono(ICONO_TIPO[tipo])}</span></span>
      <span class="nombre-col"><span class="nombre"><b>${esc(it.nombre)}</b></span>
        <span class="sub">borrado ${esc(fecha(it.borrado))}${it.elementos > 1 ? ` · ${it.elementos - 1} cosas dentro` : ""} · se elimina el ${new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long" }).format(new Date(it.caduca))}</span></span>
      <span class="tam der">${it.tipo === "carpeta" ? "" : tamano(it.tamano)}</span>
      <span class="acciones-fila" data-ancho><button class="boton suave" data-accion="recuperar" data-id="${it.id}">${icono("recuperar")}<span>Recuperar</span></button>
        <button class="boton icono peligro" data-accion="eliminar" data-id="${it.id}" aria-label="Eliminar para siempre «${esc(it.nombre)}»">${icono("cerrar")}</button></span>
    </li>`;
  }).join("")}</ul>`;
  if (!suave) escalonar($("#vista"));
}

async function recuperar(id) {
  try {
    const r = await api(`/api/papelera/${id}/recuperar`, { metodo: "POST" });
    aviso(`«${r.nombre}» ha vuelto a su sitio.`, { accion: "Ir allí", alPulsar: () => { location.hash = r.padre ? `#/c/${r.padre}` : "#/"; } });
    refrescoSuave();
  } catch (e) { aviso(e.message, { error: true }); }
}

async function eliminar(id) {
  if (!await confirmar({ titulo: "¿Eliminar para siempre?", texto: "No se podrá recuperar.", boton: "Eliminar", peligro: true })) return;
  try { await api(`/api/papelera/${id}`, { metodo: "DELETE" }); refrescoSuave(); actualizarYo(); } catch (e) { aviso(e.message, { error: true }); }
}

async function vaciarPapelera() {
  if (!await confirmar({ titulo: "¿Vaciar la papelera?", texto: "Todo lo que hay en ella se eliminará para siempre y liberará espacio.", boton: "Vaciar", peligro: true })) return;
  try {
    const r = await api("/api/papelera", { metodo: "DELETE" });
    aviso(`Eliminados ${r.elementos} elementos.`);
    refrescoSuave(); actualizarYo();
  } catch (e) { aviso(e.message, { error: true }); }
}

async function vistaEnlaces(suave) {
  cabecera({ titulo: "Enlaces", nota: "<span>Los enlaces que has creado para compartir con quien no tiene cuenta, o para pedir archivos.</span>" });
  if (!suave) esqueleto();
  let items;
  try { items = await api("/api/enlaces"); } catch (e) { return errorVista(e); }
  if (estado.vista !== "enlaces") return;
  estado.items = new Map();
  if (!items.length) {
    $("#vista").innerHTML = `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-enlace"/></svg><h2>Sin enlaces</h2>
      <p>Abre el menú ⋯ de un archivo o una carpeta y elige «Compartir y enlaces» para crear uno.</p></div>`;
    return;
  }
  $("#vista").innerHTML = `<ul class="lista">${items.map((e, i) => {
    const tipo = tipoDe(e);
    return `<li class="fila" data-i="${i}">
      <span class="marca-sel"><span class="tipo ${tipo}">${icono(ICONO_TIPO[tipo])}</span></span>
      <span class="nombre-col"><span class="nombre"><b>${esc(e.nombre)}</b></span><span class="sub enlace-detalle">${detallesEnlace(e)}</span></span>
      <span class="tam der tenue">${fecha(e.creado)}</span>
      <span class="acciones-fila" data-ancho><button class="boton suave" data-accion="ver-enlace" data-id="${e.nodo_id}">Gestionar</button>
        <button class="boton icono peligro" data-accion="quitar-enlace" data-id="${e.id}" aria-label="Quitar el enlace de «${esc(e.nombre)}»">${icono("cerrar")}</button></span></li>`;
  }).join("")}</ul>`;
  if (!suave) escalonar($("#vista"));
}

async function quitarEnlace(id) {
  if (!await confirmar({ titulo: "¿Quitar este enlace?", texto: "Quien lo tenga ya no podrá abrirlo.", boton: "Quitar", peligro: true })) return;
  try { await api(`/api/enlace/${id}`, { metodo: "DELETE" }); refrescoSuave(); } catch (e) { aviso(e.message, { error: true }); }
}

// ---------------------------------------------------------------- equipos

async function vistaEquipos(suave) {
  cabecera({
    titulo: "Equipos",
    nota: "<span>Ordenadores con el cliente de scloud: suben y bajan varios archivos a la vez y mantienen carpetas sincronizadas.</span>",
    acciones: `<button class="boton primario" data-accion="conectar-equipo">${icono("mas")}<span>Conectar un equipo</span></button>`,
  });
  if (!suave) esqueleto();
  let datos;
  try { datos = await api("/api/equipos"); } catch (e) { return errorVista(e); }
  if (estado.vista !== "equipos") return;
  const { equipos, servidor } = datos;
  const tarjetas = equipos.length ? `<div class="rejilla-equipos">${equipos.map((q, i) => {
    const vivo = q.conexiones > 0 || (q.visto && Date.now() - q.visto < 90_000);
    return `<article class="equipo" data-i="${i}">
      <h3>${icono("equipo")} ${esc(q.nombre)}</h3>
      <span class="estado"><span class="punto${vivo ? " vivo" : ""}"></span>${vivo ? `conectado${q.conexiones ? ` · ${q.conexiones} ${q.conexiones === 1 ? "conexión" : "conexiones"}` : ""}` : q.visto ? `visto ${esc(fecha(q.visto))}` : "aún no se ha conectado"}</span>
      ${q.carpetas ? `<span class="pildora acento">${icono("recuperar")} sincroniza ${esc(q.carpetas)}</span>` : ""}
      <small class="tenue">Conectado el ${new Intl.DateTimeFormat("es-ES", { dateStyle: "long" }).format(new Date(q.creado))}</small>
      <button class="boton icono quitar" data-accion="quitar-equipo" data-id="${q.id}" data-nombre="${esc(q.nombre)}" aria-label="Desconectar ${esc(q.nombre)}">${icono("cerrar")}</button>
    </article>`;
  }).join("")}</div>`
    : `<div class="vacio"><svg class="ico nube-vacia"><use href="#i-equipo"/></svg><h2>Ningún equipo conectado</h2>
       <p>Con el cliente de escritorio puedes subir carpetas enteras de golpe, reanudar lo que se corta y tener una carpeta siempre igual en tu ordenador y en la nube.</p></div>`;
  $("#vista").innerHTML = `${tarjetas}
    ${servidor ? `<section class="bloque"><h2>El servidor</h2>
      <p>Los equipos hablan con scloud por <b>${esc(servidor.host)}:${servidor.puerto}</b> con su propio protocolo sobre TCP${servidor.tls ? ", cifrado con TLS. Al conectar uno, comprueba que la huella del certificado es esta:" : " sin cifrar (modo de pruebas)."}</p>
      ${servidor.huella ? `<p class="huella">${esc(servidor.huella)}</p>` : ""}</section>` : ""}
    <section class="bloque" aria-live="off"><h2>Sala de máquinas</h2>
      <p>Lo que pasa ahora mismo dentro del servidor: un hilo por cada petición web y por cada conexión de un equipo, y un semáforo que deja pasar ${"<span id=\"sala-limite\"></span>"} transferencias a la vez.</p>
      <div class="sala" id="sala-cifras"></div><div class="hilos" id="sala-hilos"></div><div class="desplazable" id="sala-conexiones"></div></section>`;
  if (!suave) escalonar($("#vista"));
  pintarSala();
  clearInterval(estado.sala);
  estado.sala = setInterval(() => { if (estado.vista === "equipos" && !document.hidden) pintarSala(); }, 2000);
}

async function pintarSala() {
  let s;
  try { s = await api("/api/sala"); } catch { return; }
  if (estado.vista !== "equipos" || !$("#sala-cifras")) return;
  $("#sala-limite").textContent = `${s.transferencias.limite_global} (${s.transferencias.limite_usuario} por cuenta)`;
  const cifra = (n, t) => `<div class="cifra"><b>${n}</b><small>${t}</small></div>`;
  $("#sala-cifras").innerHTML = [
    cifra(s.total_hilos, "hilos vivos"), cifra(s.transferencias.en_curso, "transferencias en curso"),
    cifra(s.transferencias.en_cola, "esperando turno"), cifra(s.pestanas, "pestañas escuchando"),
    cifra(s.atendidas, "conexiones de equipos atendidas"), cifra(new Intl.NumberFormat("es-ES").format(s.ordenes_bd), "órdenes a la base de datos"),
  ].join("");
  $("#sala-hilos").innerHTML = s.hilos.map(([n, c]) => `<span class="pildora">${esc(n)} × ${c}</span>`).join("");
  $("#sala-conexiones").innerHTML = s.conexiones.length ? `<table class="tabla-conexiones"><thead><tr><th>Hilo</th><th>Equipo</th><th>Hace</th><th>Haciendo</th></tr></thead>
    <tbody>${s.conexiones.map((c) => `<tr><td class="mono">${esc(c.hilo)}</td><td>${esc(c.equipo || "—")}</td><td>${c.desde} s</td><td>${esc(c.orden)}</td></tr>`).join("")}</tbody></table>` : "";
}

async function conectarEquipo() {
  const r = await pedirTexto({ titulo: "Conectar un equipo", etiqueta: "¿Cómo se llama este equipo?", valor: "Mi portátil", boton: "Crear acceso" });
  if (!r) return;
  try {
    const q = await api("/api/equipos", { metodo: "POST", datos: { nombre: r.texto } });
    const d = dialogo({
      titulo: `Conecta «${q.nombre}»`, ancho: true,
      sub: "Solo se enseña una vez: cópiala ahora.",
      cuerpo: `<p>1. Descarga <a href="/descargas/scloud-cliente.zip" download>el cliente de scloud</a> y descomprímelo. Solo necesita Python 3.11 o más nuevo; nada más.</p>
        <p>2. Abre una terminal dentro de la carpeta <b>scloud-cliente</b> y ejecuta:</p>
        <pre class="orden-copiar">${esc(q.orden)}</pre>
        <p>3. Ya está. Por ejemplo:</p>
        <pre class="orden-copiar">python3 -m scloud subir ~/Fotos --a /Fotos --hilos 4
python3 -m scloud sincronizar ~/scloud</pre>
        <p class="ayuda">La orden lleva la huella del certificado del servidor: si alguien se hiciera pasar por él, el cliente se negaría a conectar.</p>`,
      pie: `<button class="boton" type="button" data-cerrar>Hecho</button><button class="boton primario" type="button" data-copiar-orden>${icono("copiar")}<span>Copiar la orden</span></button>`,
    });
    $("[data-copiar-orden]", d).addEventListener("click", async (e) => {
      if (await copiar(q.orden)) e.currentTarget.querySelector("span").textContent = "Copiada";
    });
    refrescoSuave();
  } catch (e) { aviso(e.message, { error: true }); }
}

async function quitarEquipo(id, nombre) {
  if (!await confirmar({ titulo: `¿Desconectar «${nombre}»?`, texto: "Ese equipo dejará de poder entrar en tu nube. Lo que ya tenga descargado se queda en él.", boton: "Desconectar", peligro: true })) return;
  try { await api(`/api/equipos/${id}`, { metodo: "DELETE" }); refrescoSuave(); } catch (e) { aviso(e.message, { error: true }); }
}

// ============================================================ subir archivos

function destinoSubida() {
  if (estado.vista === "nube" && estado.permiso !== "ver") return { id: estado.carpeta, nombre: $("#titulo").textContent };
  return { id: 0, nombre: "Mi nube" };
}

function subirArchivos(archivos, destino = destinoSubida()) {
  if (!archivos.length) return;
  lluvia.subir(archivos, destino.id);
  if (destino.id !== estado.carpeta || estado.vista !== "nube") aviso(`Subiendo a «${destino.nombre}».`);
}

$("#elegir-archivos").addEventListener("change", (e) => {
  subirArchivos([...e.target.files].map((file) => ({ file, ruta: file.name })));
  e.target.value = "";
});
$("#elegir-carpeta").addEventListener("change", (e) => {
  subirArchivos([...e.target.files].map((file) => ({ file, ruta: file.webkitRelativePath || file.name })));
  e.target.value = "";
});

// -------------------------------------------------- arrastrar y soltar

let profundidad = 0;
const esExterno = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
const esInterno = (e) => [...(e.dataTransfer?.types || [])].includes("application/x-scloud");

window.addEventListener("dragenter", (e) => {
  if (!esExterno(e) || $("#app").hidden) return;
  profundidad++;
  $("#soltar-donde").textContent = destinoSubida().nombre;
  $("#soltar").hidden = false;
});
window.addEventListener("dragleave", (e) => {
  if (!esExterno(e)) return;
  if (--profundidad <= 0) { profundidad = 0; $("#soltar").hidden = true; }
});
window.addEventListener("dragover", (e) => {
  if (esExterno(e)) {
    e.preventDefault();
    const sobre = e.target.closest?.(".fila[data-tipo=carpeta]");
    $$(".fila.soltar-aqui").forEach((f) => f !== sobre && f.classList.remove("soltar-aqui"));
    if (sobre && ["propietario", "editar"].includes(estado.items.get(+sobre.dataset.id)?.permiso)) {
      sobre.classList.add("soltar-aqui");
      $("#soltar-donde").textContent = estado.items.get(+sobre.dataset.id).nombre;
    } else {
      $("#soltar-donde").textContent = destinoSubida().nombre;
    }
  }
});
window.addEventListener("drop", async (e) => {
  if (!esExterno(e)) return;
  e.preventDefault();
  profundidad = 0;
  $("#soltar").hidden = true;
  const sobre = e.target.closest?.(".fila.soltar-aqui");
  $$(".fila.soltar-aqui").forEach((f) => f.classList.remove("soltar-aqui"));
  const destino = sobre ? { id: +sobre.dataset.id, nombre: estado.items.get(+sobre.dataset.id).nombre } : destinoSubida();
  const archivos = await leerSoltados(e.dataTransfer);
  subirArchivos(archivos, destino);
});

async function leerSoltados(dt) {
  const entradas = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entradas.length) return [...dt.files].map((file) => ({ file, ruta: file.name }));
  const salida = [];
  async function recorrer(entrada) {
    if (entrada.isFile) {
      const file = await new Promise((ok, mal) => entrada.file(ok, mal));
      salida.push({ file, ruta: entrada.fullPath.replace(/^\//, "") });
    } else if (entrada.isDirectory) {
      const lector = entrada.createReader();
      let lote;
      do {
        lote = await new Promise((ok, mal) => lector.readEntries(ok, mal));
        for (const hijo of lote) await recorrer(hijo);
      } while (lote.length);
    }
  }
  for (const entrada of entradas) await recorrer(entrada);
  return salida;
}

// Mover arrastrando dentro de la lista (sobre una carpeta o sobre una miga)
$("#vista").addEventListener("dragstart", (e) => {
  const li = e.target.closest(".fila[draggable=true]");
  if (!li) return;
  const id = +li.dataset.id;
  const ids = estado.sel.has(id) ? [...estado.sel] : [id];
  e.dataTransfer.setData("application/x-scloud", JSON.stringify(ids));
  e.dataTransfer.effectAllowed = "move";
});
for (const zona of [$("#vista"), $("#migas")]) {
  zona.addEventListener("dragover", (e) => {
    if (!esInterno(e)) return;
    const destino = e.target.closest(".fila[data-tipo=carpeta], [data-destino]");
    $$(".soltar-aqui").forEach((f) => f !== destino && f.classList.remove("soltar-aqui"));
    if (destino) { e.preventDefault(); destino.classList.add("soltar-aqui"); }
  });
  zona.addEventListener("drop", async (e) => {
    if (!esInterno(e)) return;
    const destino = e.target.closest(".fila[data-tipo=carpeta], [data-destino]");
    $$(".soltar-aqui").forEach((f) => f.classList.remove("soltar-aqui"));
    if (!destino) return;
    e.preventDefault();
    const ids = JSON.parse(e.dataTransfer.getData("application/x-scloud") || "[]");
    const a = destino.dataset.destino !== undefined ? +destino.dataset.destino : +destino.dataset.id;
    if (ids.includes(a)) return;
    try {
      const r = await api("/api/mover", { metodo: "POST", datos: { ids, destino: a } });
      if (r.errores?.length) aviso(r.errores[0], { error: true });
      limpiarSeleccion();
      refrescoSuave();
    } catch (err) { aviso(err.message, { error: true }); }
  });
}
