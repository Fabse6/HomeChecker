import sqlite3


class PlayersDatabase:

    def __init__(self, db_connection="players.db"):
        self.connection = db_connection
        self.create_table()

    def create_table(self):
        with sqlite3.connect(self.connection) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    average REAL DEFAULT 0.0,
                    darts_thrown INTEGER DEFAULT 0,
                    Score INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def add_player(self, name, average=0.0, darts_thrown=0, Score=0):
        with sqlite3.connect(self.connection) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO players (name, average, darts_thrown, Score)
                VALUES (?, ?, ?, ?)
            """,
                (name, average, darts_thrown, Score),
            )
            conn.commit()

    def get_all_players(self):
        with sqlite3.connect(self.connection) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, average, darts_thrown, Score FROM players"
            )
            return cursor.fetchall()

    def update_player(self, name=None, darts_thrown=None, Score=None):
        if name is None:
            return
        with sqlite3.connect(self.connection) as conn:
            cursor = conn.cursor()
            if darts_thrown is not None and Score is not None:
                average = (
                    round((Score / darts_thrown) * 3, 2)
                    if darts_thrown > 0
                    else 0.0
                )
                cursor.execute(
                    "UPDATE players SET darts_thrown = ?, Score = ?, average = ? WHERE name = ?",
                    (darts_thrown, Score, average, name),
                )
            conn.commit()


class dartgame:

    def __init__(self):
        self.player_db = PlayersDatabase("players.db")
        self.players = self.player_db.get_all_players()
        self.active_players = []
        self.current_player_index = 0
        self.legs_to_win = 1
        self.mode = "501"
        self.history = []  # <--- NEU: Speichert den Verlauf aller Spielzustände

    def refresh_players(self):
        self.players = self.player_db.get_all_players()
        return self.players

    def add_player(self, name):
        self.player_db.add_player(name)
        return self.refresh_players()

    def start_game(self, selected_names, legs_to_win=1, mode="501"):
        self.legs_to_win = int(legs_to_win)
        self.mode = mode
        self.active_players = []
        self.history = []  # History zurücksetzen
        all_players_dict = {p[1]: p for p in self.player_db.get_all_players()}

        for name in selected_names:
            if name in all_players_dict:
                p = all_players_dict[name]
                if mode == "501":
                    self.active_players.append({
                        "name": name,
                        "score": 501,
                        "legs": 0,
                        "darts": p[3],
                        "total_score": p[4],
                    })
                else:
                    self.active_players.append({
                        "name": name,
                        "points": 0,
                        "legs": 0,
                        "darts": p[3],
                        "total_score": p[4],
                        "cricket": {
                            "20": 0,
                            "19": 0,
                            "18": 0,
                            "17": 0,
                            "16": 0,
                            "15": 0,
                            "bull": 0,
                        },
                    })

        self.current_player_index = 0
        return self.get_game_state()

    def get_game_state(self):
        if not self.active_players:
            return {}
        current_p = self.active_players[self.current_player_index]
        state = {
            "active_players": self.active_players,
            "next_player": current_p["name"],
            "winner": None,
            "mode": self.mode,
        }
        if self.mode == "cricket":
            state["cricket_targets"] = ["20", "19", "18", "17", "16", "15", "bull"]
        return state

    def update_score(self, score_thrown_or_data):
        if not self.active_players:
            return {}

        import copy
        snapshot = {
            "active_players": copy.deepcopy(self.active_players),
            "current_player_index": self.current_player_index
        }
        self.history.append(snapshot)

        if self.mode == "501":
            return self._update_501(score_thrown_or_data)
        return self._update_cricket(score_thrown_or_data)

    def _update_501(self, score_thrown):
        current_p = self.active_players[self.current_player_index]
        new_score = current_p["score"] - int(score_thrown)

        if new_score < 0 or new_score == 1:
            current_p["darts"] += 3
        elif new_score == 0:
            current_p["legs"] += 1
            current_p["darts"] += 3
            current_p["total_score"] += int(score_thrown)

            if current_p["legs"] >= self.legs_to_win:
                for p in self.active_players:
                    self.player_db.update_player(
                        p["name"], p["darts"], p["total_score"]
                    )
                return {
                    "active_players": self.active_players,
                    "next_player": current_p["name"],
                    "winner": current_p["name"],
                    "mode": self.mode,
                }

            for p in self.active_players:
                p["score"] = 501
        else:
            current_p["score"] = new_score
            current_p["darts"] += 3
            current_p["total_score"] += int(score_thrown)

        self.current_player_index = (self.current_player_index + 1) % len(self.active_players)
        return self.get_game_state()

    def _update_cricket(self, data):
        if not isinstance(data, dict):
            return self.get_game_state()

        shots = data.get("shots")
        if not shots:
            return self.get_game_state()

        for shot in shots:
            target = shot.get("target")
            multiplier = int(shot.get("multiplier", 1))
            if target not in {"20", "19", "18", "17", "16", "15", "bull"}:
                continue
            self._apply_cricket_hit(target, multiplier)

        current_p = self.active_players[self.current_player_index]
        if self._has_cricket_winner(current_p):
            for p in self.active_players:
                self.player_db.update_player(
                    p["name"], p["darts"], p["total_score"]
                )
            return {
                "active_players": self.active_players,
                "next_player": current_p["name"],
                "winner": current_p["name"],
                "mode": self.mode,
            }

        self.current_player_index = (self.current_player_index + 1) % len(self.active_players)
        return self.get_game_state()

    def _apply_cricket_hit(self, target, multiplier):
        current_p = self.active_players[self.current_player_index]
        target_value = 25 if target == "bull" else int(target)

        opponents = [p for idx, p in enumerate(self.active_players) if idx != self.current_player_index]
        opponent_closed = all(p["cricket"][target] >= 3 for p in opponents)
        target_closed_globally = all(p["cricket"][target] >= 3 for p in self.active_players)

        current_hits = current_p["cricket"][target]
        if current_hits < 3:
            new_hits = min(3, current_hits + multiplier)
            overflow = max(0, current_hits + multiplier - 3)
            current_p["cricket"][target] = new_hits
            if overflow and not opponent_closed and not target_closed_globally:
                current_p["points"] += overflow * target_value
        else:
            if not opponent_closed and not target_closed_globally:
                current_p["points"] += multiplier * target_value

    def _has_cricket_winner(self, player):
        if not all(value >= 3 for value in player["cricket"].values()):
            return False

        opponents = [p for p in self.active_players if p["name"] != player["name"]]
        if not opponents:
            return True

        opponent_points = max(p["points"] for p in opponents)
        return player["points"] > opponent_points

    def go_back(self):
        """Macht den letzten Wurf rückgängig und stellt den alten Zustand wieder her."""
        if not self.history:
            # Keine Würfe zum Rückgängigmachen vorhanden
            return self.get_game_state()

        # Letzten Zustand aus der History holen und anwenden
        last_state = self.history.pop()
        self.active_players = last_state["active_players"]
        self.current_player_index = last_state["current_player_index"]

        return self.get_game_state()