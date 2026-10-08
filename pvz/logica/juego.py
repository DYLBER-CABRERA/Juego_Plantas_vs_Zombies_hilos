"""
CAPA 3 - LÓGICA / SERVICIO (reglas del juego) - VERSION COMENTADA LINEA POR LINEA
===============================================================================

Juego es la fachada que usa la vista. Reúne las piezas de las capas inferiores
(estado + gestor de hilos + eventos de sincronización) y aplica las reglas:
  - cuánto cuesta sembrar y en qué celdas
  - cuándo se gana o se pierde
  - arranque, pausa y parada de la partida

Primitivas de sincronización que viven aquí (se las pasa a todos los hilos):
  detener   Event  : se activa al terminar la partida -> todos los hilos salen
  corriendo Event  : activo = jugando, apagado = pausa
  eventos   Queue  : productor-consumidor. Los hilos PONEN mensajes; la vista los
                      SACA. Es la única vía por la que los hilos "hablan" con la GUI
                      (tkinter solo se puede tocar desde el hilo principal).
"""
import queue              # Para queue.Queue (cola thread-safe productor-consumidor)
import threading          # Para Event, Lock
import time               # Para time.monotonic()

from pvz import config                    # Constantes
from pvz.hilos.gestor import GestorHilos  # Administrador de hilos
from pvz.hilos.trabajadores import (      # Hilos concretos
    HiloAutoJugador, HiloCielo, HiloGirasol,
    HiloLanzaguisantes, HiloMonitor, HiloSpawner
)
from pvz.modelo.estado import EstadoJuego # Estado compartido thread-safe


