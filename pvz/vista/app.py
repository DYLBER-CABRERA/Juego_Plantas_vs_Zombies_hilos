"""
CAPA 4 - VISTA (tkinter)

REGLA DE ORO: tkinter solo se puede tocar desde el hilo principal (MainThread).
Por eso ningún hilo del juego dibuja nada. En su lugar la vista, cada 50 ms
(root.after), hace dos cosas:
    1. pide un snapshot() del estado (una copia) y lo dibuja
    2. vacía la cola de eventos (queue.Queue) que los hilos fueron llenando
Es el patrón productor-consumidor: los hilos producen, la GUI consume.

Los clics (sembrar / pala) se ejecutan en el hilo de la GUI y llaman a la capa de
lógica, que toma los mismos candados que usan los hilos del juego.
"""
import math
import queue
import tkinter as tk
from tkinter import ttk

from pvz import config
from pvz.logica.juego import Juego

C = config.TAM_CELDA
MARGEN = 36                                   # franja de la "casa" a la izquierda
ANCHO = MARGEN + config.COLUMNAS * C
ALTO = config.FILAS * C
CIELO = 40                                    # franja sobre el césped donde flotan los soles de la fila 0
CESPED = ("#9bd36b", "#8cc65c")
F_CHICA = ("TkDefaultFont", 7)
REFRESCO_MS = 50

COLOR_LOG = {"juego": "#1a5276", "hilo": "#555555", "detalle": "#8a8a8a", "error": "#c0392b"}


