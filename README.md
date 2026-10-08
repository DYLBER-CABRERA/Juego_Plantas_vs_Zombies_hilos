# Plantas vs Zombies - análisis de construcción y gestión de hilos

Juego básico en Python con interfaz gráfica (tkinter) y arquitectura por capas.
Cada entidad del juego (girasol, lanzaguisantes, guisante, zombie) corre en **su propio hilo**,
y la ventana muestra en vivo qué hace cada hilo.

## Cómo ejecutarlo

### Windows (PowerShell)

```powershell
# 1. OPCIÓN A: Ejecutar desde código fuente (requiere Python instalado)
python main.py            # con interfaz gráfica
python modo_consola.py    # sin interfaz: 90 s, imprime el log de hilos
python modo_consola.py 120 detalle   # con golpes, mordiscos y soles

# 2. OPCIÓN B: Ejecutar el .exe compilado (NO requiere Python instalado)
# Compilarlo una vez:
pip install pyinstaller
pyinstaller --onefile --windowed --name "PlantasVsZombies" main.py

# El .exe queda en:  dist\PlantasVsZombies.exe
# Para jugar: doble clic en el .exe  O  desde PowerShell:
.\dist\PlantasVsZombies.exe
```

> **Nota:** El proyecto usa solo biblioteca estándar (`tkinter`, `threading`, `queue`, etc.). En Windows, `tkinter` viene incluido con Python oficial (python.org).

### Ubuntu / WSL

```bash
# Si falta tkinter
sudo apt install python3-tk

python3 main.py            # con interfaz gráfica
python3 modo_consola.py    # sin interfaz: 90 s, imprime el log de hilos
python3 modo_consola.py 120 detalle   # con golpes, mordiscos y soles
```

En WSL la ventana necesita WSLg (Windows 11).

## Cómo se juega

1. Elige una planta arriba (Girasol 50, Lanzaguisantes 100, Nuez 50) y haz clic en una celda del césped.
2. Los girasoles producen soles; los lanzaguisantes disparan si hay un zombie en su fila; la nuez bloquea.
3. **Pala**: quita una planta (su hilo termina). **Pausa**: congela todos los hilos.
4. **Auto-jugador**: un hilo de "IA" que siembra solo. Se puede apagar y encender en caliente.
5. Ganan las plantas si mueren los 12 zombies; ganan los zombies si uno llega a la casa.

## Efecto del sol

Cada vez que un girasol produce soles, junto a la flor aparece un sol con rayos que gira, sube
flotando con un pequeño balanceo, muestra "+25" y se encoge hasta desaparecer (los soles se suman
al contador de forma automática, no hay que hacer clic). En pausa la animación se congela.
Cómo está repartido entre capas:

- `HiloGirasol` llama a `EstadoJuego.producir_sol(planta)`: suma los soles (con candado) y deja un aviso
  `(fila, col, soles)` en la bandeja `estado.efectos` (un `deque` acotado, sin candado: `append`/`popleft` son atómicos).
- La vista (`app.py`) vacía esa bandeja en cada refresco de 50 ms y anima cada sol (`_avanzar_soles`, `_dibujar_soles`, `_sol`).
  Los hilos no dibujan nada: solo avisan.
- Ajustes en `config.py`: `SOL_DURACION` (segundos que dura) y `SOL_ALTURA` (píxeles que sube).

## Arquitectura por capas

Cada capa solo conoce a la de abajo. La vista es intercambiable: `modo_consola.py` usa las mismas capas sin GUI.

```
main.py / modo_consola.py          arranque
pvz/vista/app.py                   CAPA 4  Vista (tkinter): tablero, tabla de hilos, gráfica, log
pvz/logica/juego.py                CAPA 3  Lógica: reglas, pausa, fin de partida, cola de eventos
pvz/hilos/base.py                  CAPA 2  HiloBase: parada cooperativa, pausa, métricas por hilo
pvz/hilos/gestor.py                CAPA 2  GestorHilos: lanzar, contar, listar, detener (join)
pvz/hilos/trabajadores.py          CAPA 2  Los hilos concretos (zombie, girasol, guisante, ...)
pvz/modelo/estado.py               CAPA 1  EstadoJuego: datos compartidos + candados
pvz/modelo/entidades.py            CAPA 1  Planta, Zombie, Guisante (datos puros)
pvz/config.py                      CAPA 0  Constantes (velocidades, costos, cantidad de zombies)
```

## Los hilos del juego

