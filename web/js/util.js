// Ayudas pequeñas que usan todas las vistas.

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];

export class ErrorApi extends Error {
  constructor(mensaje, estado) { super(mensaje); this.estado = estado; }
}

export async function api(ruta, { metodo = "GET", datos } = {}) {
  const opciones = { method: metodo, headers: { "X-Scloud": "1" }, credentials: "same-origin" };
  if (datos !== undefined) {
    opciones.headers["Content-Type"] = "application/json";
    opciones.body = JSON.stringify(datos);
  }
  let r;
  try {
    r = await fetch(ruta, opciones);
  } catch {
    throw new ErrorApi("No hay conexión con scloud. Comprueba tu red y vuelve a probar.", 0);
  }
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401 && ruta !== "/api/entrar" && ruta !== "/api/yo") document.dispatchEvent(new CustomEvent("sin-sesion"));
    throw new ErrorApi(cuerpo.error || `Algo ha fallado (${r.status}).`, r.status);
  }
  return cuerpo;
}

export function esc(texto) {
  return String(texto ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export const icono = (nombre) => `<svg class="ico" aria-hidden="true"><use href="#i-${nombre}"/></svg>`;

const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
export function tamano(bytes) {
  if (bytes == null) return "";
  const unidades = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < unidades.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : numero.format(n)} ${unidades[i]}`;
}

const hora = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit" });
const diaMes = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
const diaMesAno = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", year: "numeric" });
export function fecha(ms) {
  if (!ms) return "";
  const d = new Date(ms), ahora = new Date();
  const seg = (ahora - d) / 1000;
  if (seg < 50) return "ahora mismo";
  if (seg < 3600) return `hace ${Math.round(seg / 60)} min`;
  const hoy = new Date(ahora.getFullYear(), ahora.getMonth(), ahora.getDate());
  if (d >= hoy) return `hoy, ${hora.format(d)}`;
  if (d >= new Date(hoy - 86400000)) return `ayer, ${hora.format(d)}`;
  return d.getFullYear() === ahora.getFullYear() ? diaMes.format(d) : diaMesAno.format(d);
}
export function fechaFutura(ms) {
  const horas = (ms - Date.now()) / 3600000;
  if (horas <= 0) return "ya";
  if (horas < 1) return `en ${Math.max(1, Math.round(horas * 60))} min`;
  if (horas < 20) return `en ${Math.round(horas)} h`;
  const dias = Math.round(horas / 24);
  if (dias <= 1) return "mañana";
  return dias <= 45 ? `en ${dias} días (${diaMes.format(new Date(ms))})` : `el ${diaMesAno.format(new Date(ms))}`;
}
export function fechaLarga(ms) {
  return new Intl.DateTimeFormat("es-ES", { dateStyle: "long", timeStyle: "short" }).format(new Date(ms));
}

const EXTENSIONES = {
  imagen: /\.(jpe?g|png|gif|webp|avif|heic|svg|bmp|tiff?)$/i,
  video: /\.(mp4|mov|webm|mkv|avi|m4v)$/i,
  audio: /\.(mp3|wav|ogg|m4a|flac|aac|opus)$/i,
  pdf: /\.pdf$/i,
  zip: /\.(zip|rar|7z|tar|gz|bz2|xz)$/i,
  codigo: /\.(py|js|ts|jsx|tsx|html?|css|json|java|c|cpp|h|rs|go|php|rb|sh|sql|sbd|xml|ya?ml|kt|swift)$/i,
  texto: /\.(txt|md|csv|rtf|docx?|odt|pages|xlsx?|ods|pptx?|odp|log)$/i,
};
export function tipoDe(nodo) {
  if (nodo.tipo === "carpeta") return "carpeta";
  for (const [tipo, re] of Object.entries(EXTENSIONES)) if (re.test(nodo.nombre)) return tipo;
  return "otro";
}
export const ICONO_TIPO = { carpeta: "carpeta", imagen: "imagen", video: "video", audio: "audio", pdf: "pdf", zip: "zip", codigo: "codigo", texto: "texto", otro: "archivo" };

export function iniciales(nombre) {
  const partes = String(nombre || "?").trim().split(/[\s._]+/).filter(Boolean);
  return ((partes[0]?.[0] || "?") + (partes.length > 1 ? partes.at(-1)[0] : "")).toUpperCase();
}

export function crear(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// La CSP no deja escribir style="…" en el HTML: los retrasos escalonados se ponen aquí.
export function escalonar(raiz, max = 24) {
  $$("[data-i]", raiz).forEach((el) => el.style.setProperty("--i", Math.min(+el.dataset.i, max)));
}

export function aviso(texto, { error = false, accion, alPulsar, ms = 4200 } = {}) {
  const caja = $("#avisos");
  const el = crear(`<div class="aviso${error ? " error" : ""}" role="${error ? "alert" : "status"}"><span>${esc(texto)}</span></div>`);
  if (accion) {
    const b = crear(`<button class="boton suave" type="button">${esc(accion)}</button>`);
    b.addEventListener("click", () => { alPulsar?.(); quitar(); });
    el.append(b);
  }
  const quitar = () => { el.classList.add("sale"); el.addEventListener("animationend", () => el.remove(), { once: true }); };
  caja.append(el);
  setTimeout(quitar, ms);
}

export function idAleatorio() {
  const a = new Uint8Array(9);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14);
}

export function copiar(texto) {
  return navigator.clipboard?.writeText(texto).then(() => true, () => false) ?? Promise.resolve(false);
}

export function guardado(clave, defecto) {
  try { return localStorage.getItem(clave) ?? defecto; } catch { return defecto; }
}
export function guardar(clave, valor) {
  try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento: no pasa nada */ }
}
