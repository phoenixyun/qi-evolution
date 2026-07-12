import random
import math
import time
import numpy as np

from ursina import *
from ursina.shaders import lit_with_shadows_shader

BOARD_COLOR = color.rgb(30, 50, 30)
BG_COLOR = color.rgb(5, 5, 15)
GRID_COLOR = color.rgb(80, 120, 80)
BLACK_STONE = color.rgb(20, 20, 30)
WHITE_STONE = color.rgb(230, 225, 215)
GOLD_GLOW = color.rgb(255, 215, 0)
BLUE_GLOW = color.rgb(50, 100, 255)
LIBERTY_ALPHA = 0.6


class GoVisualizer:
    def __init__(self, board_size=9, auto_mode=True):
        self.board_size = board_size
        self.auto_mode = auto_mode
        self.stone_entities = {}
        self.liberty_entities = []
        self.particles = []
        self.idiom_text = None
        self.commentary_texts = []
        self.move_timer = 0
        self.move_interval = 1.5
        self.game_paused = False
        self.setup_scene()

    def setup_scene(self):
        window.borderless = False
        window.title = '3D 围棋 · 气之进化'
        window.color = BG_COLOR
        window.size = (1280, 720)
        self.camera_angle = 0
        self.camera_height = 12
        self.camera_distance = 14
        # 光源 — 让棋子有立体感
        DirectionalLight(parent=scene, shadows=True)
        self._create_board()
        self._create_background()
        self._create_ui()

    def _create_board(self):
        board_size = self.board_size
        offset = board_size // 2

        # 棋盘底面（厚木板效果）
        Entity(
            model='cube',
            scale=(board_size + 2, 0.3, board_size + 2),
            position=(0, -0.15, 0),
            color=color.rgb(25, 42, 25),
            unlit=True,
        )
        # 棋盘表面 — unlit 确保永远可见，不用依赖光源
        Entity(
            model='quad',
            scale=(board_size + 0.8, board_size + 0.8),
            position=(0, 0, 0),
            rotation=(270, 0, 0),
            color=BOARD_COLOR,
            unlit=True,
        )
        # 棋盘底光晕
        Entity(
            model='quad',
            scale=(board_size + 3, board_size + 3),
            position=(0, -0.02, 0),
            rotation=(270, 0, 0),
            color=color.rgba(20, 80, 40, 40),
            unlit=True,
        )
        # 网格线 — cube 模型，任何角度都可见
        grid_color = GRID_COLOR
        for i in range(board_size):
            pos = i - offset
            # 横线
            Entity(model='cube', scale=(board_size, 0.03, 0.03),
                   position=(0, 0.01, pos), color=grid_color, unlit=True)
            # 竖线
            Entity(model='cube', scale=(0.03, 0.03, board_size),
                   position=(pos, 0.01, 0), color=grid_color, unlit=True)
        # 边框
        edge_color = color.rgb(60, 100, 60)
        half = board_size // 2
        Entity(model='cube', scale=(board_size + 1.2, 0.08, 0.08),
               position=(0, 0.01, -half - 0.6), color=edge_color, unlit=True)
        Entity(model='cube', scale=(board_size + 1.2, 0.08, 0.08),
               position=(0, 0.01, half + 0.6), color=edge_color, unlit=True)
        Entity(model='cube', scale=(0.08, 0.08, board_size + 1.2),
               position=(-half - 0.6, 0.01, 0), color=edge_color, unlit=True)
        Entity(model='cube', scale=(0.08, 0.08, board_size + 1.2),
               position=(half + 0.6, 0.01, 0), color=edge_color, unlit=True)
        # 星位标记
        star_points = [(0, 0)]
        if board_size >= 9:
            star_points.extend([(-2, -2), (-2, 2), (2, -2), (2, 2)])
        for sr, sc in star_points:
            Entity(
                model='sphere', scale=0.12,
                position=(sc - offset, 0.02, sr - offset),
                color=color.rgb(60, 90, 60), unlit=True,
            )

    def _create_background(self):
        # 少量柔和星星，不喧宾夺主
        for _ in range(30):
            x = random.uniform(-30, 30)
            y = random.uniform(-20, 20)
            z = random.uniform(-30, 30)
            size = random.uniform(0.05, 0.15)
            Entity(
                model='sphere', scale=size,
                position=(x, y, z),
                color=color.rgba(200, 200, 255, 150),
                unlit=True,
            )
        scene.ambient_light = color.rgba(60, 70, 80, 255)

    def _create_ui(self):
        CN = 'simhei.ttf'  # 中文字体
        self.idiom_text = Text(
            text='', font=CN,
            position=(-0.72, 0.45),
            scale=1.5,
            color=color.rgb(255, 220, 100),
        )
        self.info_text = Text(
            text='', font=CN,
            position=(-0.72, 0.4),
            scale=1,
            color=color.rgb(180, 200, 180),
        )
        self.evo_text = Text(
            text='', font=CN,
            position=(0.72, 0.45),
            scale=1,
            color=color.rgb(100, 200, 255),
            origin=(0.5, 0),
        )
        self.mode_text = Text(
            text='', font=CN,
            position=(0, 0.48),
            scale=1,
            color=color.rgb(255, 200, 100),
            origin=(0, 0),
        )
        self.game_info_text = Text(
            text='', font=CN,
            position=(0, 0.44),
            scale=0.8,
            color=color.rgb(180, 220, 255),
            origin=(0, 0),
        )
        self.game_title_text = Text(
            text='', font=CN,
            position=(-0.72, 0.35),
            scale=0.7,
            color=color.rgb(140, 180, 220),
        )

    def board_to_world(self, r, c):
        offset = self.board_size // 2
        return Vec3(c - offset, 0.1, r - offset)

    def place_stone(self, r, c, color_val, animate=True):
        key = (r, c)
        if key in self.stone_entities:
            return
        world_pos = self.board_to_world(r, c)
        stone_color = BLACK_STONE if color_val == 1 else WHITE_STONE
        glow_color = BLUE_GLOW if color_val == 1 else GOLD_GLOW
        stone = Entity(
            model='sphere',
            scale=0.85,
            position=world_pos + Vec3(0, 1.5, 0) if animate else world_pos,
            color=stone_color,
            shader=lit_with_shadows_shader,
        )
        if animate:
            stone.animate_position(world_pos, duration=0.3, curve=curve.linear)
        glow_ring = Entity(
            model='quad',
            scale=0.6,
            position=world_pos + Vec3(0, 0.05, 0),
            color=color.rgba(glow_color.r, glow_color.g, glow_color.b, 60),
            unlit=True,
            billboard=True,
        )
        self.stone_entities[key] = {
            'stone': stone,
            'glow': glow_ring,
            'color': color_val,
        }

    def remove_stone(self, r, c):
        key = (r, c)
        if key in self.stone_entities:
            ent = self.stone_entities.pop(key)
            for e in [ent['stone'], ent['glow']]:
                if e:
                    e.fade_out(duration=0.3)
                    destroy(e, delay=0.3)

    def show_liberties(self, liberties_by_group, color_val):
        self.clear_liberties()
        glow_color = BLUE_GLOW if color_val == 1 else GOLD_GLOW
        for group_data in liberties_by_group:
            libs = group_data.get('liberties', set())
            group_size = group_data.get('size', 1)
            intensity = min(1.0, 0.3 + group_size * 0.1)
            is_connected = self._is_connected_liberties(libs)
            pillar_color = color.rgba(
                glow_color.r, glow_color.g, glow_color.b,
                int(180 * intensity)
            )
            for r, c in libs:
                world_pos = self.board_to_world(r, c)
                # 光柱高度设为 0（平铺），以去除竖直发光柱效果
                pillar = Entity(
                    model='cube',
                    scale=(0.15, 0.0, 0.15),
                    position=world_pos + Vec3(0, 0.0, 0),
                    color=pillar_color,
                    unlit=True,
                )
                self.liberty_entities.append(pillar)
                # 不再创建上浮粒子（移除竖直光柱与粒子）
            if is_connected and len(libs) >= 3:
                self._create_connection_lines(libs, glow_color, intensity)

    def _is_connected_liberties(self, libs):
        if len(libs) < 2:
            return False
        lib_set = set(libs)
        visited = set()
        start = next(iter(libs))
        stack = [start]
        while stack:
            r, c = stack.pop()
            if (r, c) in visited:
                continue
            visited.add((r, c))
            for nr, nc in [(r-1,c),(r+1,c),(r,c-1),(r,c+1)]:
                if (nr, nc) in lib_set and (nr, nc) not in visited:
                    stack.append((nr, nc))
        return len(visited) == len(libs)

    def _create_connection_lines(self, libs, glow_color, intensity):
        lib_set = set(libs)
        lines_drawn = set()
        for r, c in libs:
            for nr, nc in [(r+1,c),(r,c+1)]:
                if (nr, nc) in lib_set:
                    if ((r,c),(nr,nc)) not in lines_drawn:
                        lines_drawn.add(((r,c),(nr,nc)))
                        p1 = self.board_to_world(r, c)
                        p2 = self.board_to_world(nr, nc)
                        mid = (p1 + p2) / 2 + Vec3(0, 0.3, 0)
                        dx = abs(r - nr) * 1.0
                        dz = abs(c - nc) * 1.0
                        line = Entity(
                            model='cube',
                            scale=(0.06, 0.06, max(dx, dz, 0.1)),
                            position=mid,
                            color=color.rgba(glow_color.r, glow_color.g, glow_color.b,
                                             int(100 * intensity)),
                            unlit=True,
                        )
                        self.liberty_entities.append(line)

    def clear_liberties(self):
        for ent in self.liberty_entities:
            if ent:
                destroy(ent)
        self.liberty_entities = []
        self.particles = []

    def update_particles(self, dt):
        dead = []
        for p in self.particles:
            p['age'] += dt
            if p['age'] >= p['lifetime']:
                p['entity'].position += Vec3(0, 0.01, 0)
                p['entity'].alpha -= dt * 0.5
                if p['entity'].alpha <= 0:
                    dead.append(p)
            else:
                ent = p['entity']
                ent.position += Vec3(p['drift_x'], p['speed'] * dt, p['drift_z'])
                fade = 1 - (p['age'] / p['lifetime'])
                ent.alpha = max(0, fade * 0.6)
        for p in dead:
            if p in self.particles:
                self.particles.remove(p)

    def update_camera(self, dt):
        self.camera_angle += dt * 8
        rad = math.radians(self.camera_angle)
        x = math.sin(rad) * self.camera_distance
        z = math.cos(rad) * self.camera_distance
        camera.position = Vec3(x, self.camera_height, z)
        camera.look_at(Vec3(0, 0, 0))

    def show_idiom(self, text, meaning='', mood=''):
        if self.idiom_text:
            self.idiom_text.text = f"『{text}』"
        if self.info_text:
            self.info_text.text = meaning

    def show_mode(self, mode_name):
        """显示当前模式"""
        if self.mode_text:
            self.mode_text.text = f"◆ {mode_name} ◆"

    def show_game_replay_info(self, status, move_info):
        """显示棋谱回放信息"""
        if not status or not move_info:
            return
        game_name = status.get('game_name', '')
        current = move_info.get('move_number', 0)
        total = move_info.get('total', 0) or move_info.get('total_moves', status.get('total_moves', 0))
        pos = move_info.get('position', '')
        color = move_info.get('color', '')
        progress = status.get('progress', 0)

        if self.game_info_text:
            if current > 0:
                bar_len = 16
                filled = int(progress / 100 * bar_len)
                bar = '█' * filled + '░' * (bar_len - filled)
                self.game_info_text.text = f"{color} {pos}  {bar}  {current}/{total}"
            else:
                self.game_info_text.text = f"共 {total} 手，准备开始"

        if self.game_title_text:
            self.game_title_text.text = f"『{game_name}』"

    def show_evolution_status(self, gen, patterns, idioms):
        if self.evo_text:
            self.evo_text.text = f"第{gen}代\n模式:{patterns} 成语:{idioms}"

    def add_commentary_line(self, text):
        existing = self.commentary_texts
        if len(existing) > 6:
            oldest = existing.pop(0)
            destroy(oldest)
        y_pos = -0.35 - len(existing) * 0.04
        t = Text(
            text=text, font='simhei.ttf',
            position=(-0.75, y_pos),
            scale=0.7,
            color=color.rgb(160, 180, 160),
        )
        self.commentary_texts.append(t)

    def set_move_timer(self, interval):
        self.move_interval = interval

    def update(self, dt):
        self.update_camera(dt)
        self.update_particles(dt)
        # 更新光效（若存在高度则脉动），已移除竖直光柱后的实体通常 scale_y == 0
        for ent in self.liberty_entities:
            try:
                if getattr(ent, 'scale_y', 0) > 0.3:
                    pulse = 1 + math.sin(time.time() * 2 + ent.position.x) * 0.1
                    ent.scale_y = 0.5 * pulse
                    ent.alpha = LIBERTY_ALPHA * (0.8 + 0.2 * math.sin(time.time() * 1.5 + ent.position.z))
            except Exception:
                # 忽略不支持属性的实体
                continue
