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


if __name__ == '__main__':
    unittest.main()
