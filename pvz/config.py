"""
CAPA 0 - CONFIGURACIÓN
Solo constantes. No contiene lógica ni hilos. Cambia estos valores para
experimentar (más zombies, otro ritmo de disparo, etc.) sin tocar el resto.
Los tiempos están en segundos de juego (se congelan cuando se pausa).
"""

# ---------- Tablero ----------
FILAS = 5
COLUMNAS = 9
TAM_CELDA = 80            # píxeles (solo la vista lo usa)

# ---------- Hilos ----------
TICK = 0.05               # cada cuánto un hilo dormido revisa "pausa" y "parar"
GRACIA_HILO_TERMINADO = 3.0   # cuánto se muestra un hilo ya terminado en la tabla
PERIODO_MONITOR = 0.5
HISTORIAL_HILOS = 240     # muestras de la gráfica (240 x 0.5 s = 2 min)

# ---------- Economía ----------
SOLES_INICIALES = 150
PERIODO_CIELO = 9.0       # cada cuánto cae un sol del cielo
SOLES_CIELO = 25

COSTO = {"girasol": 50, "lanzaguisantes": 100, "nuez": 50}
VIDA = {"girasol": 6, "lanzaguisantes": 6, "nuez": 30}

# ---------- Plantas ----------
PERIODO_GIRASOL = 6.0
SOLES_GIRASOL = 25
PERIODO_DISPARO = 1.2

# ---------- Guisantes (1 hilo por guisante) ----------
PASO_GUISANTE = 0.05
VEL_GUISANTE = 0.25       # celdas por paso  (= 5 celdas/s)

# ---------- Zombies (1 hilo por zombie) ----------
ZOMBIE_VIDA = 8
PASO_ZOMBIE = 0.2
VEL_ZOMBIE = 0.08         # celdas por paso  (= 0.40 celdas/s)
PASO_MORDISCO = 0.8       # un mordisco = 1 punto de vida a la planta

# ---------- Oleada ----------
TOTAL_ZOMBIES = 12
ESPERA_PRIMER_ZOMBIE = 8.0
ESPERA_ZOMBIE = (4.0, 7.0)      # rango aleatorio entre zombies
ESPERA_MINIMA = 2.5
ACELERACION_OLEADA = 0.25       # cada zombie reduce la espera en 0.25 s

# ---------- Auto-jugador (hilo "IA") ----------
PERIODO_IA = 1.5

# ---------- Efecto visual del sol (solo lo usa la vista) ----------
SOL_DURACION = 1.8        # segundos que dura el sol flotando (se congela en pausa)
SOL_ALTURA = 46           # píxeles que sube
