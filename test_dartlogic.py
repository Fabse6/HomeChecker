import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dartlogic import BOT_NAME, PlayersDatabase, dartgame


class TestPlayersDatabase(unittest.TestCase):
    def test_delete_player_removes_player_and_stats(self):
        db = PlayersDatabase(':memory:')

        db.add_player('Alice', average=30.5, darts_thrown=45, Score=300)
        db.add_player('Bob', average=25.0, darts_thrown=20, Score=150)

        db.delete_player('Alice')

        players = db.get_all_players()
        names = [player[1] for player in players]

        self.assertNotIn('Alice', names)
        self.assertIn('Bob', names)

    def test_bot_player_is_present_and_cannot_be_deleted(self):
        db = PlayersDatabase(':memory:')

        players = db.get_all_players()
        names = [player[1] for player in players]

        self.assertIn(BOT_NAME, names)

        db.delete_player(BOT_NAME)
        names_after = [player[1] for player in db.get_all_players()]
        self.assertIn(BOT_NAME, names_after)


class TestBotBehavior(unittest.TestCase):
    def test_bot_uses_finish_chance_when_remaining_below_100(self):
        game = dartgame(':memory:')
        game.player_db.add_player('Alice')
        game.players = game.player_db.get_all_players()
        game.start_game(['Alice', BOT_NAME], legs_to_win=1, mode='501')

        game.active_players[1]['score'] = 60
        with patch('dartlogic.random.random', return_value=0.1):
            score = game._generate_bot_score(game.active_players[1])
        self.assertEqual(score, 60)

    def test_bot_throws_around_50_points_on_regular_turns(self):
        game = dartgame(':memory:')
        game.player_db.add_player('Alice')
        game.players = game.player_db.get_all_players()
        game.start_game(['Alice', BOT_NAME], legs_to_win=1, mode='501')

        game.active_players[1]['score'] = 180
        with patch('dartlogic.random.random', return_value=0.8), patch(
            'dartlogic.random.gauss', return_value=50
        ):
            score = game._generate_bot_score(game.active_players[1])
        self.assertGreaterEqual(score, 20)
        self.assertLessEqual(score, 90)


class TestCheckoutDarts(unittest.TestCase):
    def setUp(self):
        self.game = dartgame(':memory:')
        self.game.player_db.add_player('Alice')
        self.game.start_game(['Alice'], legs_to_win=2, mode='501')
        self.game.active_players[0]['score'] = 40

    def test_checkout_waits_for_darts_used_and_counts_selected_amount(self):
        pending_state = self.game.update_score(40)

        self.assertTrue(pending_state['awaiting_checkout_darts'])
        self.assertEqual(self.game.active_players[0]['legs'], 0)
        self.assertEqual(self.game.active_players[0]['darts'], 0)
        self.assertEqual(self.game.active_players[0]['darts_thrown'], 0)

        leg_state = self.game.confirm_checkout(2)

        player = self.game.active_players[0]
        self.assertFalse(leg_state['awaiting_checkout_darts'])
        self.assertEqual(player['legs'], 1)
        self.assertEqual(player['darts'], 2)
        self.assertEqual(player['darts_thrown'], 2)
        self.assertEqual(player['score'], 501)

        player['score'] = 40
        self.game.update_score(40)
        match_state = self.game.confirm_checkout(1)

        self.assertEqual(match_state['winner'], 'Alice')
        self.assertEqual(player['legs'], 2)
        self.assertEqual(player['darts'], 3)
        self.assertEqual(player['darts_thrown'], 3)

    def test_checkout_rejects_invalid_dart_count_and_can_be_undone(self):
        self.game.update_score(40)

        with self.assertRaises(ValueError):
            self.game.confirm_checkout(4)

        restored_state = self.game.go_back()

        self.assertFalse(restored_state['awaiting_checkout_darts'])
        self.assertEqual(self.game.active_players[0]['score'], 40)
        self.assertEqual(self.game.active_players[0]['legs'], 0)

    def test_bot_checkout_uses_three_darts_without_waiting_for_user(self):
        self.game.start_game(['Bot'], legs_to_win=1, mode='501')
        with patch.object(self.game, '_generate_bot_score', return_value=501):
            state = self.game.resolve_bot_turn()

        self.assertEqual(state['winner'], 'Bot')
        self.assertFalse(state['awaiting_checkout_darts'])
        self.assertEqual(self.game.active_players[0]['darts'], 3)