class App:
    def __init__(self, root):
        self.root = root
        self.juego = None
        self._n = 0
        self._fin_mostrado = False
        self.soles_anim = []          # soles flotando: {fila, col, valor, edad}
        self.herramienta = tk.StringVar(value="girasol")
        self.auto = tk.BooleanVar(value=True)
        self.ver_hilos = tk.BooleanVar(value=True)
        self.ver_detalle = tk.BooleanVar(value=False)
        self.msg = tk.StringVar(value="Elige una planta y haz clic en una celda del césped.")
        self._construir()
        self.nueva_partida()
        root.protocol("WM_DELETE_WINDOW", self._cerrar)
        root.after(REFRESCO_MS, self._refrescar)

    # =================================================================== UI
    def _construir(self):
        r = self.root
        r.title("Plantas vs Zombies - análisis de hilos")
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
        izq = ttk.Frame(r)
        izq.grid(row=0, column=0, sticky="nsew")
        der = ttk.Frame(r)
        der.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        r.columnconfigure(1, weight=1)
        r.rowconfigure(0, weight=1)

        # ---- barra superior (2 filas): soles + tienda, y controles de la partida
        barra = ttk.Frame(izq)
        barra.pack(fill="x", padx=6, pady=(6, 2))
        self.lbl_soles = ttk.Label(barra, text="Soles: 0", width=10, font=("TkDefaultFont", 15, "bold"))
        self.lbl_soles.pack(side="left")
        tienda = [("Girasol", "girasol"), ("Lanzaguisantes", "lanzaguisantes"), ("Nuez", "nuez")]
        for nombre, valor in tienda:
            ttk.Radiobutton(barra, text=f"{nombre} ({config.COSTO[valor]})", value=valor,
                            variable=self.herramienta, style="Toolbutton").pack(side="left", padx=2)
        ttk.Radiobutton(barra, text="Pala", value="pala", variable=self.herramienta,
                        style="Toolbutton").pack(side="left", padx=2)
        controles = ttk.Frame(izq)
        controles.pack(fill="x", padx=6, pady=(0, 4))
        self.btn_pausa = ttk.Button(controles, text="Pausa", width=9, command=self._pausa)
        self.btn_pausa.pack(side="left", padx=2)
        ttk.Button(controles, text="Nueva partida", command=self.nueva_partida).pack(side="left", padx=2)
        ttk.Checkbutton(controles, text="Auto-jugador (hilo IA)", variable=self.auto,
                        command=self._cambiar_auto).pack(side="left", padx=10)

        # ---- tablero
        # El canvas tiene una franja extra arriba (coordenadas y negativas) vía scrollregion:
        # así todas las coordenadas del tablero siguen empezando en y=0.
        self.canvas = tk.Canvas(izq, width=ANCHO, height=ALTO + CIELO, highlightthickness=0, bd=0,
                                bg="#2b2b2b", scrollregion=(0, -CIELO, ANCHO, ALTO))
        self.canvas.yview_moveto(0)
        self.canvas.pack(padx=6)
        self.canvas.bind("<Button-1>", self._clic)
        self._dibujar_fondo()
        ttk.Label(izq, textvariable=self.msg, anchor="w").pack(fill="x", padx=8, pady=(4, 0))

        # ---- registro de eventos
        marco = ttk.LabelFrame(izq, text="Registro de eventos (tiempo, hilo que lo emitió, qué hizo)")
        marco.pack(fill="both", expand=True, padx=6, pady=6)
        opciones = ttk.Frame(marco)
        opciones.pack(fill="x")
        ttk.Checkbutton(opciones, text="Ver ciclo de vida de hilos (nace / termina)",
                        variable=self.ver_hilos).pack(side="left", padx=4)
        ttk.Checkbutton(opciones, text="Ver detalle (golpes, mordiscos, soles)",
                        variable=self.ver_detalle).pack(side="left", padx=4)
        cuerpo = ttk.Frame(marco)
        cuerpo.pack(fill="both", expand=True)
        self.log = tk.Text(cuerpo, height=6, width=20, state="disabled", wrap="none",
                           font=("TkFixedFont", 9), background="#fbfbfb")
        sb = ttk.Scrollbar(cuerpo, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for nivel, color in COLOR_LOG.items():
            self.log.tag_configure(nivel, foreground=color)

        # ---- panel derecho: métricas, tabla de hilos y gráfica
        self.lbl_info = ttk.Label(der, justify="left", anchor="w", font=("TkDefaultFont", 9),
                                  wraplength=480)
        self.lbl_info.pack(fill="x")
        ttk.Label(der, text="Hilos de la partida", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(8, 2))
        cols = ("hilo", "rol", "estado", "ciclos", "vivo_s", "lock_ms")
        marco_t = ttk.Frame(der)
        marco_t.pack(fill="both", expand=True)
        self.tabla = ttk.Treeview(marco_t, columns=cols, show="headings", height=14)
        for col, texto, ancho in [("hilo", "Hilo", 128), ("rol", "Rol", 80), ("estado", "Estado", 98),
                                  ("ciclos", "Ciclos", 52), ("vivo_s", "Vivo s", 54), ("lock_ms", "Lock ms", 68)]:
            self.tabla.heading(col, text=texto)
            self.tabla.column(col, width=ancho, anchor="w" if col in ("hilo", "rol", "estado") else "e")
        sbt = ttk.Scrollbar(marco_t, command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=sbt.set)
        sbt.pack(side="right", fill="y")
        self.tabla.pack(side="left", fill="both", expand=True)
        self.tabla.tag_configure("terminado", foreground="#999999")
        self.tabla.tag_configure("lock", foreground="#c0661a")
        self.tabla.tag_configure("pausado", foreground="#2471a3")
        self.grafica = tk.Canvas(der, width=480, height=140, bg="white", highlightthickness=1,
                                 highlightbackground="#cccccc")
        self.grafica.pack(pady=(8, 0))

    def _dibujar_fondo(self):
        c = self.canvas
        c.create_rectangle(0, -CIELO, ANCHO, 0, fill="#cdeaf7", outline="")      # cielo
        c.create_rectangle(0, 0, MARGEN, ALTO, fill="#8d6e63", outline="")
        c.create_text(MARGEN / 2, ALTO / 2, text="CASA", angle=90, fill="white", font=("TkDefaultFont", 12, "bold"))
        for f in range(config.FILAS):
            for col in range(config.COLUMNAS):
                x0, y0 = MARGEN + col * C, f * C
                c.create_rectangle(x0, y0, x0 + C, y0 + C, fill=CESPED[(f + col) % 2], outline="")

    # ============================================================= acciones
    def nueva_partida(self):
        if self.juego is not None:
            self.juego.detener_todo()                 # cierre limpio de la partida anterior (join)
        self.juego = Juego()
        self._fin_mostrado = False
        self.soles_anim = []
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        for iid in self.tabla.get_children():
            self.tabla.delete(iid)
        self.btn_pausa.config(text="Pausa")
        self.msg.set("Elige una planta y haz clic en una celda del césped.")
        self.juego.iniciar(autojugador=self.auto.get())

    def _pausa(self):
        self.juego.alternar_pausa()
        self.btn_pausa.config(text="Reanudar" if self.juego.en_pausa else "Pausa")

    def _cambiar_auto(self):
        self.juego.activar_autojugador(self.auto.get())

    def _clic(self, ev):
        col = int((ev.x - MARGEN) // C)
        fila = int(self.canvas.canvasy(ev.y) // C)      # canvasy: el canvas tiene la franja de cielo arriba
        if not (0 <= fila < config.FILAS and 0 <= col < config.COLUMNAS):
            return
        herramienta = self.herramienta.get()
        if herramienta == "pala":
            ok = self.juego.quitar(fila, col)
            self.msg.set("Planta quitada: su hilo termina." if ok else "No hay ninguna planta ahí.")
            return
        ok, motivo = self.juego.sembrar(herramienta, fila, col)
        self.msg.set(f"Sembrado en la fila {fila}, columna {col}." if ok else f"No se puede sembrar: {motivo}.")

    def _cerrar(self):
        if self.juego is not None:
            self.juego.detener_todo()
        self.root.destroy()

    # ========================================================== refresco 50 ms
    def _refrescar(self):
        j = self.juego
        snap = j.estado.snapshot()
        self._avanzar_soles()
        self._dibujar(snap)
        self._vaciar_eventos()
        self.lbl_soles.config(text=f"Soles: {snap['soles']}")
        self._n += 1
        fin = snap["resultado"] is not None
        if self._n % 5 == 0 or (fin and not self._fin_mostrado):
            self._actualizar_tabla()
            self._actualizar_info()
            self._actualizar_grafica()
        if fin and not self._fin_mostrado:
            self._fin_mostrado = True
            self._mostrar_resumen()
        self.root.after(REFRESCO_MS, self._refrescar)

    # ------------------------------------------------------------- tablero
    def _dibujar(self, snap):
        c = self.canvas
        c.delete("dyn")
        for nombre, tipo, fila, col, vida, vmax in snap["plantas"]:
            self._planta(nombre, tipo, fila, col, vida, vmax)
        for nombre, fila, x, vida, vmax in snap["zombies"]:
            self._zombie(nombre, fila, x, vida, vmax)
        for fila, x in snap["guisantes"]:
            px, py = MARGEN + x * C, fila * C + 36
            c.create_oval(px - 6, py - 6, px + 6, py + 6, fill="#4cd137", outline="#1f6b2a", tags="dyn")
        self._dibujar_soles()          # encima de plantas y zombies
        mensaje = None
        if snap["resultado"] == "PLANTAS":
            mensaje = "¡GANAN LAS PLANTAS!"
        elif snap["resultado"] == "ZOMBIES":
            mensaje = "¡GANAN LOS ZOMBIES!"
        elif self.juego.en_pausa:
            mensaje = "PAUSA"
        if mensaje:
            c.create_rectangle(0, ALTO / 2 - 42, ANCHO, ALTO / 2 + 42, fill="black", stipple="gray50",
                               outline="", tags="dyn")
            c.create_text(ANCHO / 2, ALTO / 2, text=mensaje, fill="white",
                          font=("TkDefaultFont", 28, "bold"), tags="dyn")

    # ------------------------------------------- soles que flotan (efecto visual)
    def _avanzar_soles(self):
        """Lee los avisos que dejaron los hilos de girasol y hace avanzar la animación."""
        cola = self.juego.estado.efectos
        try:
            while True:
                fila, col, valor = cola.popleft()      # atómico: no necesita candado
                self.soles_anim.append({"fila": fila, "col": col, "valor": valor, "edad": 0.0})
        except IndexError:
            pass
        if not self.juego.en_pausa:                    # en pausa la animación se congela
            dt = REFRESCO_MS / 1000.0
            for a in self.soles_anim:
                a["edad"] += dt
            self.soles_anim = [a for a in self.soles_anim if a["edad"] < config.SOL_DURACION]

    def _dibujar_soles(self):
        c = self.canvas
        for a in self.soles_anim:
            p = a["edad"] / config.SOL_DURACION                       # 0 -> 1
            subida = config.SOL_ALTURA * (1 - (1 - p) ** 2)           # sube y va frenando
            if p < 0.15:
                escala = 0.4 + 0.6 * p / 0.15                         # aparece creciendo
            elif p < 0.7:
                escala = 1.0
            else:
                escala = max(0.0, 1.0 - (p - 0.7) / 0.3)              # se encoge y desaparece
            if escala <= 0.05:
                continue
            x0, y0 = MARGEN + a["col"] * C, a["fila"] * C
            cx = x0 + C * 0.60 + 4 * math.sin(a["edad"] * 7)          # pequeño balanceo
            cy = y0 + C * 0.42 - subida
            r = 15 * escala
            self._sol(cx, cy, r, giro=a["edad"] * 1.5)
            if escala > 0.5:
                tx, ty = cx + r * 1.6, cy
                texto = f"+{a['valor']}"
                fuente = ("TkDefaultFont", 10, "bold")
                c.create_text(tx + 1, ty + 1, text=texto, anchor="w", fill="white", font=fuente, tags="dyn")
                c.create_text(tx, ty, text=texto, anchor="w", fill="#b9770e", font=fuente, tags="dyn")

    def _sol(self, cx, cy, r, giro=0.0):
        """Dibuja un sol: halo, 8 rayos que giran, cuerpo amarillo y brillo."""
        c = self.canvas
        c.create_oval(cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35,
                      fill="#fff4a8", outline="", tags="dyn")
        for k in range(8):
            ang = giro + k * math.pi / 4
            a0, a1 = ang - 0.24, ang + 0.24
            c.create_polygon(
                cx + r * 0.9 * math.cos(a0), cy + r * 0.9 * math.sin(a0),
                cx + r * 1.55 * math.cos(ang), cy + r * 1.55 * math.sin(ang),
                cx + r * 0.9 * math.cos(a1), cy + r * 0.9 * math.sin(a1),
                fill="#ffb300", outline="#e69500", tags="dyn")
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffe033", outline="#f0a500", width=2, tags="dyn")
        c.create_oval(cx - r * 0.55, cy - r * 0.6, cx - r * 0.05, cy - r * 0.15,
                      fill="#fff9c4", outline="", tags="dyn")

    def _barra(self, x, y, ancho, vida, vmax):
        c = self.canvas
        c.create_rectangle(x, y, x + ancho, y + 5, fill="#c0392b", outline="", tags="dyn")
        c.create_rectangle(x, y, x + ancho * max(0, vida) / vmax, y + 5, fill="#27ae60", outline="", tags="dyn")

    def _planta(self, nombre, tipo, fila, col, vida, vmax):
        c = self.canvas
        x0, y0 = MARGEN + col * C, fila * C
        cx, cy = x0 + C / 2, y0 + C / 2 + 4
        if tipo == "girasol":
            for ang in range(0, 360, 45):
                dx, dy = 22 * math.cos(math.radians(ang)), 22 * math.sin(math.radians(ang))
                c.create_oval(cx + dx - 8, cy + dy - 8, cx + dx + 8, cy + dy + 8,
                              fill="#ffd23f", outline="#e0a800", tags="dyn")
            c.create_oval(cx - 13, cy - 13, cx + 13, cy + 13, fill="#7b4a12", outline="#4a2c08", tags="dyn")
        elif tipo == "lanzaguisantes":
            c.create_oval(cx - 22, cy - 16, cx + 12, cy + 22, fill="#3fae49", outline="#1f6b2a", width=2, tags="dyn")
            c.create_oval(cx + 4, cy - 10, cx + 28, cy + 6, fill="#2e8b3a", outline="#1f6b2a", width=2, tags="dyn")
            c.create_oval(cx + 22, cy - 6, cx + 32, cy + 2, fill="#173f1c", outline="", tags="dyn")
            c.create_oval(cx - 4, cy - 8, cx + 4, cy, fill="white", outline="", tags="dyn")
            c.create_oval(cx - 2, cy - 6, cx + 2, cy - 2, fill="black", outline="", tags="dyn")
        else:  # nuez
            c.create_oval(cx - 22, cy - 26, cx + 22, cy + 24, fill="#b07a3b", outline="#6f4a1d", width=2, tags="dyn")
            c.create_oval(cx - 12, cy - 10, cx - 4, cy - 2, fill="white", outline="", tags="dyn")
            c.create_oval(cx + 4, cy - 10, cx + 12, cy - 2, fill="white", outline="", tags="dyn")
            c.create_oval(cx - 9, cy - 7, cx - 6, cy - 4, fill="black", outline="", tags="dyn")
            c.create_oval(cx + 7, cy - 7, cx + 10, cy - 4, fill="black", outline="", tags="dyn")
        self._barra(x0 + 10, y0 + 4, C - 20, vida, vmax)
        c.create_text(x0 + C / 2, y0 + C - 6, text=nombre, font=F_CHICA, tags="dyn")

    def _zombie(self, nombre, fila, x, vida, vmax):
        c = self.canvas
        px, y0 = MARGEN + x * C, fila * C
        c.create_rectangle(px + 12, y0 + 34, px + 52, y0 + C - 16, fill="#6c7a89", outline="#2f3b46", tags="dyn")
        c.create_oval(px + 14, y0 + 12, px + 50, y0 + 44, fill="#a7c4a0", outline="#2f3b46", tags="dyn")
        c.create_oval(px + 20, y0 + 22, px + 27, y0 + 29, fill="white", outline="", tags="dyn")
        c.create_oval(px + 36, y0 + 22, px + 43, y0 + 29, fill="white", outline="", tags="dyn")
        c.create_oval(px + 22, y0 + 24, px + 25, y0 + 27, fill="#b71c1c", outline="", tags="dyn")
        c.create_oval(px + 38, y0 + 24, px + 41, y0 + 27, fill="#b71c1c", outline="", tags="dyn")
        self._barra(px + 10, y0 + 4, 44, vida, vmax)
        c.create_text(px + 32, y0 + C - 6, text=nombre, font=F_CHICA, tags="dyn")

    # --------------------------------------------------------------- eventos
    def _visible(self, nivel):
        if nivel == "hilo":
            return self.ver_hilos.get()
        if nivel == "detalle":
            return self.ver_detalle.get()
        return True

    def _vaciar_eventos(self):
        nuevos = []
        try:
            while True:
                nuevos.append(self.juego.eventos.get_nowait())
        except queue.Empty:
            pass
        visibles = [e for e in nuevos if self._visible(e[1])]
        if not visibles:
            return
        self.log.config(state="normal")
        for e in visibles:
            self.log.insert("end", self.juego.formatear(e) + "\n", e[1])
        lineas = int(self.log.index("end-1c").split(".")[0])
        if lineas > 600:
            self.log.delete("1.0", f"{lineas - 500}.0")
        self.log.see("end")
        self.log.config(state="disabled")

    def _mostrar_resumen(self):
        r = self.juego.resumen()
        h, s = r["hilos"], r["juego"]
        lineas = [
            "----------- RESUMEN DE LA PARTIDA -----------",
            f"Resultado: {r['resultado']}",
            f"Zombies: {s['zombies_creados']} creados, {s['zombies_muertos']} muertos | plantas sembradas: "
            f"{s['plantas_sembradas']}, perdidas: {s['plantas_perdidas']}",
            f"Disparos: {s['disparos']} (impactos: {s['impactos']}) | soles producidos: {s['soles_producidos']}",
            f"Hilos creados: {h['creados']} | máximo simultáneo: {h['max_vivos']} | "
            f"por rol: {', '.join(f'{k}={v}' for k, v in sorted(h['por_rol'].items()))}",
        ]
        self.log.config(state="normal")
        for linea in lineas:
            self.log.insert("end", linea + "\n", "juego")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------------------------------------------------------- panel derecho
    def _actualizar_info(self):
        r = self.juego.resumen()
        h, s = r["hilos"], r["juego"]
        por_rol = ", ".join(f"{k}={v}" for k, v in sorted(h["por_rol"].items()))
        self.lbl_info.config(text=(
            f"Hilos vivos: {h['vivos']}  (máx. simultáneo {h['max_vivos']}, creados en total {h['creados']})\n"
            f"threading.active_count(): {r['active_count']}  (incluye el MainThread = la GUI)\n"
            f"Zombies: {s['zombies_creados']} aparecidos / {s['zombies_muertos']} muertos de {config.TOTAL_ZOMBIES}"
            f"  |  plantas perdidas: {s['plantas_perdidas']}\n"
            f"Hilos creados por rol: {por_rol}"))

    def _actualizar_tabla(self):
        vistos = set()
        for d in self.juego.gestor.listado():
            iid = d["nombre"]
            valores = (d["nombre"], d["rol"], d["estado"], d["ciclos"], f"{d['edad']:.1f}", f"{d['lock_ms']:.2f}")
            if d["estado"] == "terminado":
                tag = "terminado"
            elif d["estado"] in ("esperando lock", "sección crítica"):
                tag = "lock"
            elif d["estado"] == "pausado":
                tag = "pausado"
            else:
                tag = ""
            if self.tabla.exists(iid):
                self.tabla.item(iid, values=valores, tags=(tag,))
            else:
                self.tabla.insert("", "end", iid=iid, values=valores, tags=(tag,))
            vistos.add(iid)
        for iid in self.tabla.get_children():
            if iid not in vistos:
                self.tabla.delete(iid)

    def _actualizar_grafica(self):
        g = self.grafica
        g.delete("all")
        W, H, ML, MR, MT, MB = 480, 140, 30, 10, 20, 18
        g.create_text(ML, 9, text="Hilos vivos en el tiempo (1 muestra cada 0.5 s)", anchor="w",
                      font=("TkDefaultFont", 8, "bold"))
        datos = self.juego.gestor.serie()
        ymax = max([10] + [n for _, n in datos])
        ymax = ((ymax + 4) // 5) * 5
        for v in range(0, ymax + 1, 5):
            y = H - MB - (H - MB - MT) * v / ymax
            g.create_line(ML, y, W - MR, y, fill="#e3e3e3")
            g.create_text(ML - 4, y, text=str(v), anchor="e", font=F_CHICA)
        if len(datos) >= 2:
            t0, t1 = datos[0][0], datos[-1][0]
            span = max(t1 - t0, 1e-6)
            puntos = []
            for t, n in datos:
                puntos += [ML + (W - ML - MR) * (t - t0) / span, H - MB - (H - MB - MT) * n / ymax]
            g.create_line(*puntos, fill="#1f77b4", width=2)
            g.create_text(W - MR, H - 6, text=f"ventana: {span:.0f} s", anchor="e", font=F_CHICA)
