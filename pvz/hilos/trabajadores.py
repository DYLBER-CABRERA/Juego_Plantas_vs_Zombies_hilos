"""
CAPA 2 - HILOS (los hilos concretos del juego)
=================================================================================

Hilos permanentes (viven toda la partida)     Hilos dinámicos (nacen y mueren)
  Monitor      muestrea y limpia hilos           HiloGirasol         1 por girasol
  Cielo        deja caer soles                   HiloLanzaguisantes  1 por lanzaguisantes
  Spawner      crea zombies (termina al acabar)  HiloGuisante        1 por disparo (vive ~2 s)
  AutoJugador  "IA" opcional (se activa/apaga)   HiloZombie          1 por zombie

La Nuez NO tiene hilo: es una entidad pasiva (solo recibe daño). No todo necesita un hilo.

Todos siguen el mismo patrón:  while True: dormir -> comprobar si hay que seguir -> actuar.
Todo acceso a datos compartidos pasa por EstadoJuego (que usa sus candados).
"""
import random               # Para random.randrange, random.uniform, random.random
import time                 # Para time.monotonic()
from collections import Counter  # Para contar plantas por tipo/fila

from pvz import config              # Constantes (velocidades, costos, períodos)
from pvz.hilos.base import HiloBase # Clase base con dormir(), seccion(), parada cooperativa


# ====================================================================== PLANTAS
class HiloGirasol(HiloBase):
    """Hilo de un Girasol: produce soles cada PERIODO_GIRASOL (6s)."""
    rol = "productor"       # Rol para estadísticas y tabla

    def __init__(self, ctx, planta):
        """
        Args:
            ctx: contexto Juego (inyectado)
            planta: entidad Planta (datos: nombre, fila, col, vida, vida_max)
        """
        super().__init__(ctx, planta.nombre)  # Llama a HiloBase.__init__(ctx, nombre)
        self.planta = planta                  # Guarda referencia a la entidad Planta

    def cuerpo(self):
        """Bucle principal del girasol: produce soles cada 6s hasta que muere."""
        p = self.planta                       # Referencia local para acceso rápido
        self.ctx.evento(f"nace en ({p.fila},{p.col})", "hilo")  # Evento nivel "hilo" (nacimiento)
        while p.vida > 0:                     # Condición: mientras planta viva
            # LECTURA SIN CANDADO: p.vida es int, solo comparación con 0.
            # En CPython, lectura/escritura de int es atómica (GIL).
            # El hilo zombie escribe p.vida BAJO lock_tablero, pero aquí solo leemos.
            # Race condition benigna: si zombie pone vida=0 justo después de esta comprobación,
            # el girasol hará UN ciclo extra (dormir -> producir_sol) y luego saldrá.
            
            if self.dormir(config.PERIODO_GIRASOL):  # Duerme 6s (cooperativo: revisa fin/pausa cada 0.05s)
                break                           # True = fin de partida o cancelación -> salir
            if p.vida <= 0:                     # Re-comprueba tras dormir (por si murió mientras dormía)
                break
            total = self.ctx.estado.producir_sol(p)  
                # Llama a EstadoJuego.producir_sol():
                # 1. Suma soles (lock_soles + lock_stats)
                # 2. Deja aviso en efectos (deque.append atómico) para vista
            self.ciclos += 1                    # Incrementa contador de ciclos (para tabla)
            self.ctx.evento(f"produce {config.SOLES_GIRASOL} soles (total {total})", "detalle")
                # Evento nivel "detalle" (solo visible si checkbox "Ver detalle" activado)
        self.ctx.evento("termina", "hilo")      # Evento nivel "hilo" (fin de hilo)


class HiloLanzaguisantes(HiloBase):
    """Hilo de un Lanzaguisantes: dispara guisantes si hay zombie adelante."""
    rol = "atacante"

    def __init__(self, ctx, planta):
        super().__init__(ctx, planta.nombre)
        self.planta = planta

    def cuerpo(self):
        """Bucle principal: cada 1.2s comprueba si hay zombie y dispara."""
        p = self.planta
        est = self.ctx.estado                   # Referencia local a EstadoJuego
        self.ctx.evento(f"nace en ({p.fila},{p.col})", "hilo")
        while p.vida > 0:                       # Mientras planta viva (lectura sin lock, atómica)
            if self.dormir(config.PERIODO_DISPARO):  # Duerme 1.2s (cooperativo)
                break
            # Comprueba si hay zombie vivo en misma fila a la derecha de la planta
            # hay_zombie_adelante usa lock_tablero internamente
            if p.vida > 0 and est.hay_zombie_adelante(p.fila, p.col):
                g = est.agregar_guisante(p)     # Crea entidad Guisante (bajo lock_tablero)
                # ¡UN HILO CREANDO OTRO HILO EN PLENO JUEGO!
                self.ctx.gestor.lanzar(HiloGuisante(self.ctx, g))
                # Lanza HiloGuisante que vivirá ~2s, se moverá y impactará
                self.ciclos += 1                # Incrementa ciclos (disparo = 1 ciclo)
        self.ctx.evento("termina", "hilo")


