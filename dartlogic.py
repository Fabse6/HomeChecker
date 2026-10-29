import random
import json
import sqlite3

BOT_NAME = "Bot"
SAVED_GAME_VERSION = 1


class PlayersDatabase:

    def __init__(self, db_connection="players.db"):
        self.connection = db_connection
        self.conn = sqlite3.connect(self.connection)
        self.create_table()
        self.ensure_bot_player()

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                average REAL DEFAULT 0.0,
                darts_thrown INTEGER DEFAULT 0,
                Score INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_game (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def ensure_bot_player(self):
        self.add_player(BOT_NAME, average=0.0, darts_thrown=0, Score=0)

    def add_player(self, name, average=0.0, darts_thrown=0, Score=0):
        if not name:
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO players (name, average, darts_thrown, Score)
            VALUES (?, ?, ?, ?)
        """,
            (name, average, darts_thrown, Score),
        )
        self.conn.commit()

    def get_all_players(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, name, average, darts_thrown, Score FROM players ORDER BY name"
        )
        return cursor.fetchall()

    def update_player(self, name=None, darts_thrown=None, Score=None):
        if name is None:
            return
        cursor = self.conn.cursor()
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
        self.conn.commit()

    def delete_player(self, name):
        if not name or name == BOT_NAME:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM players WHERE name = ?", (name,))
        self.conn.commit()

    def save_game_state(self, state):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO saved_game (id, state_json, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (json.dumps(state),),
        )
        self.conn.commit()

    def load_game_state(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT state_json FROM saved_game WHERE id = 1")
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def clear_game_state(self):
        self.conn.execute("DELETE FROM saved_game WHERE id = 1")
        self.conn.commit()


class dartgame:

    def __init__(self, player_db_connection="players.db"):
        self.player_db = PlayersDatabase(player_db_connection)
        self.players = self.player_db.get_all_players()
        self.active_players = []
        self.current_player_index = 0
        self.starting_player_index = 0
        self.legs_to_win = 1
        self.mode = "501"
        self.history = []
        self.pending_checkout = None
        self._restore_saved_game()

    def _restore_saved_game(self):
        state = self.player_db.load_game_state()
        if state is None:
            return
        if (
            not isinstance(state, dict)
            or state.get("version") != SAVED_GAME_VERSION
            or not isinstance(state.get("active_players"), list)
            or not state["active_players"]
            or not isinstance(state.get("history"), list)
            or state.get("mode") not in {"501", "cricket"}
            or not isinstance(state.get("legs_to_win"), int)
            or state["legs_to_win"] < 1
        ):
            raise ValueError("Der gespeicherte Spielstand ist ungültig oder nicht unterstützt.")

        current_player_index = state.get("current_player_index")
        starting_player_index = state.get("starting_player_index")
        if (
            not isinstance(current_player_index, int)
            or not 0 <= current_player_index < len(state["active_players"])
            or not isinstance(starting_player_index, int)
            or not 0 <= starting_player_index < len(state["active_players"])
        ):
            raise ValueError("Der gespeicherte Spielstand enthält einen ungültigen Spielerzug.")

        self.active_players = state["active_players"]
        self.current_player_index = current_player_index
        self.starting_player_index = starting_player_index
        self.legs_to_win = state["legs_to_win"]
        self.mode = state["mode"]
        self.history = state["history"]
        self.pending_checkout = state["pending_checkout"]

    def _persist_game_state(self, winner=False):
        if winner or not self.active_players:
            self.player_db.clear_game_state()
            return

        self.player_db.save_game_state({
            "version": SAVED_GAME_VERSION,
            "active_players": self.active_players,
            "current_player_index": self.current_player_index,
            "starting_player_index": self.starting_player_index,
            "legs_to_win": self.legs_to_win,
            "mode": self.mode,
            "history": self.history,
            "pending_checkout": self.pending_checkout,
        })

    def get_saved_game_summary(self):
        if not self.active_players:
            return None
        return {
            "players": [
                {"name": player["name"], "legs": player["legs"]}
                for player in self.active_players
            ],
            "mode": self.mode,
            "legs_to_win": self.legs_to_win,
            "awaiting_checkout_darts": self.pending_checkout is not None,
        }

    def discard_saved_game(self):
        self.active_players = []
        self.current_player_index = 0
        self.starting_player_index = 0
        self.history = []
        self.pending_checkout = None
        self.player_db.clear_game_state()


    def refresh_players(self):
        self.players = self.player_db.get_all_players()
        return self.players

    def add_player(self, name):
        self.player_db.add_player(name)
        return self.refresh_players()

    def delete_player(self, name):
        self.player_db.delete_player(name)
        self.players = self.player_db.get_all_players()
        if name in [p.get("name") for p in self.active_players]:
            self.active_players = [p for p in self.active_players if p.get("name") != name]
        return self.players

    def is_bot_turn(self):
        return (
            bool(self.active_players)
            and self.pending_checkout is None
            and self.active_players[self.current_player_index]["name"] == BOT_NAME
        )

    def _generate_bot_score(self, player):
        remaining = player["score"]
        if remaining <= 0:
            return 0

        if remaining <= 100 and random.random() < 0.2:
            return remaining

        base = random.gauss(50, 18)
        score = int(round(max(0, min(180, base))))
        if score > remaining:
            score = max(0, remaining - random.randint(1, 20))
        if score == 1:
            score = 20
        return score

    def resolve_bot_turn(self):
        if not self.is_bot_turn():
            return self.get_game_state()

        while self.is_bot_turn() and not self.get_game_state().get("winner"):
            bot_player = self.active_players[self.current_player_index]
            score = self._generate_bot_score(bot_player)
            state = self.update_score(score)
            if self.pending_checkout is not None:
                state = self.confirm_checkout(3)
            if state.get("winner"):
                return state
            if not self.is_bot_turn():
                return self.get_game_state()

        return self.get_game_state()

    def start_game(self, selected_names, legs_to_win=1, mode="501"):
        self.legs_to_win = int(legs_to_win)
        self.mode = mode
        self.active_players = []
        self.history = []
        self.pending_checkout = None
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
                        "match_score": 0,
                        "darts_thrown": 0,
                        "average": 0,
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
        self._persist_game_state()
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
            "awaiting_checkout_darts": self.pending_checkout is not None,
        }
        if self.pending_checkout is not None:
            state["checkout_player"] = self.active_players[self.pending_checkout["player_index"]]["name"]
        if self.mode == "cricket":
            state["cricket_targets"] = ["20", "19", "18", "17", "16", "15", "bull"]
        return state

    def update_score(self, score_thrown_or_data):
        if not self.active_players or self.pending_checkout is not None:
            return {}

        import copy
        snapshot = {
            "active_players": copy.deepcopy(self.active_players),
            "current_player_index": self.current_player_index,
            "starting_player_index": self.starting_player_index,
            "pending_checkout": copy.deepcopy(self.pending_checkout),
        }
        self.history.append(snapshot)

        if self.mode == "501":
            state = self._update_501(score_thrown_or_data)
        else:
            state = self._update_cricket(score_thrown_or_data)
        self._persist_game_state(winner=bool(state.get("winner")))
        return state

    def _update_501(self, score_thrown):
        current_p = self.active_players[self.current_player_index]
        new_score = current_p["score"] - int(score_thrown)

        if new_score < 0 or new_score == 1:
            current_p["darts"] += 3
            current_p["darts_thrown"] += 3
            current_p["average"] = current_p["match_score"] / current_p["darts_thrown"] * 3
            self.current_player_index = (self.current_player_index + 1) % len(self.active_players)
        elif new_score == 0:
            current_p["score"] = 0
            self.pending_checkout = {
                "player_index": self.current_player_index,
                "score": int(score_thrown),
            }
            return self.get_game_state()
        else:
            current_p["score"] = new_score
            current_p["darts"] += 3
            current_p["total_score"] += int(score_thrown)
            current_p["match_score"] += int(score_thrown)
            current_p["darts_thrown"] += 3
            current_p["average"] = current_p["match_score"] / current_p["darts_thrown"] * 3
            self.current_player_index = (self.current_player_index + 1) % len(self.active_players)
        return self.get_game_state()

    def confirm_checkout(self, darts_used):
        if self.pending_checkout is None:
            raise ValueError("Es gibt keinen offenen Leg-Abschluss.")
        if isinstance(darts_used, bool) or darts_used not in (1, 2, 3):
            raise ValueError("Die Anzahl der Pfeile muss 1, 2 oder 3 sein.")

        pending = self.pending_checkout
        current_p = self.active_players[pending["player_index"]]
        current_p["legs"] += 1
        current_p["darts"] += darts_used
        current_p["total_score"] += pending["score"]
        current_p["match_score"] += pending["score"]
        current_p["darts_thrown"] += darts_used
        current_p["average"] = (
            current_p["match_score"] / current_p["darts_thrown"] * 3
            if current_p["darts_thrown"] > 0
            else 0
        )
        self.pending_checkout = None

        self.starting_player_index = (self.starting_player_index + 1) % len(self.active_players)
        self.current_player_index = self.starting_player_index
        if current_p["legs"] >= self.legs_to_win:
            self.starting_player_index = 0
            for player in self.active_players:
                self.player_db.update_player(
                    player["name"], player["darts"], player["total_score"]
                )
            state = {
                "active_players": self.active_players,
                "next_player": current_p["name"],
                "winner": current_p["name"],
                "mode": self.mode,
                "awaiting_checkout_darts": False,
            }
            self._persist_game_state(winner=True)
            return state

        for player in self.active_players:
            player["score"] = 501
        state = self.get_game_state()
        self._persist_game_state()
        return state

    def _update_cricket(self, data):
        if not isinstance(data, dict):
            state = self.get_game_state()
            self._persist_game_state()
            return state

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
            return self.get_game_state()

        last_state = self.history.pop()
        self.active_players = last_state["active_players"]
        self.current_player_index = last_state["current_player_index"]
        self.starting_player_index = last_state.get(
            "starting_player_index", self.starting_player_index
        )
        self.pending_checkout = last_state["pending_checkout"]

        state = self.get_game_state()
        self._persist_game_state()
        return state