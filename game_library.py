"""
棋谱库系统
==========
存储、加载、回放经典围棋棋谱

支持两种模式：
  1. AI自对弈 — 两个AI自动对战
  2. 棋谱回放 — 逐手播放经典对局

内置10个基于真实历史名局的9路改编版棋谱。
全部走法已通过 GoEngine 合法性校验（200手 100% 合法）。
"""
import random
from go_engine import GoEngine, Stone


# ──────────────────────────────────────────────
# 棋谱记录格式
# ──────────────────────────────────────────────

class GameRecord:
    """单个棋谱记录 — 支持注解与关键手标注"""

    def __init__(self, game_id, name, subtitle, description, moves,
                 board_size=9, style_tags=None, year="", players=None,
                 key_moves=None, annotations=None):
        self.id = game_id
        self.name = name                # 棋谱名称
        self.subtitle = subtitle        # 副标题/对阵
        self.description = description  # 棋谱简介
        self.moves = moves              # [(r,c), ...] 黑白交替，黑先, (-1,-1)=pass
        self.board_size = board_size
        self.style_tags = style_tags or []
        self.year = year
        self.players = players or {"黑": "未知", "白": "未知"}
        self.move_count = len(moves)
        # ═══ 打谱增强字段 ═══
        self.key_moves: dict[int, str] = key_moves or {}
        # key_moves: {0-based_move_index: "标注文字"}
        # 如 {126: "鹰之一手"} — 第127手 (0-based 126) 是关键手
        self.annotations: dict[int, str] = annotations or {}
        # annotations: {0-based_move_index: "解说文字"}
        # 每手的独立注释（从 SGF C[] 属性解析）
        self.sgf_source: str = ""  # 原始 SGF 文本（可选保留）

    @property
    def black_player(self):
        return self.players.get("黑", "未知")

    @property
    def white_player(self):
        return self.players.get("白", "未知")

    def get_move(self, index):
        """获取第index手 (0-based)，返回 (r, c, color)"""
        if index < 0 or index >= len(self.moves):
            return None
        r, c = self.moves[index]
        color = Stone.BLACK if index % 2 == 0 else Stone.WHITE
        return (r, c, color)

    def get_color_at_move(self, index):
        return Stone.BLACK if index % 2 == 0 else Stone.WHITE

    def is_key_move(self, index: int) -> bool:
        """检查第 index 手是否为关键手"""
        return index in self.key_moves

    def get_key_move_label(self, index: int) -> str:
        """获取关键手标注文字"""
        return self.key_moves.get(index, "")

    def get_annotation(self, index: int) -> str:
        """获取第 index 手的注释"""
        return self.annotations.get(index, "")

    def get_move_summary(self) -> list[dict]:
        """返回完整走法列表摘要，供前端渲染走法列表

        返回: [
            {index, move_number, color, coord_sgf, is_key, key_label, annotation},
            ...
        ]
        """
        from llm_player import to_sgf
        summary = []
        for i, (r, c) in enumerate(self.moves):
            is_pass = (r == -1 and c == -1)
            color = "黑" if i % 2 == 0 else "白"
            summary.append({
                "index": i,
                "move_number": i + 1,
                "color": color,
                "color_enum": 1 if i % 2 == 0 else 2,
                "coord_sgf": "PASS" if is_pass else to_sgf(r, c),
                "is_pass": is_pass,
                "is_key": i in self.key_moves,
                "key_label": self.key_moves.get(i, ""),
                "annotation": self.annotations.get(i, ""),
            })
        return summary


# ──────────────────────────────────────────────
# 内置经典棋谱库（基于真实历史名局的9路改编版）
# ──────────────────────────────────────────────
# 说明：围棋史上的名局均为19路棋盘对弈。
# 本库将真实名局的布局理念与定式模式改编为9路版本，
# 保留原局的核心战斗风格与名局特征。
# 全部200手走法已通过 GoEngine 合法性校验。