class HiloGuisante(HiloBase):
    """Hilo de un Guisante: proyectil que vuela, golpea y muere (vida ~2s)."""
    rol = "proyectil"

    def __init__(self, ctx, guisante):
        super().__init__(ctx, guisante.nombre)
        self.guisante = guisante                # Entidad Guisante (nombre, fila, x)

    def cuerpo(self):
        """Bucle: avanza 0.25 celdas cada 0.05s hasta impactar o salir."""
        est = self.ctx.estado
        while not self.dormir(config.PASO_GUISANTE):  # Duerme 0.05s (cooperativo)
            # NOTA: dormir devuelve True si debe terminar, False si durmió completo
            # while not self.dormir(...) -> continúa mientras durmió completo
            
            with self.seccion():                # Context manager: mide contención lock_tablero
                # Dentro: estado = "esperando lock" -> "sección crítica" -> "activo"
                resultado, zombie = est.avanzar_guisante(self.guisante)
                # Avanza guisante y detecta impacto (bajo lock_tablero)
                # Devuelve: ("vuela", None) | ("impacto", zombie) | ("fuera", None)
            self.ciclos += 1                    # Cada avance = 1 ciclo
            if resultado == "impacto":          # ¡Golpeó a un zombie!
                self.ctx.evento(f"golpea a {zombie.nombre} (vida {max(0, zombie.vida)})", "detalle")
                break                           # Sale del bucle -> hilo termina
            if resultado == "fuera":            # Salió del tablero (x >= COLUMNAS)
                break                           # Sale del bucle -> hilo termina
        est.quitar_guisante(self.guisante)      # Elimina guisante de lista (bajo lock_tablero)


# ====================================================================== ZOMBIES
class HiloZombie(HiloBase):
    """Hilo de un Zombie: camina, muerde plantas, muere o llega a casa."""
    rol = "enemigo"

    def __init__(self, ctx, zombie):
        super().__init__(ctx, zombie.nombre)
        self.zombie = zombie                    # Entidad Zombie (nombre, fila, x, vida, vida_max)

    def cuerpo(self):
        """Bucle principal del zombie: paso_zombie + dormir variable."""
        est = self.ctx.estado
        z = self.zombie
        self.ctx.evento(f"aparece en la fila {z.fila}", "juego")  # Evento nivel "juego" (importante)
        while True:                             # Bucle infinito (sale por return dentro)
            with self.seccion():                # Sección crítica: TODO el paso atómico
                accion, planta = est.paso_zombie(z)
                # paso_zombie bajo lock_tablero:
                # - Si hay planta en celda: muerde (vida--), maybe la come
                # - Si no: avanza x -= VEL_ZOMBIE
                # - Devuelve: "muerto" | "mordisco" | "comio" | "avanza" | "llego"
            self.ciclos += 1                    # Cada paso = 1 ciclo
            
            if accion == "muerto":              # Zombie murió (vida <= 0)
                est.sumar_stat("zombies_muertos")  # Incrementa stat (lock_stats)
                self.ctx.evento("muere", "juego")  # Evento nivel "juego"
                self.ctx.zombie_muerto()        # Notifica a Juego: ¿victoria?
                return                          # Hilo TERMINA (return desde cuerpo)
            
            if accion == "llego":               # Zombie llegó a casa (x < 0)
                self.ctx.evento("llegó a la casa", "juego")
                self.ctx.finalizar("ZOMBIES")   # FIN DE PARTIDA: ganan zombies
                return                          # Hilo TERMINA
            
            if accion == "mordisco":            # Mordió planta (planta vive)
                self.ctx.evento(f"muerde a {planta.nombre} (vida {planta.vida})", "detalle")
            elif accion == "comio":             # Se comió planta (planta muere)
                est.sumar_stat("plantas_perdidas")  # Stat plantas perdidas
                self.ctx.evento(f"devoró a {planta.nombre}", "juego")
            
            # Tiempo de espera variable: más lento si está mordiendo/comiendo
            espera = config.PASO_MORDISCO if accion in ("mordisco", "comio") else config.PASO_ZOMBIE
            # PASO_MORDISCO = 0.8s (mordiendo lento), PASO_ZOMBIE = 0.2s (caminando)
            if self.dormir(espera):             # Duerme tiempo variable (cooperativo)
                return                          # Fin de partida o cancelación -> termina


