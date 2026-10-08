"""
CAPA 1 - MODELO (estado compartido, thread-safe)
====================================================================================

Es el recurso que comparten TODOS los hilos. Cada dato tiene un candado:

    lock_tablero (RLock) -> plantas, zombies, guisantes y sus atributos (vida, x)
    lock_soles   (Lock)  -> contador de soles
    lock_stats   (Lock)  -> estadísticas

ORDEN DE ADQUISICIÓN (evita interbloqueos / deadlock):
    tablero  ->  soles  ->  stats
Nunca se pide un candado "anterior" mientras se tiene uno "posterior".

Los hilos y la vista nunca recorren las listas directamente: usan los métodos de
esta clase o snapshot(), que devuelve COPIAS para poder dibujar sin bloquear.

Además hay una "bandeja de efectos" (self.efectos): los hilos dejan avisos para que
la vista los anime (p. ej. "este girasol acaba de producir un sol"). Es un deque
acotado: append() y popleft() son atómicos en CPython, así que no necesita candado,
y si nadie la lee (modo consola) nunca crece sin límite.
"""
import itertools          # Para contadores infinitos (itertools.count)
import threading          # Para locks (RLock, Lock) y sincronización
from collections import deque  # Cola doble acotada para efectos visuales

from pvz import config              # Constantes de configuración (velocidades, costos, etc.)
from pvz.modelo.entidades import Guisante, Planta, Zombie  # Clases de datos puras


