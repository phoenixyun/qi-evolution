import numpy as np
from enum import IntEnum

class Stone(IntEnum):
    EMPTY = 0
    BLACK = 1
    WHITE = 2

class GoEngine:
    def __init__(self, size=9):
        self.size = size
        self.board = np.zeros((size, size), dtype=int)
        self.current_player = Stone.BLACK
        self.move_history = []
        self.ko_point = None
        self.last_move = None
        self.consecutive_passes = 0
        self.game_over = False
        self.captures = {Stone.BLACK: 0, Stone.WHITE: 0}

    def get_opponent(self, color):
        return Stone.WHITE if color == Stone.BLACK else Stone.BLACK

    def get_group(self, r, c):
        color = self.board[r][c]
        if color == Stone.EMPTY:
            return set()
        group = set()
        stack = [(r, c)]
        while stack:
            cr, cc = stack.pop()
            if (cr, cc) in group:
                continue
            group.add((cr, cc))
            for nr, nc in [(cr-1, cc), (cr+1, cc), (cr, cc-1), (cr, cc+1)]:
                if 0 <= nr < self.size and 0 <= nc < self.size:
                    if self.board[nr][nc] == color and (nr, nc) not in group:
                        stack.append((nr, nc))
        return group

    def get_liberties(self, group):
        liberties = set()
        for r, c in group:
            for nr, nc in [(r-1, c), (r+1, c), (r, c-1), (r, c+1)]:
                if 0 <= nr < self.size and 0 <= nc < self.size:
                    if self.board[nr][nc] == Stone.EMPTY:
                        liberties.add((nr, nc))
        return liberties

    def get_liberties_for_color(self, color):
        visited = set()
        all_liberties = set()
        groups = []
        for r in range(self.size):
            for c in range(self.size):
                if self.board[r][c] == color and (r, c) not in visited:
                    group = self.get_group(r, c)
                    visited.update(group)
                    libs = self.get_liberties(group)
                    all_liberties.update(libs)
                    groups.append((group, libs))
        return all_liberties, groups

    def is_valid_move(self, r, c):
        if not (0 <= r < self.size and 0 <= c < self.size):
            return False
        if self.board[r][c] != Stone.EMPTY:
            return False
        if self.ko_point == (r, c):
            return False
        color = self.current_player
        opponent = self.get_opponent(color)
        self.board[r][c] = color
        opponent_group_to_capture = None
        for nr, nc in [(r-1, c), (r+1, c), (r, c-1), (r, c+1)]:
            if 0 <= nr < self.size and 0 <= nc < self.size:
                if self.board[nr][nc] == opponent:
                    g = self.get_group(nr, nc)
                    if len(self.get_liberties(g)) == 0:
                        opponent_group_to_capture = g
        if opponent_group_to_capture:
            self.board[r][c] = Stone.EMPTY
            return True
        my_group = self.get_group(r, c)
        if len(self.get_liberties(my_group)) > 0:
            self.board[r][c] = Stone.EMPTY
            return True
        self.board[r][c] = Stone.EMPTY
        return False

    def get_legal_moves(self):
        moves = []
        for r in range(self.size):
            for c in range(self.size):
                if self.is_valid_move(r, c):
                    moves.append((r, c))
        return moves

    def make_move(self, r, c):
        if self.game_over:
            return False
        if (r, c) == (-1, -1):
            self.consecutive_passes += 1
            self.move_history.append((-1, -1, self.current_player))
            self.current_player = self.get_opponent(self.current_player)
            self.ko_point = None
            if self.consecutive_passes >= 2:
                self.game_over = True
            return True
        if not self.is_valid_move(r, c):
            return False
        color = self.current_player
        opponent = self.get_opponent(color)
        self.board[r][c] = color
        captured = 0
        for nr, nc in [(r-1, c), (r+1, c), (r, c-1), (r, c+1)]:
            if 0 <= nr < self.size and 0 <= nc < self.size:
                if self.board[nr][nc] == opponent:
                    g = self.get_group(nr, nc)
                    if len(self.get_liberties(g)) == 0:
                        for pr, pc in g:
                            self.board[pr][pc] = Stone.EMPTY
                            captured += 1
                        self.captures[color] += len(g)
        self.ko_point = None
        if captured == 1:
            group = self.get_group(r, c)
            libs = self.get_liberties(group)
            if len(group) == 1 and len(libs) == 1:
                self.ko_point = list(libs)[0]
        self.move_history.append((r, c, color))
        self.last_move = (r, c)
        self.current_player = self.get_opponent(color)
        self.consecutive_passes = 0
        return True

    def calculate_score(self):
        territory_black = 0
        territory_white = 0
        visited = set()
        for r in range(self.size):
            for c in range(self.size):
                if (r, c) in visited:
                    continue
                if self.board[r][c] != Stone.EMPTY:
                    continue
                region = set()
                stack = [(r, c)]
                borders = set()
                while stack:
                    cr, cc = stack.pop()
                    if (cr, cc) in region:
                        continue
                    region.add((cr, cc))
                    for nr, nc in [(cr-1, cc), (cr+1, cc), (cr, cc-1), (cr, cc+1)]:
                        if 0 <= nr < self.size and 0 <= nc < self.size:
                            if self.board[nr][nc] == Stone.EMPTY:
                                stack.append((nr, nc))
                            else:
                                borders.add(self.board[nr][nc])
                visited.update(region)
                if borders == {Stone.BLACK}:
                    territory_black += len(region)
                elif borders == {Stone.WHITE}:
                    territory_white += len(region)
        black_total = territory_black + np.count_nonzero(self.board == Stone.BLACK)
        white_total = territory_white + np.count_nonzero(self.board == Stone.WHITE) + 6.5
        return black_total, white_total, territory_black, territory_white

    def get_liberty_groups(self, color):
        visited = set()
        result = []
        for r in range(self.size):
            for c in range(self.size):
                if self.board[r][c] == color and (r, c) not in visited:
                    group = self.get_group(r, c)
                    visited.update(group)
                    libs = self.get_liberties(group)
                    if libs:
                        result.append({
                            'stones': group,
                            'liberties': libs,
                            'size': len(group),
                            'liberty_count': len(libs),
                            'color': color
                        })
        return result

    def get_game_state(self):
        return {
            'board': self.board.copy(),
            'current_player': self.current_player,
            'move_count': len(self.move_history),
            'captures': dict(self.captures),
            'game_over': self.game_over,
            'last_move': self.last_move,
            'consecutive_passes': self.consecutive_passes,
        }