# ============================================================ HILOS PERMANENTES
class HiloSpawner(HiloBase):
    """Hilo Spawner: genera oleada de zombies (12 total) con intervalos aleatorios."""
    rol = "generador"

    def __init__(self, ctx, nombre="Spawner"):
        super().__init__(ctx, nombre)

    def cuerpo(self):
        """Genera zombies uno a uno con tiempos crecientemente cortos."""
        if self.dormir(config.ESPERA_PRIMER_ZOMBIE):  # Espera inicial 8s antes del primer zombie
            return
        for n in range(config.TOTAL_ZOMBIES):   # Bucle: 12 zombies (config.TOTAL_ZOMBIES = 12)
            z = self.ctx.estado.agregar_zombie(random.randrange(config.FILAS))
                # Crea zombie en fila aleatoria 0-4 (bajo lock_tablero)
            self.ctx.gestor.lanzar(HiloZombie(self.ctx, z))
                # Lanza hilo para ESTE zombie (hilo dinámico)
            self.ciclos += 1                    # Cada zombie spawnado = 1 ciclo
            
            if n == config.TOTAL_ZOMBIES - 1:   # Si era el último zombie
                break                           # Sale del bucle -> hilo termina
            
            # Calcula espera hasta el siguiente zombie:
            # - Base aleatoria entre 4.0 y 7.0s
            # - Menos n * 0.25s (acelera progresivamente: oleada se intensifica)
            # - Mínimo 2.5s (ESPERA_MINIMA)
            espera = max(config.ESPERA_MINIMA,
                         random.uniform(*config.ESPERA_ZOMBIE) - n * config.ACELERACION_OLEADA)
            if self.dormir(espera):             # Espera calculada (cooperativo)
                return                          # Fin partida -> termina
        self.ctx.evento("ya salieron todos los zombies: este hilo termina", "hilo")


class HiloCielo(HiloBase):
    """Hilo Cielo: deja caer soles del cielo cada PERIODO_CIELO (9s)."""
    rol = "productor"

    def __init__(self, ctx, nombre="Cielo"):
        super().__init__(ctx, nombre)

    def cuerpo(self):
        """Bucle infinito: cada 9s suma 25 soles."""
        while not self.dormir(config.PERIODO_CIELO):  # Duerme 9s (cooperativo)
            total = self.ctx.estado.sumar_soles(config.SOLES_CIELO)  # +25 soles (lock_soles + stats)
            self.ciclos += 1
            self.ctx.evento(f"cae un sol del cielo (+{config.SOLES_CIELO}, total {total})", "detalle")


class HiloMonitor(HiloBase):
    """Hilo Monitor: limpia hilos terminados y muestrea hilos vivos para gráfica."""
    rol = "monitor"

    def __init__(self, ctx, nombre="Monitor"):
        super().__init__(ctx, nombre)

    def cuerpo(self):
        """Cada 0.5s: limpia lista de hilos + registra muestra para gráfica."""
        t0 = time.monotonic()                   # Tiempo de inicio de partida (para timestamps relativos)
        while not self.dormir(config.PERIODO_MONITOR):  # Duerme 0.5s (cooperativo)
            self.ctx.gestor.limpiar()           # Borra hilos terminados hace > 3s (GRACIA_HILO_TERMINADO)
            self.ctx.gestor.muestrear(time.monotonic() - t0)
                # Registra (tiempo_relativo, hilos_vivos) en historial para gráfica
            self.ciclos += 1                    # Cada muestra = 1 ciclo


