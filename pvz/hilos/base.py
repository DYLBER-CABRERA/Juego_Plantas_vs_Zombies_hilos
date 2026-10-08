"""
CAPA 2 - HILOS (clase base)
===============================================================

HiloBase extiende threading.Thread y le agrega lo que todos los hilos del juego
necesitan:
  * cooperación para terminar: el evento global ctx.detener (fin de partida) y
    el evento propio self.parar (cancelar solo este hilo, p. ej. al quitar una planta)
  * pausa: ctx.corriendo (Event). Si está apagado, los hilos se quedan dormidos.
  * observabilidad: estado, ciclos, tiempo vivo y tiempo esperando un candado,
    que la vista muestra en la tabla de hilos.

"ctx" es el servicio de la capa de lógica (Juego), inyectado por constructor. Los
hilos NO lo importan: solo usan ctx.estado, ctx.detener, ctx.corriendo, ctx.gestor,
ctx.evento(...), ctx.sembrar(...), ctx.zombie_muerto() y ctx.finalizar(...).
"""
import threading          # Para Thread, Event, Lock
import time               # Para time.monotonic(), time.perf_counter()
from contextlib import contextmanager  # Para crear context managers con @contextmanager

from pvz import config    # Constantes (TICK = 0.05s)


class HiloBase(threading.Thread):
    """Clase base para TODOS los hilos del juego. Extiende threading.Thread."""

    rol = "hilo"          # Atributo de clase: rol por defecto (sobrescrito en subclases)
                          # Valores típicos: "productor", "atacante", "proyectil", "enemigo",
                          # "generador", "monitor", "ia"

    def __init__(self, ctx, nombre):
        """
        Inicializa el hilo base.
        
        Args:
            ctx: Contexto (instancia de Juego) - inyección de dependencias.
                 Provee: ctx.estado, ctx.detener, ctx.corriendo, ctx.gestor, ctx.evento()
            nombre: str - nombre único del hilo (ej. "Girasol-2", "Zombie-5")
        """
        super().__init__(name=nombre, daemon=True)  
        # super().__init__: llama a threading.Thread.__init__
        # name=nombre: establece nombre del hilo (visible en threading.current_thread().name)
        # daemon=True: hilo demonio -> NO impide que el programa termine.
        #              Si solo quedan hilos daemon, Python sale. 
        #              Pero usamos join() en detener_todos() para cierre limpio.
        
        self.ctx = ctx                    # Referencia al contexto (Juego) - inyección de dependencias
        self.parar = threading.Event()    # Evento PRIVADO de este hilo para cancelación individual
                                          # Ej: al quitar una planta con pala, se hace hilo.parar.set()
        self.estado = "creado"            # Estado visible para la tabla de hilos:
                                          # "creado" | "activo" | "durmiendo" | "pausado" | 
                                          # "esperando lock" | "sección crítica" | "terminado"
        self.ciclos = 0                   # Contador de iteraciones del bucle principal (cuerpo)
        self.inicio = None                # time.monotonic() cuando empieza run() (float segundos)
        self.fin = None                   # time.monotonic() cuando termina (float segundos)
        self.espera_lock = 0.0            # Segundos ACUMULADOS esperando lock_tablero (float)
                                          # Se usa para medir contención (columna "Lock ms" en tabla)

    # ------------------------------------------------------------ CICLO DE VIDA
    def run(self):
        """
        Método que ejecuta threading.Thread al hacer hilo.start().
        NO sobrescribir este método en subclases; sobrescribir cuerpo() en su lugar.
        """
        self.inicio = time.monotonic()    # Marca tiempo de inicio (reloj monótono, no afecta por cambios de hora)
        self.estado = "activo"            # Cambia estado visible
        try:
            self.cuerpo()                 # Llama al método abstracto cuerpo() (implementado en subclase)
                                          # Aquí es donde el hilo hace su trabajo real
        except Exception as e:            # Captura CUALQUIER excepción no manejada en cuerpo()
            # Un hilo que muere en silencio es imposible de depurar
            self.ctx.evento(f"ERROR: {e!r}", "error")  # Envía evento a cola (nivel "error")
        finally:
            self.estado = "terminado"     # Siempre se ejecuta: marca como terminado
            self.fin = time.monotonic()   # Marca tiempo de fin

    def cuerpo(self):
        """Método abstracto: cada subclase DEBE implementar su lógica aquí."""
        raise NotImplementedError         # Lanza error si subclase no lo implementa

    # ---------------------------------------------------------------- UTILIDADES
    def edad(self):
        """Devuelve cuántos segundos lleva vivo el hilo (float)."""
        if self.inicio is None:           # Si aún no empezó (no debería pasar si se llama tras start)
            return 0.0
        return (self.fin or time.monotonic()) - self.inicio
        # Si self.fin existe (hilo terminado), usa fin; sino usa ahora (time.monotonic())

    def dormir(self, seg):
        """
        Espera 'seg' segundos DE JUEGO cooperativamente.
        
        Se hace en rebanadas de TICK (0.05s) para poder:
          - terminar al instante si termina la partida o se cancela este hilo
          - congelarse mientras el juego está en pausa
          
        Args:
            seg: float - segundos a dormir
            
        Returns:
            bool: True si el hilo debe terminar (fin de partida o cancelación), False si durmió completo
        """
        restante = seg                    # Segundos que faltan por dormir
        while True:                       # Bucle de rebanadas
            # 1. ¿Fin de partida global O cancelación individual?
            # ctx.detener.wait(TICK) espera HASTA TICK segundos por el evento.
            # Devuelve True si el evento se activó, False si timeout.
            # self.parar.is_set() comprueba si ESTE hilo fue cancelado individualmente.
            if self.ctx.detener.wait(config.TICK) or self.parar.is_set():
                return True               # -> Hilo DEBE terminar YA (return True = salir de cuerpo)
            
            # 2. ¿Juego en pausa?
            if not self.ctx.corriendo.is_set():
                self.estado = "pausado"   # Actualiza estado visible para la tabla
                continue                  # Vuelve al while: vuelve a esperar TICK sin consumir 'restante'
            
            # 3. Durmiendo de verdad (juego corriendo, no hay fin)
            self.estado = "durmiendo"     # Actualiza estado visible
            restante -= config.TICK       # Resta el tick que acabamos de esperar
            if restante <= 0:             # ¿Ya dormimos todo lo pedido?
                break                     # Sale del while: durmió completo
        
        self.estado = "activo"            # Volvió a estado activo tras dormir
        return False                      # False = durmió completo, NO terminar

    @contextmanager
    def seccion(self):
        """
        Context manager para sección crítica sobre el tablero.
        
        Mide cuánto tiempo se espera el candado (contención) y deja el estado
        visible en la tabla ("esperando lock" -> "sección crítica").
        
        Uso:
            with self.seccion():
                # código que accede a datos compartidos bajo lock_tablero
                resultado = self.ctx.estado.alguna_operacion()
        
        El 'with' garantiza que el lock se libera incluso si hay excepción.
        """
        lock = self.ctx.estado.lock_tablero  # Obtiene referencia al RLock del tablero
        self.estado = "esperando lock"       # Estado visible: esperando para entrar
        t0 = time.perf_counter()             # Marca tiempo ANTES de intentar adquirir lock
                                             # perf_counter() = alta resolución para medir intervalos
        with lock:                           # Adquiere lock_tablero (bloquea si otro lo tiene)
            # --- AQUÍ ESTAMOS DENTRO DE LA SECCIÓN CRÍTICA ---
            self.espera_lock += time.perf_counter() - t0
            # Acumula tiempo que esperamos (perf_counter() - t0) en segundos
            # Esto mide CONTENCIÓN REAL del candado del tablero
            self.estado = "sección crítica"  # Estado visible: dentro de sección crítica
            yield                            # Pausa aquí: ejecuta el bloque 'with' del llamador
            # --- AL SALIR DEL BLOQUE 'with' DEL LLAMADOR ---
        # Lock liberado automáticamente al salir del 'with lock:'
        self.estado = "activo"               # Vuelve a estado activo