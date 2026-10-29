import unittest
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
        game = dartgame()
        game.player_db = PlayersDatabase(':memory:')
        game.players = game.player_db.get_all_players()
        game.start_game(['Alice', BOT_NAME], legs_to_win=1, mode='501')

        game.active_players[1]['score'] = 60
        with patch('dartlogic.random.random', return_value=0.1):
            score = game._generate_bot_score(game.active_players[1])
        self.assertEqual(score, 60)

    def test_bot_throws_around_50_points_on_regular_turns(self):
        game = dartgame()
        game.player_db = PlayersDatabase(':memory:')
        game.players = game.player_db.get_all_players()
        game.start_game(['Alice', BOT_NAME], legs_to_win=1, mode='501')

        game.active_players[1]['score'] = 180
        with patch('dartlogic.random.random', return_value=0.8):
            score = game._generate_bot_score(game.active_players[1])
        self.assertGreaterEqual(score, 20)
        self.assertLessEqual(score, 90)


class TestCheckoutDarts(unittest.TestCase):
    def setUp(self):
        self.game = dartgame()
        self.game.player_db = PlayersDatabase(':memory:')
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


if __name__ == '__main__':
    unittest.main()
