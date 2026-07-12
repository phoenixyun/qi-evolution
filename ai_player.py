import random
import math
import numpy as np

from go_engine import GoEngine, Stone

class AIPlayer:
    def __init__(self, color, name="AI Player", style="balanced"):
        self.color = color
        self.name = name
        self.style = style
        self.move_count = 0

    def get_move(self, engine):
        legal = engine.get_legal_moves()
        if not legal:
            return (-1, -1)
        best_move = None
        best_score = -999999
        for r, c in legal:
            score = self.evaluate_move(engine, r, c)
            noise = random.uniform(0, 0.3)
            if score + noise > best_score:
                best_score = score + noise
                best_move = (r, c)
        self.move_count += 1
        return best_move if best_move else (-1, -1)

    def evaluate_move(self, engine, r, c):
        """
        用临时引擎真实落子来评估 — 干净、准确、无副作用
        """
        color = self.color
        opponent = engine.get_opponent(color)

        # 创建临时引擎，克隆当前状态
        temp = GoEngine(engine.size)
        temp.board = engine.board.copy()
        temp.current_player = engine.current_player
        temp.captures = {Stone.BLACK: engine.captures[Stone.BLACK],
                         Stone.WHITE: engine.captures[Stone.WHITE]}
        temp.ko_point = engine.ko_point
        temp.game_over = engine.game_over

        # 真实落子
        if not temp.make_move(r, c):
            return -99999  # 非法着法

        score = 0

        # 1) 提子奖励
        captured = temp.captures[color] - engine.captures[color]
        score += captured * 50

        # 2) 自身气数
        my_group = temp.get_group(r, c)
        libs = temp.get_liberties(my_group)
        score += len(libs) * 12

        # 3) 位置倾向
        center = engine.size // 2
        dist = abs(r - center) + abs(c - center)
        if self.style == "territorial":
            score += (engine.size - dist) * 0.8
        elif self.style == "aggressive":
            score += dist * 0.3
        else:
            score += (engine.size - dist) * 0.4

        # 4) 边缘惩罚 (太靠边棋效低)
        edge_penalty = 1.0
        if r <= 1 or r >= engine.size - 2: edge_penalty *= 0.85
        if c <= 1 or c >= engine.size - 2: edge_penalty *= 0.85
        if r == 0 or r == engine.size - 1: edge_penalty *= 0.7
        if c == 0 or c == engine.size - 1: edge_penalty *= 0.7
        score *= edge_penalty

        # 5) 攻击有价值：紧对方气
        for nr, nc in [(r-1, c), (r+1, c), (r, c-1), (r, c+1)]:
            if 0 <= nr < engine.size and 0 <= nc < engine.size:
                if engine.board[nr][nc] == opponent:
                    g = engine.get_group(nr, nc)
                    opp_libs = engine.get_liberties(g)
                    n_libs_after = len(opp_libs) - (1 if (r, c) in opp_libs else 0)
                    if n_libs_after <= 1:
                        score += 45   # 叫吃/提子
                    elif n_libs_after <= 2:
                        score += 20   # 紧气

        # 6) 分散奖励 — 避免扎堆
        # 检查周围8格内的己方棋子数量
        crowding = 0
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                if dr == 0 and dc == 0: continue
                nr, nc = r + dr, c + dc
                if 0 <= nr < engine.size and 0 <= nc < engine.size:
                    if engine.board[nr][nc] == color:
                        crowding += 1
        score -= crowding * 5  # 周围己子多 → 扣分鼓励分散

        return score


class RandomAI:
    def __init__(self, color, name="Random AI"):
        self.color = color
        self.name = name

    def get_move(self, engine):
        legal = engine.get_legal_moves()
        if not legal:
            return (-1, -1)
        return random.choice(legal)