class TestSavedGamePersistence(unittest.TestCase):
    def test_match_state_and_undo_history_survive_game_instance_restart(self):
        with TemporaryDirectory() as directory:
            database_path = f'{directory}/players.db'
            game = dartgame(database_path)
            game.player_db.add_player('Alice')
            game.start_game(['Alice'], legs_to_win=2, mode='501')
            game.update_score(60)

            restored_game = dartgame(database_path)

            self.assertEqual(restored_game.mode, '501')
            self.assertEqual(restored_game.legs_to_win, 2)
            self.assertEqual(restored_game.current_player_index, 0)
            self.assertEqual(restored_game.active_players[0]['score'], 441)
            self.assertEqual(restored_game.active_players[0]['darts_thrown'], 3)
            self.assertEqual(restored_game.active_players[0]['match_score'], 60)
            self.assertEqual(len(restored_game.history), 1)

            restored_game.go_back()
            self.assertEqual(restored_game.active_players[0]['score'], 501)

    def test_pending_checkout_survives_restart_and_can_be_confirmed(self):
        with TemporaryDirectory() as directory:
            database_path = f'{directory}/players.db'
            game = dartgame(database_path)
            game.player_db.add_player('Alice')
            game.start_game(['Alice'], legs_to_win=2, mode='501')
            game.active_players[0]['score'] = 40
            game.update_score(40)

            restored_game = dartgame(database_path)

            self.assertTrue(restored_game.get_game_state()['awaiting_checkout_darts'])
            self.assertEqual(restored_game.pending_checkout['score'], 40)
            state = restored_game.confirm_checkout(2)
            self.assertEqual(state['active_players'][0]['legs'], 1)
            self.assertEqual(state['active_players'][0]['darts_thrown'], 2)

            restored_game.discard_saved_game()
            self.assertIsNone(dartgame(database_path).get_saved_game_summary())

    def test_scoring_statistics_survive_game_instance_restart(self):
        with TemporaryDirectory() as directory:
            database_path = f'{directory}/players.db'
            game = dartgame(database_path)
            game.player_db.add_player('Alice')
            game.start_game(['Alice'], mode='501')
            game.update_score(180)

            restored_game = dartgame(database_path)

            alice = next(
                player for player in restored_game.player_db.get_player_statistics()
                if player['name'] == 'Alice'
            )
            self.assertEqual(alice['count_180'], 1)
            self.assertEqual(alice['count_140_plus'], 1)
            self.assertEqual(alice['count_100_plus'], 1)
            self.assertEqual(alice['first9_average'], 180)

    def test_cricket_marks_and_current_player_survive_restart(self):
        with TemporaryDirectory() as directory:
            database_path = f'{directory}/players.db'
            game = dartgame(database_path)
            game.player_db.add_player('Alice')
            game.player_db.add_player('Bob')
            game.start_game(['Alice', 'Bob'], mode='cricket')
            game.update_score({'shots': [{'target': '20', 'multiplier': 3}]})

            restored_game = dartgame(database_path)

            self.assertEqual(restored_game.mode, 'cricket')
            self.assertEqual(restored_game.current_player_index, 1)
            self.assertEqual(restored_game.active_players[0]['cricket']['20'], 3)
            self.assertEqual(restored_game.active_players[1]['name'], 'Bob')

class TestPlayerStatistics(unittest.TestCase):
    def test_scoring_visits_update_lifetime_stats_and_first_nine_average(self):
        game = dartgame(':memory:')
        game.player_db.add_player('Alice')
        game.start_game(['Alice'], legs_to_win=1, mode='501')

        game.update_score(180)
        game.update_score(140)
        game.update_score(80)
        game.active_players[0]['score'] = 101
        game.update_score(101)
        game.confirm_checkout(2)

        stats = game.player_db.get_player_statistics()
        alice = next(player for player in stats if player['name'] == 'Alice')
        self.assertEqual(alice['count_180'], 1)
        self.assertEqual(alice['count_140_plus'], 2)
        self.assertEqual(alice['count_100_plus'], 3)
        self.assertEqual(alice['legs_played'], 1)
        self.assertAlmostEqual(alice['first9_average'], 400 / 9 * 3)
        self.assertAlmostEqual(alice['average'], 501 / 11 * 3, places=2)

    def test_bust_counts_darts_but_not_invalid_high_score(self):
        game = dartgame(':memory:')
        game.player_db.add_player('Alice')
        game.start_game(['Alice'], mode='501')
        game.active_players[0]['score'] = 100

        game.update_score(180)

        stats = next(
            player for player in game.player_db.get_player_statistics()
            if player['name'] == 'Alice'
        )
        self.assertEqual(stats['count_180'], 0)
        self.assertEqual(stats['count_140_plus'], 0)
        self.assertEqual(stats['count_100_plus'], 0)
        self.assertEqual(stats['darts_thrown'], 3)
        self.assertEqual(stats['first9_average'], 0)

    def test_undo_restores_lifetime_statistics(self):
        game = dartgame(':memory:')
        game.player_db.add_player('Alice')
        game.start_game(['Alice'], mode='501')
        game.update_score(180)

        updated = next(
            player for player in game.player_db.get_player_statistics()
            if player['name'] == 'Alice'
        )
        self.assertEqual(updated['count_180'], 1)

        game.go_back()
        restored = next(
            player for player in game.player_db.get_player_statistics()
            if player['name'] == 'Alice'
        )
        self.assertEqual(restored['count_180'], 0)
        self.assertEqual(restored['darts_thrown'], 0)
        self.assertEqual(restored['score'], 0)

    def test_existing_player_database_is_migrated(self):
        with TemporaryDirectory() as directory:
            database_path = f'{directory}/players.db'
            import sqlite3
            with sqlite3.connect(database_path) as connection:
                connection.execute("""
                    CREATE TABLE players (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        average REAL DEFAULT 0.0,
                        darts_thrown INTEGER DEFAULT 0,
                        Score INTEGER DEFAULT 0
                    )
                """)
                connection.execute(
                    "INSERT INTO players (name, average, darts_thrown, Score) VALUES (?, ?, ?, ?)",
                    ('Alice', 40.0, 3, 40),
                )

            database = PlayersDatabase(database_path)

            alice = next(
                player for player in database.get_player_statistics()
                if player['name'] == 'Alice'
            )
            self.assertEqual(alice['average'], 40.0)
            self.assertEqual(alice['count_180'], 0)
            self.assertEqual(alice['legs_played'], 0)


if __name__ == '__main__':
    unittest.main()
