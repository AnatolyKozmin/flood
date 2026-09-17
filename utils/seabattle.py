"""Морской бой 10 на 10: чистая логика без телеграма.

Классика: флот 1×4, 2×3, 3×2, 4×1, корабли не касаются даже углами.
Клетка — int 0..99 (row * 10 + col). Телеграм-обвязка —
handlers/command_seabattle.py.
"""
import random
from dataclasses import dataclass, field

N = 10
FLEET = (4, 3, 3, 2, 2, 2, 1, 1, 1, 1)
LETTERS = "АБВГДЕЖЗИК"


def row_of(cell: int) -> int:
    return cell // N


def col_of(cell: int) -> int:
    return cell % N


def cell_label(cell: int) -> str:
    """А1..К10."""
    return f"{LETTERS[col_of(cell)]}{row_of(cell) + 1}"


def neighbours(cell: int) -> list[int]:
    """Ортогональные соседи внутри поля."""
    r, c = row_of(cell), col_of(cell)
    out = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < N and 0 <= nc < N:
            out.append(nr * N + nc)
    return out


def halo(cells: set[int]) -> set[int]:
    """Клетки ореола: сами клетки + все 8 соседей (для запрета касаний)."""
    out = set(cells)
    for cell in cells:
        r, c = row_of(cell), col_of(cell)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = r + dr, c + dc
                if 0 <= nr < N and 0 <= nc < N:
                    out.add(nr * N + nc)
    return out


@dataclass
class Board:
    ships: list[set[int]] = field(default_factory=list)
    shots: dict[int, str] = field(default_factory=dict)  # cell -> miss/hit
    sunk: set[int] = field(default_factory=set)


def occupied(board: Board) -> set[int]:
    out: set[int] = set()
    for ship in board.ships:
        out |= ship
    return out


def blocked(board: Board) -> set[int]:
    """Куда нельзя ставить: корабли и их ореолы."""
    out: set[int] = set()
    for ship in board.ships:
        out |= halo(ship)
    return out


def ship_cells(start: int, size: int, vertical: bool) -> set[int] | None:
    """Клетки корабля от носа. None — вылезает за поле."""
    r, c = row_of(start), col_of(start)
    if vertical:
        if r + size > N:
            return None
        return {start + i * N for i in range(size)}
    if c + size > N:
        return None
    return {start + i for i in range(size)}


def can_place(board: Board, cells: set[int]) -> bool:
    return bool(cells) and not (cells & blocked(board))


def place(board: Board, cells: set[int]) -> None:
    assert can_place(board, cells)
    board.ships.append(set(cells))


def valid_starts(board: Board, size: int) -> list[int]:
    """Откуда можно начать корабль длины size (хоть как-то)."""
    out = []
    for start in range(N * N):
        for vertical in (False, True):
            cells = ship_cells(start, size, vertical)
            if cells is not None and can_place(board, cells):
                out.append(start)
                break
    return out


def valid_ends(board: Board, start: int, size: int) -> list[int]:
    """Концы корабля от выбранного носа (для однопалубного — сам нос)."""
    out = []
    for vertical in (False, True):
        cells = ship_cells(start, size, vertical)
        # max в row-major — крайняя клетка: правая или нижняя.
        if cells is not None and can_place(board, cells):
            out.append(max(cells))
    return list(dict.fromkeys(out))


def random_fleet(rng: random.Random) -> Board:
    board = Board()
    for size in FLEET:
        options = []
        for start in range(N * N):
            for vertical in (False, True):
                cells = ship_cells(start, size, vertical)
                if cells is not None and can_place(board, cells):
                    options.append(cells)
        assert options, "нет места под флот — баг генератора"
        place(board, set(rng.choice(options)))
    return board


def shoot(board: Board, cell: int) -> tuple[str, set[int]]:
    """Выстрел: ('miss'|'hit'|'sunk', клетки потопленного). Повторы — баг вызывающего."""
    assert cell not in board.shots, "повторный выстрел"
    for ship in board.ships:
        if cell in ship:
            board.shots[cell] = "hit"
            if all(c in board.shots for c in ship):
                board.sunk |= ship
                for c in ship:
                    board.shots[c] = "hit"
                return "sunk", set(ship)
            return "hit", set()
    board.shots[cell] = "miss"
    return "miss", set()


def all_sunk(board: Board) -> bool:
    return bool(board.ships) and all(
        all(c in board.shots for c in ship) for ship in board.ships)


class Hunter:
    """ИИ стрельбы: шахматный поиск + добивка соседей раненого."""

    def __init__(self) -> None:
        self.shots: set[int] = set()
        self.targets: list[int] = []

    def _push_neighbours(self, cell: int) -> None:
        for nb in neighbours(cell):
            if nb not in self.shots and nb not in self.targets:
                self.targets.append(nb)

    def next_shot(self) -> int:
        while self.targets:
            cand = self.targets.pop()
            if cand not in self.shots:
                self.shots.add(cand)
                return cand
        fresh = [c for c in range(N * N) if c not in self.shots]
        parity = [c for c in fresh if (row_of(c) + col_of(c)) % 2 == 0]
        pool = parity or fresh
        pick = random.choice(pool)
        self.shots.add(pick)
        return pick

    def report(self, cell: int, result: str, sunk_cells: set[int]) -> None:
        if result == "hit":
            self._push_neighbours(cell)
        elif result == "sunk":
            # Клетки потопленного и ореол — заведомо не цели.
            dead = set(sunk_cells) | halo(set(sunk_cells))
            self.targets = [t for t in self.targets if t not in dead]
