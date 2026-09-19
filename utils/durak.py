"""Подкидной дурак один на один: чистая логика без телеграма.

Правила: колода 36, раздача по 6, козырь — масть нижней карты прикупа.
Первым ходит владелец младшего козыря. Нападать можно любым количеством
карт рангов со стола, но не больше 6 и не больше карт защитника на начало
захода. Защитник бьёт старшей в масть или козырем (некозырь), либо берёт всё.
После захода: взявшему — тот же нападающий ходит снова, отбившемуся —
атака переходит. Добор из прикупа до 6: сначала нападавший, потом защитник.
Проигрывает тот, кто остался с картами при пустом прикупе; оба пустые — ничья.

Игрок 0 — человек, игрок 1 — бот. Телеграм-обвязка и пошаговый бой —
в handlers/command_durak.py, здесь только механика и эвристики ai_*.
"""
import random
from dataclasses import dataclass, field

RANKS = (6, 7, 8, 9, 10, 11, 12, 13, 14)
SUITS = (0, 1, 2, 3)
SUIT_EMOJI = ("♠️", "♥️", "♦️", "♣️")
RANK_LABEL = {
    2: "2", 3: "3", 4: "4", 5: "5",
    6: "6", 7: "7", 8: "8", 9: "9", 10: "10",
    11: "В", 12: "Д", 13: "К", 14: "А",
}
# Выбор колоды перед партией: id карты — suit*13 + (rank-2), единое
# пространство 0..51, колода — подмножество по рангам.
DECK_RANKS = {
    24: (9, 10, 11, 12, 13, 14),
    36: (6, 7, 8, 9, 10, 11, 12, 13, 14),
    52: (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14),
}
HAND_SIZE = 6


def suit_of(card: int) -> int:
    return card // 13


def rank_of(card: int) -> int:
    return card % 13 + 2


def card_label(card: int) -> str:
    return f"{RANK_LABEL[rank_of(card)]}{SUIT_EMOJI[suit_of(card)]}"


def full_deck(ranks: tuple[int, ...] = RANKS) -> list[int]:
    return [s * 13 + (r - 2) for s in SUITS for r in ranks]


def beats(att: int, dfn: int, trump: int) -> bool:
    """Бьёт ли dfn карту att: старшая в масть или козырь по некозырю."""
    if suit_of(dfn) == suit_of(att):
        return rank_of(dfn) > rank_of(att)
    return suit_of(dfn) == trump and suit_of(att) != trump


@dataclass
class Game:
    talon: list[int] = field(default_factory=list)
    trump: int = 0
    hands: list[list[int]] = field(default_factory=lambda: [[], []])
    table: list[list] = field(default_factory=list)  # [атака, защита|None]
    attacker: int = 0
    bout_def_count: int = 6  # карт у защитника на начало захода (лимит)
    beaten: int = 0  # побитые карты в отбое (из игры, для инварианта колоды)
    deck_size: int = 36  # 24/36/52 — для шапки доски
    over: bool = False
    winner: int | None = None  # 0/1, None — ничья


def _sort_key(trump: int, card: int) -> tuple[int, int]:
    """Дешёвая карта первой: некозыри по возрастанию, потом козыри."""
    return (0, rank_of(card)) if suit_of(card) != trump else (1, rank_of(card))


def first_attacker(game: Game) -> int:
    """Владелец младшего козыря; козырей ни у кого — человек."""
    best: tuple[int, int] | None = None
    for player in (0, 1):
        for card in game.hands[player]:
            if suit_of(card) == game.trump:
                cand = (rank_of(card), player)
                if best is None or cand < best:
                    best = cand
    return best[1] if best is not None else 0


def new_game(seed: int | None = None, deck_size: int = 36) -> Game:
    size = deck_size if deck_size in DECK_RANKS else 36
    ranks = DECK_RANKS[size]
    rng = random.Random(seed)
    deck = full_deck(ranks)
    rng.shuffle(deck)
    game = Game(
        talon=deck[12:],
        trump=suit_of(deck[-1]),
        hands=[sorted(deck[0:6]), sorted(deck[6:12])],
        table=[],
        attacker=0,
        deck_size=size,
    )
    game.attacker = first_attacker(game)
    game.bout_def_count = len(game.hands[1 - game.attacker])
    return game


def defender_of(game: Game) -> int:
    return 1 - game.attacker


def attack_limit(game: Game) -> int:
    return min(6, game.bout_def_count)


def table_ranks(game: Game) -> set[int]:
    ranks = set()
    for att, dfn in game.table:
        ranks.add(rank_of(att))
        if dfn is not None:
            ranks.add(rank_of(dfn))
    return ranks


def uncovered(game: Game) -> list[int]:
    return [att for att, dfn in game.table if dfn is None]


def legal_attacks(game: Game, player: int) -> list[int]:
    """Чем можно напасть/подкинуть. Пусто — подкидывать нечего/нельзя."""
    if game.over or player != game.attacker:
        return []
    hand = sorted(game.hands[player], key=lambda c: _sort_key(game.trump, c))
    if not game.table:
        return hand
    if len(game.table) >= attack_limit(game):
        return []
    ranks = table_ranks(game)
    return [c for c in hand if rank_of(c) in ranks]


