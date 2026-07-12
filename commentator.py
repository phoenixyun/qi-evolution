"""
Commentator — 棋评解说模块（桩模块）
"""
import random


class Commentator:
    def __init__(self):
        self.commentary_log = []

    def on_move(self, engine, r, c, color_name):
        text = f"{color_name}棋落子 ({r},{c})"
        return text

    def on_liberties_analysis(self, best):
        idiom = best.get("idiom", "一气呵成")
        meaning = best.get("meaning", "")
        return f"【气形】{idiom} {meaning}"

    def on_evolution(self, report):
        gen = report.get("generation", "?")
        return f"【进化】第 {gen} 代演化完成"

    def on_game_over(self, engine):
        black_captures = getattr(engine, 'black_captures', 0)
        white_captures = getattr(engine, 'white_captures', 0)
        return f"终局 — 黑方提子 {black_captures} 子，白方提子 {white_captures} 子"
