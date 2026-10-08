"""
Plantas vs Zombies - análisis de hilos (interfaz gráfica).
Uso:  python main.py

Si falta tkinter (en Ubuntu / WSL):  sudo apt install python3-tk
Sin interfaz gráfica puedes usar:    python modo_consola.py
"""
import sys

try:
    import tkinter as tk
except ModuleNotFoundError:
    sys.exit("No se encontró tkinter. En Ubuntu/WSL instálalo con:  sudo apt install python3-tk\n"
             "Mientras tanto puedes correr el juego sin interfaz:  python modo_consola.py")

from pvz.vista.app import App


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
