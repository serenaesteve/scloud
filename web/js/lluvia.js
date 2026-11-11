// La lluvia: la bandeja de transferencias.
//
// Junta dos fuentes:
//  · las subidas de esta pestaña (XMLHttpRequest, porque fetch todavía no
//    cuenta el progreso de lo que se envía), de 3 en 3;
//  · lo que el servidor anuncia por /api/eventos: lo que suben o bajan tus
//    equipos, otras pestañas o alguien desde un enlace.
// Cada transferencia es una gota que se llena según avanza.

import { $, api, aviso, crear, esc, icono, idAleatorio, tamano } from "./util.js";

const A_LA_VEZ = 3;
const ACTIVAS = new Set(["en cola", "en curso"]);

export class Lluvia {
  constructor({ alSubir } = {}) {
    this.items = new Map();
    this.cola = [];
    this.activas = 0;
    this.alSubir = alSubir;
    this.caja = $("#lluvia");
    this.lista = $("#lluvia-lista");
    this.resumen = $("#lluvia-resumen");
    this.temporizador = null;
    this.resumen.addEventListener("click", () => this.plegar(this.lista.hidden));
    this.lista.addEventListener("click", (e) => {
      const b = e.target.closest("[data-cancelar]");
      if (b) this.cancelar(b.dataset.cancelar);
    });
    window.addEventListener("beforeunload", (e) => {
      if (this.cola.length || this.activas) { e.preventDefault(); e.returnValue = ""; }
    });
  }

  plegar(abrir) {
    this.lista.hidden = !abrir;
    this.resumen.setAttribute("aria-expanded", String(abrir));
  }

  // ------------------------------------------------------------ subir

  subir(archivos, carpeta) {
    // archivos: [{ file, ruta }] — ruta relativa ("Viaje/fotos/a.jpg") para las carpetas
    for (const { file, ruta } of archivos) {
      const id = idAleatorio();
      const item = { id: "w" + id, idCliente: id, nombre: file.name, sentido: "subida", canal: "web", origen: "este navegador",
        total: file.size, hecho: 0, estado: "en cola", local: true, file, ruta: ruta || file.name, carpeta, inicio: 0 };
      this.items.set(item.id, item);
      this.cola.push(item);
    }
    if (archivos.length > 3) this.plegar(false);
    this.pintar();
    this.siguiente();
  }

  siguiente() {
    while (this.activas < A_LA_VEZ && this.cola.length) {
      const item = this.cola.shift();
      if (item.estado !== "en cola") continue;
      this.activas++;
      this.enviar(item).finally(() => { this.activas--; this.siguiente(); });
    }
  }

  enviar(item) {
    return new Promise((resolver) => {
      const xhr = new XMLHttpRequest();
      item.xhr = xhr;
      const q = new URLSearchParams({ carpeta: item.carpeta || 0, ruta: item.ruta, mod: item.file.lastModified || "" });
      xhr.open("PUT", `/api/subir?${q}`);
      xhr.setRequestHeader("X-Scloud", "1");
      xhr.setRequestHeader("X-Scl-Id", item.idCliente);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => {
        if (!item.inicio) item.inicio = performance.now();
        item.hecho = e.loaded;
        if (item.estado === "en cola") item.estado = "en curso";
        const s = (performance.now() - item.inicio) / 1000;
        item.velocidad = s > .3 ? e.loaded / s : 0;
        this.pintar();
      };
      xhr.onload = () => {
        let r = {};
        try { r = JSON.parse(xhr.responseText); } catch { /* respuesta vacía */ }
        if (xhr.status === 201) {
          item.estado = "hecha"; item.hecho = item.total;
          this.alSubir?.(r);
        } else if (item.estado !== "cancelada") {
          item.estado = "error"; item.error = r.error || `Error ${xhr.status}`;
          aviso(`«${item.nombre}»: ${item.error}`, { error: true, ms: 7000 });
        }
        this.pintar(); resolver();
      };
      xhr.onerror = () => {
        if (item.estado !== "cancelada") { item.estado = "error"; item.error = "Se cortó la conexión"; }
        this.pintar(); resolver();
      };
      xhr.onabort = () => { item.estado = "cancelada"; this.pintar(); resolver(); };
      xhr.send(item.file);
    });
  }

  cancelar(id) {
    const item = this.items.get(id);
    if (!item) return;
    if (item.local) {
      item.estado = "cancelada";
      item.xhr?.abort();
    }
    api(`/api/transferencias/${encodeURIComponent(id)}/cancelar`, { metodo: "POST" }).catch(() => {});
    this.pintar();
  }

  // ----------------------------------------------- avisos del servidor

  desdeServidor(t) {
    const item = this.items.get(t.id);
    if (item?.local) {
      // La subida es nuestra: el navegador sabe mejor lo enviado. Del servidor
      // solo interesa si está esperando turno en su cola.
      if (t.estado === "en cola" && item.estado === "en curso" && item.hecho >= item.total) item.estado = "en cola";
      if (t.estado === "en curso" && item.estado === "en cola") item.estado = "en curso";
      this.pintar();
      return;
    }
    if (!item && !ACTIVAS.has(t.estado)) return; // ya terminada antes de que la viéramos
    this.items.set(t.id, { ...(item || {}), ...t, local: false });
    this.pintar();
  }

