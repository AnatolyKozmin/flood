
class Phase(Enum):
    WAITING = "waiting"
    NIGHT = "night"
    DAY = "day"
    VOTING = "voting"
    END = "end"

class GameEngine:
    def __init__(self, state: GameState):
        self.state = state

    # --------------------
    # PHASE MANAGEMENT
    # --------------------
    def next_phase(self):
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

    # --------------------
    # NIGHT LOGIC
    # --------------------
    def night_action(self, player_id: int, target_id: int):
        player = self._get_player(player_id)

        if not player.is_alive:
            return

        self.state.actions[player_id] = target_id

    def resolve_night(self):
        mafia_target = None
        doctor_target = None

        for player_id, target_id in self.state.actions.items():
            player = self._get_player(player_id)

            if player.role == Role.MAFIA:
                mafia_target = target_id

            elif player.role == Role.DOCTOR:
                doctor_target = target_id

        if mafia_target and mafia_target != doctor_target:
            self._kill(mafia_target)

        self.state.actions.clear()

    # --------------------
    # VOTING
    # --------------------
    def vote(self, voter_id: int, target_id: int):
        voter = self._get_player(voter_id)

        if not voter.is_alive:
            return

        self.state.votes[voter_id] = target_id

    def resolve_voting(self):
        from collections import Counter

        counter = Counter(self.state.votes.values())

        if not counter:
            return

        target_id, _ = counter.most_common(1)[0]
        self._kill(target_id)

        self.state.votes.clear()

    # --------------------
    # GAME RULES
    # --------------------
    def check_winner(self) -> Optional[str]:
        mafia = [p for p in self.state.players if p.role == Role.MAFIA and p.is_alive]
        civilians = [p for p in self.state.players if p.role != Role.MAFIA and p.is_alive]

        if not mafia:
            return "civilians"

        if len(mafia) >= len(civilians):
            return "mafia"

        return None

    # --------------------
    # HELPERS
    # --------------------
    def _get_player(self, player_id: int) -> Player:
        return next(p for p in self.state.players if p.id == player_id)

    def _kill(self, player_id: int):
        player = self._get_player(player_id)
        player.is_alive = False