class HiloAutoJugador(HiloBase):
    """
    Hilo AutoJugador: "IA" que juega sola. Se puede APAGAR EN CALIENTE:
    otro hilo (MainThread via vista) hace self.parar.set().
    """
    rol = "ia"

    def __init__(self, ctx, nombre):
        super().__init__(ctx, nombre)

    def cuerpo(self):
        """Bucle: cada 1.5s toma snapshot, decide y siembra."""
        est = self.ctx.estado
        self.ctx.evento("empieza a jugar", "hilo")
        while not self.dormir(config.PERIODO_IA):  # Duerme 1.5s (cooperativo)
            snap = est.snapshot()               # COPIA del estado (snapshot() usa lock_tablero breve)
                                                # Decide SIN retener ningún candado -> no bloquea a otros hilos
            decision = self._decidir(snap)      # Lógica pura de decisión (ver método abajo)
            if decision:                        # Si hay decisión válida (tipo, fila, col)
                self.ctx.sembrar(*decision)     # Llama a Juego.sembrar() -> lanza hilos de plantas
                self.ciclos += 1                # Cada siembra = 1 ciclo
        self.ctx.evento("deja de jugar", "hilo")

    @staticmethod
    def _decidir(snap):
        """
        Lógica de decisión del auto-jugador. Recibe SNAPSHOT (copia inmutable).
        NO usa candados: opera sobre datos ya copiados.
        
        Returns:
            tuple | None: ("tipo", fila, col) o None si no hace nada
        """
        soles = snap["soles"]                   # Soles disponibles (int)
        ocupadas = {(f, c) for (_, _, f, c, _, _) in snap["plantas"]}
            # Set de tuplas (fila, col) ocupadas por plantas
        tipos = [t for (_, t, _, _, _, _) in snap["plantas"]]
            # Lista de tipos de plantas existentes
        filas_zombie = {f for (_, f, _, _, _) in snap["zombies"]}
            # Set de filas que tienen zombies vivos
        lanza = Counter(f for (_, t, f, _, _, _) in snap["plantas"] if t == "lanzaguisantes")
            # Counter: fila -> cuántos lanzaguisantes hay en esa fila
        
        # Ordena filas por prioridad:
        # 1. Menos lanzaguisantes (menos defensa)
        # 2. Filas SIN zombies al final (filas con zombies primero)
        # 3. random.random() para desempate aleatorio
        filas = sorted(range(config.FILAS),
                       key=lambda f: (lanza[f], f not in filas_zombie, random.random()))
        
        sin_defensa = [f for f in filas if f in filas_zombie and lanza[f] == 0]
            # Filas que TIENEN zombie Y NO TIENEN lanzaguisantes (¡urgente!)
        
        giras = tipos.count("girasol")          # Total girasoles en tablero
        costo_l, costo_g = config.COSTO["lanzaguisantes"], config.COSTO["girasol"]
            # costos: lanzaguisantes=100, girasol=50
        
        # PRIORIDADES DE DECISIÓN (en orden):
        
        # 1) ECONOMÍA MÍNIMA: si no hay girasoles y alcanza, planta girasol
        if giras == 0 and soles >= costo_g:
            return HiloAutoJugador._girasol(filas, ocupadas)
        
        # 2) APAGAR INCENDIOS: fila con zombie y sin defensa -> lanzaguisantes
        if sin_defensa:
            if soles >= costo_l:
                return HiloAutoJugador._lanzaguisantes(sin_defensa, ocupadas)
            return None                         # No alcanza para lanzaguisantes -> espera
        
        # 3) MÁS ECONOMÍA: si menos de 3 girasoles y alcanza -> girasol
        if giras < 3 and soles >= costo_g:
            return HiloAutoJugador._girasol(filas, ocupadas)
        
        # 4) DEFENSA PREVENTIVA: si alcanza lanzaguisantes -> planta en fila prioritaria
        if soles >= costo_l:
            return HiloAutoJugador._lanzaguisantes(filas, ocupadas)
        
        # 5) NUECES: si tiene 2+ lanzaguisantes, <4 nueces, y alcanza (50 soles)
        if (soles >= config.COSTO["nuez"] and tipos.count("lanzaguisantes") >= 2
                and tipos.count("nuez") < 4):
            for f in filas:
                if f in filas_zombie and lanza[f] and (f, 5) not in ocupadas:
                    # Fila con zombie + tiene lanzaguisantes + columna 5 libre
                    return "nuez", f, 5         # Planta nuez delante del lanzaguisantes (col 5)
        
        return None                             # No hace nada este turno

    @staticmethod
    def _lanzaguisantes(filas, ocupadas):
        """Busca celda libre para lanzaguisantes en columnas preferidas (3,2,4,1)."""
        for f in filas:                         # Filas ordenadas por prioridad
            for c in (3, 2, 4, 1):              # Columnas preferidas (centro -> lados)
                if (f, c) not in ocupadas:      # ¿Celda libre?
                    return "lanzaguisantes", f, c
        return None                             # No hay hueco

    @staticmethod
    def _girasol(filas, ocupadas):
        """Busca celda libre para girasol en columnas 0,1 (izquierda)."""
        for f in filas:
            for c in (0, 1):                    # Columnas 0 y 1 (cerca de casa)
                if (f, c) not in ocupadas:
                    return "girasol", f, c
        return None