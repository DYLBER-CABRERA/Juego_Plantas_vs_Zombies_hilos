"""
CAPA 2 - HILOS (gestión) - VERSION COMENTADA LINEA POR LINEA
============================================================

GestorHilos es el "administrador de hilos" de la partida:
  * lanzar(hilo)      -> start() + registro (creados, máximo simultáneo, por rol)
  * limpiar()         -> olvida los hilos terminados hace más de GRACIA segundos
  * listado()         -> datos de cada hilo para la tabla de la vista
  * muestrear()/serie -> historial de "hilos vivos" para la gráfica
  * detener_todos()   -> parada cooperativa + join() de todos

Su propio candado (_lock) es independiente de los del estado del juego.
"""
import threading          # Para Lock
import time               # Para time.monotonic()
from collections import deque  # Cola doble acotada para historial de gráfica

from pvz import config    # Constantes (HISTORIAL_HILOS, GRACIA_HILO_TERMINADO)


class GestorHilos:
    """Administra el ciclo de vida y métricas de TODOS los hilos de la partida."""

    def __init__(self):
        self._lock = threading.Lock()       # Lock PROPIO del gestor (independiente de EstadoJuego)
                                            # Protege: _hilos, creados, max_vivos, por_rol, _historial
        self._hilos = []                    # Lista de TODOS los hilos lanzados (referencias a HiloBase)
        self.creados = 0                    # Contador total de hilos creados en la partida
        self.max_vivos = 0                  # Máximo de hilos vivos SIMULTÁNEOS alcanzado
        self.por_rol = {}                   # Dict: rol -> cantidad creados (ej. {"enemigo": 12, "productor": 5})
        self._historial = deque(maxlen=config.HISTORIAL_HILOS)
            # Historial para gráfica: deque de tuplas (timestamp, hilos_vivos)
            # maxlen=240 -> guarda 240 muestras. A 0.5s por muestra = 2 minutos de historia

    # ------------------------------------------------------------------ CREAR
    def lanzar(self, hilo):
        """
        Lanza un hilo: hace start() y lo registra en las estadísticas.
        
        Args:
            hilo: instancia de HiloBase (o subclase) YA CONSTRUIDA pero sin start()
            
        Returns:
            el mismo hilo (para encadenar: gestor.lanzar(MiHilo(ctx, nombre)))
        """
        hilo.start()                        # 1. Inicia el hilo (llama a run() en nuevo hilo de ejecución)
                                            # IMPORTANTE: se registra DESPUÉS de start() para que nunca
                                            # se vea un hilo "no iniciado" en la tabla
        with self._lock:                    # 2. Adquiere lock del gestor para actualizar contadores
            self._hilos.append(hilo)        # Añade a lista maestra
            self.creados += 1               # Incrementa total creados
            self.por_rol[hilo.rol] = self.por_rol.get(hilo.rol, 0) + 1
            # Incrementa contador por rol (get devuelve 0 si no existe)
            vivos = sum(1 for h in self._hilos if h.is_alive())
            # Cuenta cuántos están vivos AHORA (is_alive() = True si hilo started y no terminó)
            self.max_vivos = max(self.max_vivos, vivos)
            # Actualiza máximo simultáneo histórico
        return hilo                         # Devuelve hilo para posible uso posterior

    # ---------------------------------------------------------------- CONSULTAR
    def listado(self):
        """
        Devuelve lista de dicts con datos de cada hilo para la tabla de la vista.
        Llamado desde App._actualizar_tabla() cada ~250ms (cada 5 frames).
        
        Returns:
            list[dict]: cada dict tiene keys: nombre, rol, estado, ciclos, edad, lock_ms, vivo
        """
        with self._lock:                    # Adquiere lock del gestor (rápido, solo lectura)
            hilos = list(self._hilos)       # Copia superficial de la lista (para iterar sin lock)
        # FUERA del lock: construye lista de dicts (operación más costosa)
        return [{
            "nombre": h.name,               # Nombre del hilo (ej. "Girasol-2")
            "rol": h.rol,                   # Rol: "productor", "enemigo", "proyectil", etc.
            "estado": h.estado,             # Estado actual: "activo", "durmiendo", "pausado", etc.
            "ciclos": h.ciclos,             # Iteraciones del bucle principal completadas
            "edad": h.edad(),               # Segundos vivo (float, usa time.monotonic())
            "lock_ms": h.espera_lock * 1000.0,  # Milisegundos acumulados esperando lock_tablero
            "vivo": h.is_alive(),           # True si hilo started y no terminó
        } for h in hilos]

    def vivos(self):
        """Devuelve cuántos hilos están vivos ahora. Thread-safe."""
        with self._lock:                    # Adquiere lock
            return sum(1 for h in self._hilos if h.is_alive())  # Cuenta vivos

    def resumen(self):
        """
        Devuelve resumen final de hilos para el reporte de fin de partida.
        Llamado desde Juego.resumen().
        
        Returns:
            dict: creados, max_vivos, vivos, por_rol
        """
        with self._lock:                    # Adquiere lock
            return {
                "creados": self.creados,
                "max_vivos": self.max_vivos,
                "vivos": sum(1 for h in self._hilos if h.is_alive()),
                "por_rol": dict(self.por_rol),  # Copia del dict por_rol
            }

    # --------------------------------------------------------------- MANTENER
    def limpiar(self):
        """
        Elimina de la lista los hilos terminados hace más de GRACIA_HILO_TERMINADO segundos.
        Llamado desde HiloMonitor.cuerpo() cada 0.5s.
        Permite que la tabla muestre hilos terminados unos segundos antes de borrarlos.
        """
        ahora = time.monotonic()            # Tiempo actual
        with self._lock:                    # Adquiere lock
            # Filtra: mantiene si está vivo O si terminó hace poco (menos de GRACIA segundos)
            self._hilos = [h for h in self._hilos
                           if h.is_alive() or ahora - (h.fin or 0) <= config.GRACIA_HILO_TERMINADO]
            # h.fin = time.monotonic() cuando terminó (None si no terminó)
            # (h.fin or 0) -> 0 si None, sino h.fin

    def muestrear(self, t):
        """
        Registra una muestra de "hilos vivos" en el historial para la gráfica.
        Llamado desde HiloMonitor.cuerpo() cada 0.5s.
        
        Args:
            t: float - timestamp (segundos desde inicio de partida, time.monotonic() - t0)
        """
        n = self.vivos()                    # Cuenta hilos vivos ahora (adquiere y libera _lock)
        with self._lock:                    # Adquiere lock para escribir en historial
            self._historial.append((t, n))  # Añade tupla (tiempo, hilos_vivos)
            # deque con maxlen descarta automáticamente las más viejas si se llena

    def serie(self):
        """
        Devuelve copia del historial para dibujar la gráfica.
        Llamado desde App._actualizar_grafica().
        
        Returns:
            list[tuple]: [(t, n), (t, n), ...] copia del historial
        """
        with self._lock:                    # Adquiere lock
            return list(self._historial)    # Copia superficial de la lista de tuplas

    # ---------------------------------------------------------------- TERMINAR
    def detener_todos(self, timeout=2.0):
        """
        Parada cooperativa de TODOS los hilos + join() para cierre limpio.
        Llamado desde Juego.detener_todo() al cerrar ventana o fin de partida.
        
        Args:
            timeout: float - segundos máximos a esperar por hilo en join()
        """
        with self._lock:                    # Adquiere lock para copiar lista
            hilos = list(self._hilos)       # Copia para iterar sin lock
        # FUERA del lock: señalamos parada a todos
        for h in hilos:
            h.parar.set()                   # Activa evento PARAR de CADA hilo individualmente
                                            # Esto hace que dormir() devuelva True en su próxima iteración
        limite = time.monotonic() + timeout # Tiempo límite absoluto para todos los joins
        for h in hilos:
            if h.is_alive():                # Solo hace join si sigue vivo
                restante = max(0.0, limite - time.monotonic())
                # Calcula tiempo restante (no negativo)
                h.join(restante)            # Espera a que el hilo termine (máx. 'restante' segundos)
                                            # join() bloquea ESTE hilo (MainThread) hasta que h termine