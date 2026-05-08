from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Role(Enum):
    MAFIA = "mafia"
    CIVILIAN = "civilian"
    DOCTOR = "doctor"
    COMMISSAR = "commissar"


class Phase(Enum):
    WAITING = "waiting"
    NIGHT = "night"
    DAY = "day"
    VOTING = "voting"
    END = "end"


@dataclass
class Player:
    id: int
    role: Role
    is_alive: bool = True


@dataclass
class GameState:
    game_id: int
    phase: Phase
    players: list[Player]
    votes: dict[int, int]  # кто -> кого
    actions: dict[int, int]  # НОЧНАЯ СУЕТА



class GameEngine:
    def __init__(self, state: GameState):
        self.state = state


    def next_phase(self):
        f'''
            Метод, который отвечает за переход на следующий этап игры
        '''

        if self.state.phase == Phase.WAITING:
            self.state.phase = Phase.NIGHT

        elif self.state.phase == Phase.NIGHT:
            self.resolve_night()
            self.state.phase = Phase.DAY

        elif self.state.phase == Phase.DAY:
            self.state.phase = Phase.VOTING

        elif self.state.phase == Phase.VOTING:
            self.resolve_voting()
            self.state.phase = Phase.NIGHT


    def night_action(self, player_id: int, target_id: int):
        f'''
        Метод, который отвечает за деятельность игроков ночью
        '''
        player = self._get_player(player_id)

        if not player.is_alive:
            return

        self.state.actions[player_id] = target_id


    def resolve_night(self):
        mafia_target = None
        docker_target = None

        for player_id, target_id in self.state.action.items():
            player = self._get_player(player_id)

            if player.role == Role.MAFIA:
                mafia_target = target_id

            elif player.role == Role.DOCTOR:
                docker_target = target_id

        if mafia_target and mafia_target != docker_target:
            self._kill(mafia_target)

        self.state.actions.clear()

    

    def vote(self, voter_id: int, target_id: int):
        voter = self._get_player(voter_id )

        
        if not voter.is_alive:
            return
        
        self.state.votes[voter_id] = target_id


    