  // ------------------------------------------------------------ pintar

  pintar() {
    cancelAnimationFrame(this.marco);
    this.marco = requestAnimationFrame(() => this._pintar());
  }

  _pintar() {
    const todas = [...this.items.values()];
    if (!todas.length) { this.caja.hidden = true; return; }
    this.caja.hidden = false;
    const activas = todas.filter((t) => ACTIVAS.has(t.estado));
    const subiendo = activas.filter((t) => t.sentido === "subida").length;
    const bajando = activas.length - subiendo;
    const total = todas.reduce((s, t) => s + (t.estado === "cancelada" ? 0 : t.total), 0);
    const hecho = todas.reduce((s, t) => s + (t.estado === "cancelada" ? 0 : Math.min(t.hecho, t.total)), 0);
    const velocidad = activas.reduce((s, t) => s + (t.velocidad || 0), 0);
    const errores = todas.filter((t) => t.estado === "error").length;

    const gota = $(".gota-nivel", this.resumen);
    gota.style.setProperty("--p", total ? hecho / total : 1);
    gota.classList.toggle("lista", !activas.length && !errores);
    gota.classList.toggle("fallo", !activas.length && errores > 0);

    let titulo, sub;
    if (activas.length) {
      const partes = [];
      if (subiendo) partes.push(`Subiendo ${subiendo}`);
      if (bajando) partes.push(`${subiendo ? "bajando" : "Bajando"} ${bajando}`);
      titulo = partes.join(" y ") + (activas.length === 1 ? " archivo" : " archivos");
      sub = `${Math.floor((total ? hecho / total : 0) * 100)} %${velocidad ? ` · ${tamano(velocidad)}/s` : ""} · ${tamano(hecho)} de ${tamano(total)}`;
    } else if (errores) {
      titulo = errores === 1 ? "Un archivo no se pudo pasar" : `${errores} archivos no se pudieron pasar`;
      sub = "Ábrelo para ver por qué";
    } else {
      const hechas = todas.filter((t) => t.estado === "hecha").length;
      titulo = "Todo listo";
      sub = `${hechas} ${hechas === 1 ? "archivo" : "archivos"} · ${tamano(hecho)}`;
    }
    $("#lluvia-titulo").textContent = titulo;
    $("#lluvia-sub").textContent = sub;

    // Filas: se reutilizan para que la gota se llene con transición y no salte
    const vistas = new Set();
    for (const t of todas.slice(-60).reverse()) {
      vistas.add(t.id);
      let li = this.lista.querySelector(`[data-id="${CSS.escape(t.id)}"]`);
      if (!li) {
        li = crear(`<li class="gota-fila" data-id="${esc(t.id)}">
          <span class="gota-nivel" aria-hidden="true"><span class="agua"></span></span>
          <span class="info"><b></b><small></small></span>
          <button class="cancelar" type="button" aria-label="Cancelar">${icono("cerrar")}</button></li>`);
        $(".cancelar", li).dataset.cancelar = t.id;
        this.lista.prepend(li);
      }
      const p = t.total ? Math.min(t.hecho, t.total) / t.total : (t.estado === "hecha" ? 1 : 0);
      const g = $(".gota-nivel", li);
      g.style.setProperty("--p", t.estado === "cancelada" ? 0 : p);
      g.classList.toggle("lista", t.estado === "hecha");
      g.classList.toggle("fallo", t.estado === "error");
      g.classList.toggle("espera", t.estado === "en cola");
      $("b", li).textContent = t.nombre;
      $("b", li).title = t.nombre;
      const flecha = t.sentido === "subida" ? "↑" : "↓";
      const de = t.origen && t.canal !== "web" ? ` · ${t.origen}` : "";
      const estado = {
        "en cola": "esperando turno", "en curso": `${Math.floor(p * 100)} %${t.velocidad ? ` · ${tamano(t.velocidad)}/s` : ""}`,
        hecha: tamano(t.total), error: t.error || "falló", cancelada: "cancelada",
      }[t.estado];
      $("small", li).innerHTML = `<span class="flecha">${flecha}</span> ${esc(estado)}${esc(de)}`;
      $(".cancelar", li).hidden = !ACTIVAS.has(t.estado);
    }
    for (const li of [...this.lista.children]) if (!vistas.has(li.dataset.id)) li.remove();

    // Cuando todo termina bien, la bandeja se recoge sola al rato
    clearTimeout(this.temporizador);
    if (!activas.length && !errores) {
      this.temporizador = setTimeout(() => this.limpiar(), 6000);
    }
  }

  limpiar() {
    for (const [id, t] of this.items) if (!ACTIVAS.has(t.estado)) this.items.delete(id);
    this.plegar(false);
    this.pintar();
  }
}
