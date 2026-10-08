"""
Ejecuta el MISMO juego sin interfaz gráfica (capas de modelo, hilos y lógica).
Sirve para ver el log de hilos en la terminal o si tu equipo no tiene tkinter.
Prueba de que la vista es intercambiable: las capas de abajo no la conocen.

Uso:  python modo_consola.py [segundos] [detalle]
      python modo_consola.py 90 detalle     # incluye golpes, mordiscos y soles
"""
import queue
import sys
import time

from pvz.logica.juego import Juego


def vaciar(juego, ver_detalle):
    try:
        while True:
            ev = juego.eventos.get_nowait()
            if ev[1] != "detalle" or ver_detalle:
                print(juego.formatear(ev))
    except queue.Empty:
        pass


def main():
    duracion = float(sys.argv[1]) if len(sys.argv) > 1 else 90
    detalle = "detalle" in sys.argv[2:]
    juego = Juego()
    juego.iniciar(autojugador=True)
    limite = time.monotonic() + duracion
    ultimo = 0.0
    while time.monotonic() < limite and not juego.detener.is_set():
        vaciar(juego, detalle)
        if time.monotonic() - ultimo >= 5:
            ultimo = time.monotonic()
            r = juego.gestor.resumen()
            print(f"    ... hilos vivos={r['vivos']} (máx {r['max_vivos']}) "
                  f"soles={juego.estado.soles_actuales()} zombies={juego.estado.zombies_vivos()}")
        time.sleep(0.1)
    juego.detener_todo()
    time.sleep(0.2)
    vaciar(juego, detalle)

    r = juego.resumen()
    print("\n=========== RESUMEN ===========")
    print("Resultado:", r["resultado"])
    for k, v in r["juego"].items():
        print(f"  {k}: {v}")
    h = r["hilos"]
    print(f"  hilos creados: {h['creados']} | máximo simultáneo: {h['max_vivos']} | por rol: {h['por_rol']}")
    print("  threading.active_count() al final (incluye MainThread):", r["active_count"])


if __name__ == "__main__":
    main()