class Juego:
    """Fachada principal: orquesta estado, hilos, eventos y reglas de victoria/derrota."""

    def __init__(self):
        self.estado = EstadoJuego()           # Estado compartido (plantas, zombies, soles, stats, locks)
        self.gestor = GestorHilos()           # Administrador de hilos (lanzar, listar, limpiar, detener)
        self.detener = threading.Event()      # Event GLOBAL: fin de partida
                                              # Cuando se hace .set(), TODOS los hilos ven parar en dormir()
        self.corriendo = threading.Event()    # Event GLOBAL: pausa/reanudar
                                              # .is_set() = jugando, .clear() = pausa
        self.eventos = queue.Queue()          # Cola thread-safe: hilos PRODUCEN, vista CONSUME
                                              # Items: (timestamp, nivel, nombre_hilo, texto)
        self.t0 = time.monotonic()            # Tiempo de inicio de partida (para timestamps relativos)
        self._lock_fin = threading.Lock()     # Lock para proteger finalización (solo uno decide fin)
        self._lock_ia = threading.Lock()      # Lock para activar/desactivar auto-jugador (thread-safe)
        self._ia = None                       # Referencia al hilo AutoJugador actual (o None)

    # ------------------------------------------------------------- CICLO DE VIDA
    def iniciar(self, autojugador=True):
        """
        Arranca una nueva partida. Lanza los hilos permanentes.
        
        Args:
            autojugador: bool - si True, activa la "IA" automática
        """
        self.corriendo.set()                  # Activa evento "corriendo" (juego NO en pausa)
        self.gestor.lanzar(HiloMonitor(self))     # 1. Monitor: limpia + muestrea para gráfica
        self.gestor.lanzar(HiloCielo(self))       # 2. Cielo: soles del cielo cada 9s
        self.gestor.lanzar(HiloSpawner(self))     # 3. Spawner: genera 12 zombies progresivamente
        if autojugador:
            self.activar_autojugador(True)    # 4. AutoJugador: "IA" que siembra sola (opcional)
        self.evento("Partida iniciada", "juego")  # Evento nivel "juego" para log

    def alternar_pausa(self):
        """
        Alterna pausa/juego. Llamado desde vista (botón Pausa/Reanudar).
        
        Returns:
            bool: True si quedó EN PAUSA, False si quedó JUGANDO
        """
        if self.detener.is_set():             # Si partida ya terminó, no hace nada
            return False
        if self.corriendo.is_set():           # Si está jugando -> PAUSA
            self.corriendo.clear()            # Apaga evento -> hilos ven pausado en dormir()
            self.evento("Juego en pausa", "juego")
        else:                                 # Si está en pausa -> REANUDAR
            self.corriendo.set()              # Enciende evento -> hilos salen de estado "pausado"
            self.evento("Juego reanudado", "juego")
        return not self.corriendo.is_set()    # True = en pausa, False = jugando

    @property
    def en_pausa(self):
        """True si el juego está en pausa (corriendo apagado Y no terminado)."""
        return not self.corriendo.is_set() and not self.detener.is_set()

    def detener_todo(self):
        """
        Cierre limpio: señal de parada global + join() de cada hilo.
        Llamado desde vista al cerrar ventana (App._cerrar).
        """
        self.detener.set()                    # Activa evento global -> TODOS los hilos terminan en su próximo dormir()
        self.gestor.detener_todos()           # Hace hilo.parar.set() a cada uno + join(timeout)

    def finalizar(self, resultado):
        """
        Declara fin de partida. Solo el PRIMERO en llegar decide (lock _lock_fin).
        Llamado desde HiloZombie (llego a casa) o Juego.zombie_muerto (victoria).
        
        Args:
            resultado: "PLANTAS" | "ZOMBIES"
        """
        with self._lock_fin:                  # Lock: solo uno puede entrar aquí
            if self.estado.resultado is not None:  # ¿Ya hay resultado?
                return                        # Sí -> ignora (evita doble fin)
            self.estado.resultado = resultado # Guarda resultado en estado
            texto = "GANAN LAS PLANTAS" if resultado == "PLANTAS" else "GANAN LOS ZOMBIES"
            self.evento(f"Fin de la partida: {texto}", "juego")  # Evento para log
            self.detener.set()                # Activa parada global -> todos los hilos terminan

    def zombie_muerto(self):
        """
        Comprueba condición de victoria: salieron TODOS los zombies Y no queda ninguno vivo.
        Llamado desde HiloZombie.cuerpo() cuando un zombie muere.
        """
        # Lee stats (bajo lock_stats) y zombies_vivos (bajo lock_tablero)
        if (self.estado.copiar_stats()["zombies_creados"] >= config.TOTAL_ZOMBIES
                and self.estado.zombies_vivos() == 0):
            self.finalizar("PLANTAS")         # Victoria: ganan plantas

    # ---------------------------------------------------------------- ACCIONES
    def sembrar(self, tipo, fila, col):
        """
        Siembra una planta. Lo llaman: vista (clic usuario) y auto-jugador (hilo IA).
        
        Args:
            tipo: str - "girasol" | "lanzaguisantes" | "nuez"
            fila: int - 0-4
            col: int - 0-8
            
        Returns:
            (bool, str): (True, "") si éxito, (False, motivo) si fallo
        """
        if self.detener.is_set():             # ¿Partida terminada?
            return False, "la partida terminó"
        planta, motivo = self.estado.plantar(tipo, fila, col)
            # EstadoJuego.plantar(): atómico (lock_tablero -> lock_soles)
            # Devuelve (planta, "") si éxito, (None, motivo) si fallo
        if planta is None:
            return False, motivo              # Fallo: celda ocupada o soles insuficientes
        
        self.evento(f"se siembra {planta.nombre} en ({fila},{col}) [-{config.COSTO[tipo]} soles]", "juego")
        
        # Lanza hilo correspondiente al tipo de planta:
        if tipo == "girasol":
            self.gestor.lanzar(HiloGirasol(self, planta))
        elif tipo == "lanzaguisantes":
            self.gestor.lanzar(HiloLanzaguisantes(self, planta))
        # La nuez es PASIVA: no necesita hilo (solo recibe daño en paso_zombie)
        return True, ""

    def quitar(self, fila, col):
        """
        Quita una planta (pala). Lo llama vista (clic con pala seleccionada).
        
        Returns:
            bool: True si quitó algo, False si no había planta
        """
        planta = self.estado.quitar_planta(fila, col)
            # EstadoJuego.quitar_planta(): bajo lock_tablero, pone planta.vida = 0
            # El hilo de la planta verá vida=0 en su próxima iteración y terminará
        if planta is None:
            return False
        self.evento(f"la pala quita a {planta.nombre}", "juego")
        return True

    def activar_autojugador(self, activo):
        """
        Activa/desactiva el hilo AutoJugador ("IA"). Thread-safe.
        Se puede llamar desde vista (checkbox) en caliente durante la partida.
        
        Args:
            activo: bool - True = encender IA, False = apagar IA
        """
        with self._lock_ia:                   # Lock: evita race condition encender/apagar simultáneo
            if activo and not self.detener.is_set() and (self._ia is None or not self._ia.is_alive()):
                # Encender: no terminado + no hay IA viva
                self._ia = HiloAutoJugador(self, self.estado.nuevo_nombre("AutoJugador"))
                self.gestor.lanzar(self._ia)  # Lanza nuevo hilo IA
            elif not activo and self._ia is not None:
                # Apagar: hay IA -> cancelación cooperativa
                self._ia.parar.set()          # Activa evento PARAR de ESE hilo específico
                                              # HiloAutoJugador.dormir() devolverá True y saldrá
                self._ia = None               # Limpia referencia

    # ----------------------------------------------------------------- EVENTOS
    def evento(self, texto, nivel="juego"):
        """
        Registra un evento en la cola. Lo llama CUALQUIER hilo.
        
        Args:
            texto: str - mensaje
            nivel: str - "juego" | "hilo" | "detalle" | "error" (para filtrado en vista)
        """
        # timestamp relativo (segundos desde inicio), nivel, nombre hilo actual, texto
        self.eventos.put((time.monotonic() - self.t0, nivel, threading.current_thread().name, texto))
        # queue.Queue.put() es thread-safe (bloquea si llena, pero Queue sin maxsize no se llena)

    @staticmethod
    def formatear(evento):
        """Formatea evento para mostrar en log: '[ 12.3s] [HiloNombre      ] mensaje'"""
        t, nivel, hilo, texto = evento
        return f"[{t:6.1f}s] [{hilo:<16}] {texto}"
        # t:6.1f -> 6 chars, 1 decimal (ej. " 12.3s")
        # hilo:<16 -> 16 chars alineado izquierda (ej. "Girasol-2       ")

    def resumen(self):
        """
        Devuelve diccionario con resumen completo para fin de partida.
        Llamado desde vista (App._mostrar_resumen) y modo_consola.
        
        Returns:
            dict: resultado, juego(stats), hilos(resumen), active_count
        """
        return {
            "resultado": self.estado.resultado or "en curso",
            "juego": self.estado.copiar_stats(),      # Copia stats (lock_stats)
            "hilos": self.gestor.resumen(),           # Resumen hilos (creados, max_vivos, por_rol)
            "active_count": threading.active_count(), # Hilos Python vivos (incluye MainThread)
        }