| Hilo | Cantidad | Vida | Qué hace |
|---|---|---|---|
| Monitor | 1 | toda la partida | limpia hilos terminados y muestrea "hilos vivos" para la gráfica |
| Cielo | 1 | toda la partida | deja caer 25 soles cada 9 s |
| Spawner | 1 | hasta soltar el último zombie | crea un `HiloZombie` cada 2.5-7 s |
| AutoJugador | 0 o 1 | se activa/apaga | decide qué sembrar usando un snapshot del estado |
| Girasol-N | 1 por girasol | hasta que lo devoran o lo quitan | +25 soles cada 6 s |
| Lanzaguisantes-N | 1 por planta | ídem | cada 1.2 s dispara si hay zombie delante |
| Guisante-N | 1 por disparo | ~2 s | avanza, golpea y muere (**un hilo creado por otro hilo**) |
| Zombie-N | 1 por zombie | hasta morir o llegar | camina, muerde plantas |

La Nuez **no** tiene hilo: es pasiva (solo recibe daño). No todo necesita un hilo.

## Sincronización (lo que conviene analizar)

| Mecanismo | Dónde | Para qué |
|---|---|---|
| `RLock` del tablero | `EstadoJuego.lock_tablero` | protege plantas, zombies, guisantes y sus vidas/posiciones. Es reentrante porque `plantar()` toma el de soles estando dentro |
| `Lock` de soles | `lock_soles` | el contador lo tocan girasoles, cielo, jugador y clics de la GUI |
| `Lock` de estadísticas | `lock_stats` | contadores del resumen |
| Orden de candados | `tablero -> soles -> stats` | siempre en ese orden: evita interbloqueo (deadlock) |
| `Event` `detener` | `Juego.detener` | fin de partida: todos los hilos salen de sus esperas al instante |
| `Event` `corriendo` | `Juego.corriendo` | pausa: apagado = los hilos se quedan en estado `pausado` |
| `Event` `parar` por hilo | `HiloBase.parar` | cancelar un solo hilo (apagar la IA) |
| `queue.Queue` | `Juego.eventos` | productor-consumidor: los hilos ponen mensajes, la GUI los saca |
| `snapshot()` | `EstadoJuego` | copia del estado para dibujar sin retener ningún candado |
| `daemon=True` + `join()` | `HiloBase` / `GestorHilos` | los hilos no bloquean el cierre y se cierran de forma ordenada |

**Regla de oro de la GUI:** tkinter solo se toca desde el hilo principal. Ningún hilo del juego dibuja:
la vista consulta un snapshot y vacía la cola de eventos cada 50 ms (`root.after`).

## Qué observar en la ventana

- **Tabla "Hilos de la partida"**: estado de cada hilo en vivo (`durmiendo`, `esperando lock`, `sección crítica`,
  `pausado`, `terminado`), ciclos ejecutados, segundos vivo y milisegundos acumulados esperando el candado del tablero.
- **Gráfica de hilos vivos**: sube con cada zombie, planta y disparo, y baja cuando mueren.
- **Registro**: cada línea indica qué hilo la emitió. Activa "Ver detalle" para ver golpes y mordiscos.
- **Resumen final** (en el registro al terminar): hilos creados, máximo simultáneo y por rol.
- **Pausa**: todos los hilos pasan a `pausado` y el contador de ciclos se detiene.
- **Pala**: la planta desaparece y su hilo pasa a `terminado` (cancelación cooperativa).
- `threading.active_count()` incluye el MainThread (la GUI); al cerrar la ventana debe quedar en 1.

## Experimentos sugeridos para el informe

1. Sube `TOTAL_ZOMBIES` y baja `PERIODO_DISPARO` en `config.py`: ¿cuántos hilos simultáneos aparecen?
2. Apaga el auto-jugador y siembra muchos lanzaguisantes a mano: mira la columna "Lock ms" (contención del candado del tablero).
3. Apaga y enciende el auto-jugador: se crea y se cancela un hilo en caliente.
4. En `base.py` cambia `dormir()` por `time.sleep()` y observa que al terminar la partida los hilos tardan en cerrar
   (por eso se usa `Event.wait`).
5. Quita el `with lock_tablero` de `avanzar_guisante` y busca condiciones de carrera
   (un guisante y un zombie modificando la vida a la vez).

## Estructura de archivos

```
plantas_vs_zombies/
  main.py
  modo_consola.py
  README.md
  pvz/
    config.py
    modelo/    entidades.py  estado.py
    hilos/     base.py  gestor.py  trabajadores.py
    logica/    juego.py
    vista/     app.py
```
