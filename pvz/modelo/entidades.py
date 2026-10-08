"""
CAPA 1 - MODELO (entidades) - VERSION COMENTADA LINEA POR LINEA
===============================================================
Datos puros del dominio: no tienen hilos ni locks. Quién las modifica y con qué
protección lo decide EstadoJuego (estado.py).

eq=False => dos entidades distintas nunca son "iguales" aunque tengan los mismos
valores; así list.remove() quita exactamente el objeto que se pide.
"""
from dataclasses import dataclass  # Importa decorador para crear clases de datos automáticamente


@dataclass(eq=False)  # eq=False: desactiva __eq__ automático; dos plantas con mismos datos NO son iguales
class Planta:
    """Entidad Planta: representa una planta en el tablero."""
    nombre: str      # También es el nombre de su hilo (ej. "Girasol-2", "Lanzaguisantes-1")
    tipo: str        # Tipo de planta: "girasol" | "lanzaguisantes" | "nuez"
    fila: int        # Fila en el tablero (0-4)
    col: int         # Columna en el tablero (0-8)
    vida: int        # Vida actual (baja cuando zombie muerde; 0 = muerta)
    vida_max: int    # Vida máxima (para barra de vida en vista)


@dataclass(eq=False)  # eq=False: cada zombie es único aunque tenga mismos atributos
class Zombie:
    """Entidad Zombie: representa un zombie en el tablero."""
    nombre: str      # Nombre único del hilo (ej. "Zombie-3")
    fila: int        # Fila donde camina (0-4)
    x: float         # Posición horizontal en celdas (9.0 = entra por derecha, <0 = llegó a casa)
    vida: int        # Vida actual (baja cuando guisante golpea; 0 = muerto)
    vida_max: int    # Vida máxima (para barra de vida)


@dataclass(eq=False)  # eq=False: cada guisante es único
class Guisante:
    """Entidad Guisante: proyectil disparado por lanzaguisantes."""
    nombre: str      # Nombre único (ej. "Guisante-7")
    fila: int        # Fila donde viaja (misma que la planta que lo disparó)
    x: float         # Posición horizontal en celdas (avanza hacia la derecha)