def legal_beaters(game: Game, player: int, att: int) -> list[int]:
    """Чем можно побить конкретную карту. Пусто — только брать."""
    if game.over or player != defender_of(game):
        return []
    return sorted(
        (c for c in game.hands[player] if beats(att, c, game.trump)),
        key=lambda c: _sort_key(game.trump, c),
    )


def apply_attack(game: Game, player: int, card: int) -> None:
    assert player == game.attacker and card in legal_attacks(game, player)
    game.hands[player].remove(card)
    game.table.append([card, None])


def apply_defense(game: Game, player: int, att: int, card: int) -> None:
    assert player == defender_of(game) and card in legal_beaters(game, player, att)
    for row in game.table:
        if row[0] == att and row[1] is None:
            game.hands[player].remove(card)
            row[1] = card
            return
    raise AssertionError("битой карты уже нет на столе")


def all_covered(game: Game) -> bool:
    return bool(game.table) and all(dfn is not None for _, dfn in game.table)


def can_redirect(game: Game, player: int) -> list[int]:
    """Чем можно перевести атаку на нападавшего: тот же ранг, что на столе.
    Только пока ничего не побито — классика переводного."""
    if game.over or player != defender_of(game) or not game.table:
        return []
    if any(dfn is not None for _, dfn in game.table):
        return []
    ranks = table_ranks(game)
    return sorted(
        (c for c in game.hands[player] if rank_of(c) in ranks),
        key=lambda c: _sort_key(game.trump, c),
    )


def apply_redirect(game: Game, player: int, card: int) -> None:
    """Перевод: карта в стол новым нападением, переводящий сам атакует.
    Лимит захода не обновляем — он тянется с начала захода."""
    assert card in can_redirect(game, player)
    game.hands[player].remove(card)
    game.table.append([card, None])
    game.attacker = player


def _draw_up(game: Game, first: int) -> None:
    for player in (first, 1 - first):
        hand = game.hands[player]
        while len(hand) < 6 and game.talon:
            hand.append(game.talon.pop(0))
        hand.sort()


def _finish_bout(game: Game) -> str | None:
    """Итог после добора: 'user' | 'bot' | 'draw' | None (играем дальше)."""
    if game.talon:
        return None
    empty = [not game.hands[p] for p in (0, 1)]
    if empty[0] and empty[1]:
        game.over, game.winner = True, None
        return "draw"
    if empty[0]:
        game.over, game.winner = True, 0
        return "user"
    if empty[1]:
        game.over, game.winner = True, 1
        return "bot"
    return None


def resolve_take(game: Game) -> str | None:
    """Защитник берёт всё: стол ему в руку, атакует тот же."""
    taken: list[int] = []
    for att, dfn in game.table:
        taken.append(att)
        if dfn is not None:
            taken.append(dfn)
    game.table.clear()
    game.hands[defender_of(game)].extend(sorted(taken))
    _draw_up(game, game.attacker)
    game.bout_def_count = len(game.hands[defender_of(game)])
    return _finish_bout(game)


def resolve_done(game: Game) -> str | None:
    """Всё побито: отбой, атака переходит защитнику."""
    assert all_covered(game)
    old_attacker = game.attacker
    game.beaten += sum(2 for _, dfn in game.table if dfn is not None)
    game.table.clear()
    game.attacker = 1 - game.attacker
    _draw_up(game, old_attacker)
    game.bout_def_count = len(game.hands[defender_of(game)])
    return _finish_bout(game)


def card_count(game: Game) -> int:
    """Карты в игре: руки + стол + прикуп + отбой (24/36/52 по колоде)."""
    total = len(game.talon) + game.beaten + sum(len(h) for h in game.hands)
    for att, dfn in game.table:
        total += 1 + (1 if dfn is not None else 0)
    return total


# ── Эвристики бота ──

def ai_min_beater(game: Game, bot: int, att: int) -> int | None:
    opts = legal_beaters(game, bot, att)
    return opts[0] if opts else None


def ai_defense_full(game: Game, bot: int) -> dict[int, int] | None:
    """План на весь стол сразу: {атака: защита}. None — надо брать."""
    open_cards = uncovered(game)

    def cost(att: int) -> tuple[int, int]:
        opts = legal_beaters(game, bot, att)
        if not opts:
            return (2, 99)
        return _sort_key(game.trump, opts[0])

    plan: dict[int, int] = {}
    used: set[int] = set()
    for att in sorted(open_cards, key=cost, reverse=True):
        opts = [c for c in legal_beaters(game, bot, att) if c not in used]
        if not opts:
            return None
        pick = min(opts, key=lambda c: _sort_key(game.trump, c))
        plan[att] = pick
        used.add(pick)
    return plan


def ai_lead(game: Game, bot: int) -> int:
    """Первый ход захода: младшая некозырная, иначе младший козырь."""
    return min(game.hands[bot], key=lambda c: _sort_key(game.trump, c))


def ai_toss(game: Game, bot: int, max_pairs: int = 6) -> int | None:
    """Подкинуть самую дешёвую подходящую. Козыри бережём, пока стол мал."""
    opts = legal_attacks(game, bot)
    if not opts or len(game.table) >= min(attack_limit(game), max_pairs):
        return None
    plain = [c for c in opts if suit_of(c) != game.trump]
    if plain:
        return min(plain, key=lambda c: _sort_key(game.trump, c))
    if len(game.table) >= 2:
        return None
    return min(opts, key=lambda c: _sort_key(game.trump, c))