class EstadoJuego:
    """Estado compartido thread-safe del juego. Un único objeto por partida."""

    def __init__(self):
        # ===== CANDADOS (Locks) =====
        self.lock_tablero = threading.RLock()   # RLock = Reentrant Lock: el MISMO hilo puede adquirirlo varias veces
                                                # Necesario porque plantar() llama a gastar_soles() que usa lock_soles
                                                # DENTRO del lock_tablero. Un Lock normal haría deadlock.
        self.lock_soles = threading.Lock()      # Lock simple (no reentrante) para contador de soles
        self.lock_stats = threading.Lock()      # Lock simple para estadísticas
        self._lock_nombres = threading.Lock()   # Lock privado para generar nombres únicos

        # ===== ESTRUCTURAS DE DATOS DEL TABLERO (protegidas por lock_tablero) =====
        self.plantas = {}        # Dict: clave (fila, col) -> objeto Planta. Acceso O(1) por posición
        self.zombies = []        # Lista de objetos Zombie. Se recorre linealmente (pocos zombies)
        self.guisantes = []      # Lista de objetos Guisante. Se recorre linealmente

        # ===== ECONOMÍA Y ESTADO GLOBAL (protegidas por lock_soles / lock_stats) =====
        self.soles = config.SOLES_INICIALES   # Soles disponibles para gastar (int)
        self.resultado = None                 # None | "PLANTAS" | "ZOMBIES" (fin de partida)
        self.stats = {                        # Diccionario de contadores para resumen final
            "zombies_creados": 0, "zombies_muertos": 0,
            "plantas_sembradas": 0, "plantas_perdidas": 0,
            "disparos": 0, "impactos": 0, "soles_producidos": 0,
        }
        self._contadores = {}                 # Dict interno: base_nombre -> itertools.count para nombres únicos

        # ===== BANDEJA DE EFECTOS VISUALES (sin candado - operaciones atómicas en CPython) =====
        # deque con maxlen=100: si se llena, descarta los más viejos automáticamente
        # append() y popleft() son atómicos gracias al GIL de CPython -> no necesita lock
        # Los hilos producen (append), la vista consume (popleft) cada 50ms
        self.efectos = deque(maxlen=100)      # Tuplas: (fila, col, valor_soles) para animar soles

    # ------------------------------------------------------------ UTILIDADES
    def nuevo_nombre(self, base):
        """
        Genera un nombre único para hilos/entidades: base-1, base-2, base-3...
        Thread-safe: usa _lock_nombres para proteger el dict de contadores.
        """
        with self._lock_nombres:                    # Adquiere lock privado (rápido, poca contención)
            cont = self._contadores.setdefault(base, itertools.count(1))
            # setdefault: si base no existe, crea itertools.count(1) y lo guarda
            # itertools.count(1) genera 1, 2, 3... infinito
            return f"{base}-{next(cont)}"           # next(cont) avanza el contador y devuelve el siguiente

    def sumar_stat(self, clave, n=1):
        """Incrementa un contador de estadísticas de forma thread-safe."""
        with self.lock_stats:                       # Adquiere lock_stats (orden: tablero -> soles -> stats)
            self.stats[clave] += n                  # Incrementa atómicamente

    def copiar_stats(self):
        """Devuelve una COPIA del dict de stats para leer sin retener candado."""
        with self.lock_stats:                       # Adquiere lock_stats brevemente
            return dict(self.stats)                 # Crea nuevo dict (copia superficial) y devuelve

    # ----------------------------------------------------------------- SOLES
    def soles_actuales(self):
        """Lee el contador de soles de forma thread-safe."""
        with self.lock_soles:                       # Adquiere lock_soles
            return self.soles                       # Devuelve valor (int es inmutable, seguro copiar)

    def sumar_soles(self, n):
        """Suma soles al contador y actualiza estadística. Thread-safe."""
        with self.lock_soles:                       # Adquiere lock_soles
            self.soles += n                         # Modifica entero (operación atómica en Python pero seguro con lock)
            total = self.soles                      # Captura valor para devolver
        self.sumar_stat("soles_producidos", n)      # Llama a sumar_stat (usa lock_stats, orden correcto)
        return total                                # Devuelve nuevo total

    def producir_sol(self, planta):
        """
        Un girasol produce soles: suma al contador y avisa a la vista para animarlo.
        Llamado desde HiloGirasol.cuerpo().
        """
        total = self.sumar_soles(config.SOLES_GIRASOL)  # Suma soles (usa lock_soles + lock_stats)
        self.efectos.append((planta.fila, planta.col, config.SOLES_GIRASOL))
        # append() en deque es atómico (GIL) -> no necesita lock
        # La vista leerá esto en _avanzar_soles() y animará el sol
        return total

    def gastar_soles(self, n):
        """Intenta gastar n soles. Devuelve True si había suficientes, False si no."""
        with self.lock_soles:                       # Adquiere lock_soles
            if self.soles < n:                      # Comprueba si alcanza
                return False                        # No hay suficientes
            self.soles -= n                         # Resta soles
            return True                             # Gasto exitoso

    # --------------------------------------------------------------- PLANTAS
    def plantar(self, tipo, fila, col):
        """
        Atómico: comprueba celda libre + cobra + inserta. Devuelve (planta, motivo).
        Llamado desde Juego.sembrar() (vista clic o auto-jugador).
        ORDEN DE LOCKS: tablero -> soles (correcto según convención)
        """
        with self.lock_tablero:                     # 1. Adquiere lock_tablero (RLock)
            if (fila, col) in self.plantas:         # Comprueba si celda ocupada (O(1) en dict)
                return None, "la celda está ocupada"
            if not self.gastar_soles(config.COSTO[tipo]):  # 2. Llama a gastar_soles -> adquiere lock_soles
                # ¡Aquí estamos DENTRO de lock_tablero Y adquiriendo lock_soles!
                # Por eso lock_tablero DEBE ser RLock (reentrante), no Lock.
                return None, "soles insuficientes"
            # Crea nueva entidad Planta con nombre único, vida según config
            planta = Planta(self.nuevo_nombre(tipo.capitalize()), tipo, fila, col,
                            config.VIDA[tipo], config.VIDA[tipo])
            self.plantas[(fila, col)] = planta      # Inserta en dict (clave = tupla fila,col)
        # Al salir del 'with', se libera lock_tablero (y lock_soles ya se liberó en gastar_soles)
        self.sumar_stat("plantas_sembradas")        # Incrementa stat (usa lock_stats, orden correcto)
        return planta, ""                           # Devuelve planta creada y motivo vacío (éxito)

    def quitar_planta(self, fila, col):
        """
        La pala. vida=0 es la señal para que el hilo de esa planta termine.
        Llamado desde Juego.quitar() (vista clic con pala).
        """
        with self.lock_tablero:                     # Adquiere lock_tablero
            planta = self.plantas.pop((fila, col), None)  # Elimina y devuelve planta, o None si no existe
            if planta is not None:
                planta.vida = 0                     # Marca vida=0 -> hilo de la planta lo verá y terminará
            return planta                           # Devuelve planta quitada (o None)

    def contar_plantas(self, tipo):
        """Cuenta cuántas plantas de un tipo hay en el tablero. Thread-safe."""
        with self.lock_tablero:                     # Adquiere lock_tablero
            return sum(1 for p in self.plantas.values() if p.tipo == tipo)  # Generador + sum

    # --------------------------------------------------------------- ZOMBIES
    def agregar_zombie(self, fila):
        """
        Crea un zombie nuevo en la fila dada, al final del tablero (x = COLUMNAS).
        Llamado desde HiloSpawner.cuerpo().
        """
        with self.lock_tablero:                     # Adquiere lock_tablero
            z = Zombie(self.nuevo_nombre("Zombie"), fila, float(config.COLUMNAS),
                       config.ZOMBIE_VIDA, config.ZOMBIE_VIDA)
            self.zombies.append(z)                  # Añade a lista
        self.sumar_stat("zombies_creados")          # Incrementa stat (lock_stats)
        return z                                    # Devuelve zombie creado (su hilo lo usará)

    def zombies_vivos(self):
        """Cuenta zombies con vida > 0. Thread-safe."""
        with self.lock_tablero:                     # Adquiere lock_tablero
            return sum(1 for z in self.zombies if z.vida > 0)  # Cuenta vivos

    def hay_zombie_adelante(self, fila, col):
        """
        Comprueba si hay algún zombie vivo en la misma fila a la derecha de la columna dada.
        Llamado desde HiloLanzaguisantes.cuerpo() para decidir si disparar.
        """
        with self.lock_tablero:                     # Adquiere lock_tablero
            return any(z.fila == fila and z.vida > 0 and z.x >= col for z in self.zombies)

    def paso_zombie(self, z):
        """
        Un "tick" de la vida de un zombie (lo llama SU hilo). Todo ocurre bajo el
        candado del tablero para que nadie vea la planta o el zombie a medias.
        Devuelve (accion, planta): "muerto" | "mordisco" | "comio" | "avanza" | "llego"
        """
        with self.lock_tablero:                     # Adquiere lock_tablero (sección crítica completa)
            if z.vida <= 0:                         # ¿Ya está muerto? (puede pasar si lo mataron entre ticks)
                if z in self.zombies:               # Comprueba si sigue en lista
                    self.zombies.remove(z)          # Elimina de lista
                return "muerto", None               # Acción: muerto, sin planta
            clave = (z.fila, int(z.x))              # Celda donde está el zombie (x es float, int() trunca)
            planta = self.plantas.get(clave)        # Busca planta en esa celda (O(1))
            if planta is not None:                  # ¡Hay planta en la celda!
                planta.vida -= 1                    # Muerde: resta 1 de vida a la planta
                if planta.vida <= 0:                # ¿Planta muere?
                    del self.plantas[clave]         # Elimina planta del tablero
                    return "comio", planta          # Acción: se comió la planta
                return "mordisco", planta           # Acción: mordió pero planta vive
            z.x -= config.VEL_ZOMBIE                # No hay planta: avanza hacia la izquierda
            if z.x < 0:                             # ¿Llegó a la casa (x < 0)?
                return "llego", None                # Acción: llegó a casa -> FIN PARTIDA
            return "avanza", None                   # Acción: simplemente avanza

    # ------------------------------------------------------------- GUISANTES
    def agregar_guisante(self, planta):
        """
        Crea un guisante disparado por una planta. Llamado desde HiloLanzaguisantes.cuerpo().
        """
        with self.lock_tablero:                     # Adquiere lock_tablero
            g = Guisante(self.nuevo_nombre("Guisante"), planta.fila, planta.col + 0.8)
            # Empieza ligeramente a la derecha de la planta (col + 0.8 celdas)
            self.guisantes.append(g)                # Añade a lista
        self.sumar_stat("disparos")                 # Incrementa stat disparos (lock_stats)
        return g                                    # Devuelve guisante (su hilo lo usará)

    def avanzar_guisante(self, g):
        """
        Mueve el guisante y detecta impacto. Devuelve ('vuela'|'impacto'|'fuera', zombie).
        Llamado desde HiloGuisante.cuerpo().
        """
        with self.lock_tablero:                     # Adquiere lock_tablero (sección crítica completa)
            g.x += config.VEL_GUISANTE              # Avanza guisante hacia la derecha
            for z in self.zombies:                  # Recorre zombies (búsqueda lineal, pocos zombies)
                if z.fila == g.fila and z.vida > 0 and z.x <= g.x <= z.x + 0.8:
                    # Mismo fila + zombie vivo + guisante dentro del rango horizontal del zombie
                    z.vida -= 1                     # ¡Impacto! Resta 1 vida al zombie
                    resultado = ("impacto", z)      # Guarda resultado y zombie golpeado
                    break                           # Sale del bucle (un guisante golpea a uno solo)
            else:
                # else del for: se ejecuta si NO hubo break (ningún impacto)
                resultado = ("fuera", None) if g.x >= config.COLUMNAS else ("vuela", None)
                # Si x >= COLUMNAS -> salió del tablero ("fuera"), sino sigue volando ("vuela")
        # Lock liberado aquí (sale del with)
        if resultado[0] == "impacto":               # Si hubo impacto
            self.sumar_stat("impactos")             # Incrementa stat impactos (lock_stats)
        return resultado                            # Devuelve tupla (acción, zombie_o_None)

    def quitar_guisante(self, g):
        """Elimina guisante de la lista (cuando impacta o sale). Llamado desde HiloGuisante.cuerpo()."""
        with self.lock_tablero:                     # Adquiere lock_tablero
            if g in self.guisantes:                 # Comprueba si sigue en lista
                self.guisantes.remove(g)            # Elimina (búsqueda lineal + remove)

    # -------------------------------------------------------------- SNAPSHOT
    def snapshot(self):
        """
        Copia inmutable del estado para que la vista dibuje sin retener candados.
        Llamado desde App._refrescar() cada 50ms y desde HiloAutoJugador.cuerpo().
        PATRÓN: Copia rápida bajo lock -> libera lock -> devuelve copia.
        """
        with self.lock_tablero:                     # Adquiere lock_tablero brevemente
            # Crea listas de TUPLAS (inmutables) con datos necesarios para dibujar
            plantas = [(p.nombre, p.tipo, p.fila, p.col, p.vida, p.vida_max)
                       for p in self.plantas.values()]
            zombies = [(z.nombre, z.fila, z.x, z.vida, z.vida_max)
                       for z in self.zombies if z.vida > 0]  # Solo vivos
            guisantes = [(g.fila, g.x) for g in self.guisantes]  # Solo posición
        # Lock liberado ANTES de leer soles y stats (orden correcto: tablero -> soles -> stats)
        return {
            "plantas": plantas,
            "zombies": zombies,
            "guisantes": guisantes,
            "soles": self.soles_actuales(),         # Usa método thread-safe (lock_soles)
            "stats": self.copiar_stats(),           # Usa método thread-safe (lock_stats)
            "resultado": self.resultado,            # Es inmutable (str o None), seguro leer sin lock
        }