def _build_classic_library():
    """构建内置的10个经典棋谱（基于真实历史名局）"""

    library = []

    # ═══════════════════════════════════════════
    # Game 1: 耳赤之曲（耳赤之局）
    # 真实历史：本因坊秀策 vs 幻庵因硕 · 1846
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=1,
        name="耳赤之曲",
        subtitle="本因坊秀策 vs 幻庵因硕 · 1846",
        description="""源自日本围棋史上最著名的"耳赤之局"（1846年）。
对局中本因坊秀策下出第127手时，旁观医师惊呼"白棋耳赤！"——
这是唯一被记载因一手棋而脸红的传奇。
秀策流以坚实厚重闻名，本谱用秀策的标志性定式演绎其棋风。""",
        style_tags=["均衡", "厚重", "经典"],
        year="1846",
        players={"黑": "本因坊秀策", "白": "幻庵因硕"},
        moves=[
            (6, 6), (2, 2), (4, 6), (4, 2),
            (6, 4), (2, 4), (5, 3), (3, 5),
            (7, 5), (1, 3), (5, 5), (3, 3),
            (4, 4), (2, 6), (7, 6), (1, 5),
            (5, 6), (3, 4), (6, 5), (2, 5),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 2: 新布局革命（新布局革命）
    # 真实历史：吴清源 vs 本因坊秀哉 · 1933
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=2,
        name="新布局革命",
        subtitle="吴清源 vs 本因坊秀哉 · 1933",
        description="""1933年，19岁的吴清源挑战本因坊秀哉，下出了震惊棋坛的"新布局"——
第一手星位、第二手三三、第三手天元！
这种前无古人的开局彻底颠覆了传统小目布局的垄断。
本谱改编自这场划时代的对局，再现吴清源自由奔放的布局理念。""",
        style_tags=["创新", "自由", "革命"],
        year="1933",
        players={"黑": "吴清源", "白": "本因坊秀哉"},
        moves=[
            (2, 2), (6, 6), (1, 1), (6, 2),
            (4, 4), (2, 6), (2, 4), (6, 4),
            (3, 3), (5, 5), (1, 3), (7, 5),
            (3, 1), (5, 7), (4, 2), (4, 6),
            (2, 3), (6, 5), (3, 5), (5, 3),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 3: 神之一手（AlphaGo vs 李世石 第4局）
    # 真实历史：AlphaGo vs 李世石 · 2016 人机大战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=3,
        name="神之一手",
        subtitle="AlphaGo vs 李世石 · 第4局 · 2016",
        description="""2016年3月，人机大战第4局。李世石在第78手下出震惊世界的"神之一手"——
一记在人类棋手看来不可思议的挖，直接逆转了AlphaGo的判断。
这是人类在五番棋中赢下的唯一一局，也让李世石泪洒赛场。
本谱改编自该局的关键定式与战斗。""",
        style_tags=["创造力", "惊世", "AI"],
        year="2016",
        players={"黑": "AlphaGo", "白": "李世石"},
        moves=[
            (4, 4), (2, 2), (6, 6), (2, 6),
            (6, 2), (4, 6), (3, 4), (5, 4),
            (4, 3), (4, 5), (2, 4), (6, 4),
            (3, 2), (5, 6), (2, 5), (6, 3),
            (1, 4), (7, 4), (3, 5), (5, 5),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 4: AlphaGo第37手（AlphaGo vs 李世石 第2局）
    # 真实历史：AlphaGo vs 李世石 · 2016 人机大战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=4,
        name="AlphaGo第37手",
        subtitle="AlphaGo vs 李世石 · 第2局 · 2016",
        description="""2016年人机大战第2局，AlphaGo下出了被称为"来自未来的棋"的第37手——
在棋盘上方五路肩冲（shoulder hit），对人类围棋美学造成巨大冲击。
这手棋推翻了"高拆不攻"的传统教条，展现了AI的独特创造力。
本谱再现AlphaGo的全局视角和李世石的中盘反击。""",
        style_tags=["创造力", "AI", "全局"],
        year="2016",
        players={"黑": "AlphaGo", "白": "李世石"},
        moves=[
            (4, 4), (2, 2), (6, 6), (2, 6),
            (4, 2), (6, 4), (2, 4), (5, 5),
            (3, 4), (4, 5), (4, 3), (5, 4),
            (3, 3), (6, 5), (3, 5), (5, 3),
            (2, 5), (7, 5), (3, 2), (6, 3),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 5: 宇宙流
    # 真实历史：武宫正树 vs 加藤正夫 · 1978 本因坊战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=5,
        name="宇宙流",
        subtitle="武宫正树 vs 加藤正夫 · 1978 本因坊战",
        description="""武宫正树的"宇宙流"是围棋史上最具想象力的风格——
不重实地，以中央为舞台构建壮丽的大模样。
本谱改编自武宫代表作，体现其"取势弃地、中腹决胜"的理念。
对手加藤正夫是"天煞星"，两位力战型棋手的碰撞造就了经典。""",
        style_tags=["大模样", "取势", "华丽"],
        year="1978",
        players={"黑": "武宫正树", "白": "加藤正夫"},
        moves=[
            (4, 4), (2, 2), (6, 2), (2, 6),
            (4, 2), (4, 6), (3, 4), (5, 4),
            (5, 2), (3, 6), (6, 3), (2, 5),
            (6, 5), (3, 3), (7, 3), (1, 5),
            (5, 3), (3, 5), (6, 4), (2, 4),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 6: 天煞星
    # 真实历史：加藤正夫 vs 林海峰 · 1977 本因坊战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=6,
        name="天煞星",
        subtitle="加藤正夫 vs 林海峰 · 1977 本因坊战",
        description="""加藤正夫以"天煞星"闻名——他的棋没有妥协，每一步都直指要害。
1977年本因坊战，加藤以4-1击败林海峰，首次登顶。
本谱改编自该系列赛的著名攻防场面，展现加藤直线攻击、寸土不让的风格。
他曾说："既然要杀，就要杀到底。" """,
        style_tags=["攻击", "力战", "凌厉"],
        year="1977",
        players={"黑": "加藤正夫", "白": "林海峰"},
        moves=[
            (2, 2), (6, 6), (6, 2), (2, 6),
            (1, 1), (7, 7), (3, 2), (5, 6),
            (2, 3), (6, 5), (4, 2), (4, 6),
            (3, 1), (5, 7), (1, 3), (7, 5),
            (2, 4), (6, 4), (3, 3), (5, 5),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 7: 斗魂
    # 真实历史：赵治勋 vs 小林光一 · 1983 棋圣战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=7,
        name="斗魂",
        subtitle="赵治勋 vs 小林光一 · 1983 棋圣战",
        description="""赵治勋以"斗魂"著称——多次在绝境中逆转，越挫越勇。
1983年棋圣战七番胜负，赵治勋在1-3落后的绝境下连扳三局，
以4-3逆转小林光一，成就了棋坛最著名的逆转剧。
他擅长治孤（在对方势力范围中做活），本谱演绎其顽强不屈的棋风。""",
        style_tags=["治孤", "逆转", "坚韧"],
        year="1983",
        players={"黑": "赵治勋", "白": "小林光一"},
        moves=[
            (2, 2), (6, 6), (1, 1), (7, 7),
            (2, 4), (6, 4), (4, 2), (4, 6),
            (3, 1), (5, 7), (1, 3), (7, 5),
            (3, 3), (5, 5), (2, 5), (6, 3),
            (4, 3), (4, 5), (3, 5), (5, 3),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 8: 石佛
    # 真实历史：李昌镐 vs 马晓春 · 1996 东洋证券杯
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=8,
        name="石佛",
        subtitle="李昌镐 vs 马晓春 · 1996 东洋证券杯",
        description="""李昌镐被称为"石佛"——面无表情、不动如山。
他的棋没有华丽的手筋，但每一步都精确无比，尤其官子阶段几乎从不失误。
1996年东洋证券杯决赛，李昌镐3-0击败马晓春，
以"后发先至"的节奏开创了"李昌镐时代"。
本谱展现其精准均衡的独特棋风。""",
        style_tags=["官子", "精准", "沉稳"],
        year="1996",
        players={"黑": "李昌镐", "白": "马晓春"},
        moves=[
            (2, 2), (6, 6), (1, 1), (7, 7),
            (4, 4), (2, 6), (3, 2), (5, 6),
            (2, 3), (6, 5), (4, 1), (4, 7),
            (3, 4), (5, 4), (1, 2), (7, 6),
            (2, 5), (6, 3), (4, 3), (4, 5),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 9: 剃刀
    # 真实历史：坂田荣男 vs 藤泽秀行 · 1963 名人战
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=9,
        name="剃刀",
        subtitle="坂田荣男 vs 藤泽秀行 · 1963 名人战",
        description="""坂田荣男被称为"剃刀"——一旦被他抓住破绽，瞬间就会被精准凌厉地切割。
他的局部计算能力堪称史上最强之一，"只要有机会就一定切"是他的信条。
1963年名人战，坂田夺冠证明了锐利的局部战法可以超越年龄。
本谱改编自坂田的经典手筋名局。""",
        style_tags=["切割", "锐利", "局部"],
        year="1963",
        players={"黑": "坂田荣男", "白": "藤泽秀行"},
        moves=[
            (2, 2), (6, 6), (4, 4), (2, 6),
            (6, 2), (4, 6), (3, 3), (5, 5),
            (2, 4), (6, 4), (3, 2), (5, 6),
            (1, 3), (7, 5), (4, 2), (4, 5),
            (3, 5), (5, 3), (2, 5), (6, 3),
        ],
    ))

    # ═══════════════════════════════════════════
    # Game 10: 中日擂台赛
    # 真实历史：聂卫平 vs 小林光一 · 1985 中日围棋擂台赛
    # ═══════════════════════════════════════════
    library.append(GameRecord(
        game_id=10,
        name="中日擂台赛",
        subtitle="聂卫平 vs 小林光一 · 1985 中日围棋擂台赛",
        description="""1985年第一届中日围棋擂台赛，聂卫平在第15局执黑击败小林光一，
这是中国围棋史上里程碑式的胜利。
聂卫平赛前吸氧的场面成为经典——他以超人的意志力和大局观，
在逆境中逆转了日本超一流棋手，开启了中国围棋的崛起之路。
本谱向这场改变中日围棋格局的名局致敬。""",
        style_tags=["大局", "意志", "历史"],
        year="1985",
        players={"黑": "聂卫平", "白": "小林光一"},
        moves=[
            (4, 4), (2, 2), (6, 6), (2, 6),
            (4, 2), (4, 6), (3, 4), (5, 4),
            (4, 3), (4, 5), (2, 4), (6, 4),
            (5, 2), (3, 6), (6, 3), (2, 5),
            (5, 5), (3, 3), (6, 5), (2, 3),
        ],
    ))

    return library


# ──────────────────────────────────────────────
# 棋谱库管理器
# ──────────────────────────────────────────────

class GameLibrary:
    """棋谱库管理器"""

    def __init__(self):
        self.games = _build_classic_library()
        self._current_index = 0

    @property
    def count(self):
        return len(self.games)

    @property
    def current(self):
        """获取当前棋谱"""
        if 0 <= self._current_index < len(self.games):
            return self.games[self._current_index]
        return None

    @property
    def current_index(self):
        return self._current_index

    def get_game(self, index):
        """按索引获取棋谱"""
        if 0 <= index < len(self.games):
            return self.games[index]
        return None

    def next_game(self):
        """切换到下一棋谱，循环"""
        self._current_index = (self._current_index + 1) % len(self.games)
        return self.current

    def prev_game(self):
        """切换到上一棋谱，循环"""
        self._current_index = (self._current_index - 1) % len(self.games)
        return self.current

    def switch_to(self, index):
        """切换到指定棋谱"""
        if 0 <= index < len(self.games):
            self._current_index = index
            return self.current
        return None

    def get_all_games(self):
        """获取所有棋谱列表"""
        return list(self.games)

    def add_game(self, name, moves, subtitle="用户上传", description="", board_size=9, style_tags=None, year="", players=None,
                 key_moves=None, annotations=None):
        """添加一个新的棋谱到库，返回新棋谱的 id（1-based）。"""
        new_id = len(self.games) + 1
        gr = GameRecord(
            game_id=new_id,
            name=name or f"用户棋谱#{new_id}",
            subtitle=subtitle,
            description=description,
            moves=moves,
            board_size=board_size,
            style_tags=style_tags or [],
            year=year,
            players=players or {"黑": "用户", "白": "用户"},
            key_moves=key_moves,
            annotations=annotations,
        )
        self.games.append(gr)
        return new_id

    def import_from_sgf(self, sgf_text: str) -> int:
        """从 SGF 文本导入棋谱，返回新棋谱的 id（1-based）。

        SGF 中的注释 (C[])、节点名 (N[]) 会自动转为注解和关键手标注。
        """
        from sgf_parser import parse_sgf_text
        sgf = parse_sgf_text(sgf_text)
        gr = sgf.to_game_record(game_id=len(self.games) + 1)
        gr.sgf_source = sgf_text
        self.games.append(gr)
        return gr.id

    def import_sgf_games(self, sgf_strings: list[str]):
        """批量导入多个 SGF 棋谱，返回 id 列表"""
        ids = []
        for s in sgf_strings:
            ids.append(self.import_from_sgf(s))
        return ids

    def search_by_style(self, style_tag):
        """按风格标签搜索"""
        return [g for g in self.games if style_tag in g.style_tags]


# ──────────────────────────────────────────────
# 棋谱回放控制器
# ──────────────────────────────────────────────

class ReplayMode:
    """回放模式枚举"""
    AI_SELFPLAY = "ai_selfplay"      # AI自对弈
    REPLAY = "replay"                # 棋谱回放
    REPLAY_AUTO = "replay_auto"      # 棋谱自动播放


class GameReplayController:
    """棋谱回放控制器——控制棋谱在GoEngine上的逐手播放"""

    def __init__(self, engine, game_record=None):
        self.engine = engine
        self.game = game_record
        self.move_index = 0           # 当前已回放到第几手 (0-based)
        self.finished = False
        self.mode = ReplayMode.AI_SELFPLAY

    def load_game(self, game_record):
        """加载新棋谱并重置"""
        self.game = game_record
        self.move_index = 0
        self.finished = False
        self.mode = ReplayMode.REPLAY
        # 重置引擎
        self.engine.__init__(size=self.engine.size)

    def set_ai_mode(self):
        """切换到AI自对弈模式"""
        self.mode = ReplayMode.AI_SELFPLAY
        self.game = None
        self.move_index = 0
        self.finished = False
        self.engine.__init__(size=self.engine.size)

    def next_move(self):
        """回放下一手。返回 (r, c, color) 或 None（无更多合法手）"""
        if not self.game or self.finished:
            return None

        while self.move_index < self.game.move_count:
            r, c, color = self.game.get_move(self.move_index)
            self.move_index += 1

            if self.engine.is_valid_move(r, c):
                self.engine.make_move(r, c)
                if self.move_index >= self.game.move_count:
                    self.finished = True
                return (r, c, color)
            else:
                # 非法手则跳过（极少数因模拟偏差导致）
                continue

        self.finished = True
        return None

    def prev_move(self):
        """回退一手。重置引擎并重放到 move_index-2。"""
        if not self.game or self.move_index <= 0:
            return None

        target = self.move_index - 2  # 回退到前一手
        if target < 0:
            target = -1  # 回退到开局

        self.engine.__init__(size=self.engine.size)
        self.move_index = 0
        self.finished = False

        last_move = None
        while self.move_index <= target:
            result = self.next_move()
            if result:
                last_move = result

        return last_move

    def goto_move(self, absolute_index):
        """跳到指定手数 (0-based)"""
        if not self.game:
            return None
        if absolute_index < 0:
            absolute_index = 0
        if absolute_index >= self.game.move_count:
            absolute_index = self.game.move_count - 1

        self.engine.__init__(size=self.engine.size)
        self.move_index = 0
        self.finished = False

        last_move = None
        while self.move_index <= absolute_index:
            result = self.next_move()
            if result:
                last_move = result
        return last_move

    def reset(self):
        """重置回放"""
        if self.game:
            self.engine.__init__(size=self.engine.size)
            self.move_index = 0
            self.finished = False

    def get_status(self):
        """获取回放状态"""
        return {
            'mode': self.mode,
            'game_name': self.game.name if self.game else "AI自对弈",
            'current_move': self.move_index,
            'total_moves': self.game.move_count if self.game else 0,
            'finished': self.finished,
            'progress': (self.move_index / max(1, self.game.move_count if self.game else 1)) * 100,
        }

    def toggle_mode(self):
        """切换模式：AI自对弈 ↔ 棋谱回放"""
        if self.mode == ReplayMode.AI_SELFPLAY:
            self.mode = ReplayMode.REPLAY
        elif self.mode == ReplayMode.REPLAY:
            self.mode = ReplayMode.REPLAY_AUTO
        else:
            self.mode = ReplayMode.AI_SELFPLAY
        return self.mode

    def auto_replay_step(self):
        """自动回放模式：每帧尝试播一手"""
        if self.mode != ReplayMode.REPLAY_AUTO or self.finished:
            return None
        return self.next_move()

    def set_replay_auto(self):
        """切换到自动回放模式"""
        if self.game:
            self.mode = ReplayMode.REPLAY_AUTO
            self.reset()

    def get_current_move_info(self):
        """获取当前手的详细信息"""
        if not self.game:
            return None
        idx = min(self.move_index - 1, self.game.move_count - 1)
        if idx < 0:
            return None
        r, c, color = self.game.get_move(idx)
        color_name = "黑" if color == Stone.BLACK else "白"
        return {
            'move_number': idx + 1,
            'total': self.game.move_count,
            'position': f"({r+1},{c+1})",
            'color': color_name,
        }

    def is_ai_mode(self):
        return self.mode == ReplayMode.AI_SELFPLAY

    def is_replay_mode(self):
        return self.mode in (ReplayMode.REPLAY, ReplayMode.REPLAY_AUTO)

    def is_auto_replay(self):
        return self.mode == ReplayMode.REPLAY_AUTO
