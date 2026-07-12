"""
SGF 棋谱解析器
==============
解析标准 SGF FF[4] 格式，转换为内部 GameRecord。
支持: 坐标解析、注释提取、节点标注、变化分支。
"""

import re
import logging
from typing import Optional

log = logging.getLogger("sgf_parser")

# SGF 列/行标签 (19路，a-s 含 i，共19字母 per SGF FF[4])
SGF_LETTERS = "abcdefghijklmnopqrs"
SGF_LETTERS_UPPER = SGF_LETTERS.upper()


# ─── 坐标转换 ──────────────────────────────

def sgf_letter_to_index(ch: str) -> int:
    """单个 SGF 字母 → 0-based 索引"""
    ch = ch.lower()
    try:
        return SGF_LETTERS.index(ch)
    except ValueError:
        raise ValueError(f"无效的 SGF 字母: '{ch}'，合法值: a-s (跳过 i)")


def parse_sgf_coord(coord: str, board_size: int = 19) -> Optional[tuple]:
    """解析单个 SGF 坐标 → 引擎 (row, col)，pass 返回 None。

    支持三种格式:
      - 字母+数字: 'Q16'  (引擎内部格式)
      - 字母+字母: 'qd'   (标准 SGF)
      - 空或 'tt': pass
    """
    coord = coord.strip()
    if not coord or coord.lower() in ('pass', 'tt', ''):
        return None  # pass

    # 格式1: 字母+数字 (如 Q16, d4)
    m = re.match(r'^([A-Za-z])(\d+)$', coord)
    if m:
        col = sgf_letter_to_index(m.group(1))
        num = int(m.group(2))
        if num < 1 or num > board_size:
            raise ValueError(f"SGF 行号越界: {num} (棋盘 {board_size}路)")
        row = board_size - num  # 数字1=底行
        return (row, col)

    # 格式2: 字母+字母 (如 qd, QD, pi)
    m = re.match(r'^([A-Za-z])([A-Za-z])$', coord)
    if m:
        col = sgf_letter_to_index(m.group(1))
        row = sgf_letter_to_index(m.group(2))
        if col >= board_size or row >= board_size:
            raise ValueError(f"SGF 坐标越界: {coord} (棋盘 {board_size}路)")
        return (row, col)

    raise ValueError(f"无法解析 SGF 坐标: '{coord}'，期望格式如 Q16 或 qd")


def coord_to_sgf_ll(r: int, c: int) -> str:
    """引擎 (r,c) → 标准 SGF 双字母坐标，如 (3,15) → 'qd'"""
    return f"{SGF_LETTERS[c]}{SGF_LETTERS[r]}"


# ─── SGF 词法分析 ──────────────────────────

def _tokenize(sgf_text: str):
    """将 SGF 文本分解为 token 流: '(' ')' ';' PropertyName '[' Value ']'"""
    # 移除注释和空白
    text = sgf_text.strip()

    tokens = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch in '();':
            tokens.append(ch)
            i += 1
            continue

        if ch.isspace():
            i += 1
            continue

        # Property name: 大写字母序列
        if ch.isupper():
            name_start = i
            while i < n and text[i].isupper():
                i += 1
            tokens.append(('PROP', text[name_start:i]))
            continue

        # Property value: [ ... ]
        if ch == '[':
            i += 1
            val_chars = []
            depth = 1
            while i < n and depth > 0:
                if text[i] == '\\' and i + 1 < n:
                    # 转义字符
                    i += 1
                    val_chars.append(text[i])
                elif text[i] == '[':
                    depth += 1
                    val_chars.append(text[i])
                elif text[i] == ']':
                    depth -= 1
                    if depth > 0:
                        val_chars.append(text[i])
                else:
                    val_chars.append(text[i])
                i += 1
            tokens.append(('VAL', ''.join(val_chars)))
            continue

        # 跳过无法识别的字符
        i += 1

    return tokens


# ─── SGF 解析核心 ──────────────────────────

class SGFNode:
    """SGF 节点树节点"""

    def __init__(self):
        self.props: dict[str, list[str]] = {}  # 属性名 → 值列表
        self.children: list['SGFNode'] = []     # 子节点（分支）

    def get(self, prop: str, default=None):
        """获取属性第一个值"""
        values = self.props.get(prop)
        return values[0] if values else default

    def get_all(self, prop: str) -> list[str]:
        """获取属性所有值"""
        return self.props.get(prop, [])

    def has(self, prop: str) -> bool:
        return prop in self.props


def _parse_node_tree(tokens: list, start: int = 0) -> tuple[SGFNode, int]:
    """从 token 流解析一棵节点树，返回 (根节点, 下一个 token 索引)"""
    i = start
    n = len(tokens)

    if i >= n or tokens[i] != '(':
        raise ValueError(f"期望 '(' 开始节点树，实际 token: {tokens[i] if i < n else 'EOF'}")

    i += 1  # 跳过 '('

    root = SGFNode()
    current = root
    node_stack = []  # 用于处理变化的父节点栈

    while i < n:
        token = tokens[i]

        if token == '(':
            # 新子树（变化分支）
            child, next_i = _parse_node_tree(tokens, i)
            current.children.append(child)
            i = next_i
            continue

        if token == ')':
            i += 1
            return root, i

        if token == ';':
            # 新节点
            new_node = SGFNode()
            current.children.append(new_node)
            current = new_node
            i += 1
            continue

        if isinstance(token, tuple) and token[0] == 'PROP':
            prop_name = token[1]
            i += 1

            # 收集该属性的所有值
            values = []
            while i < n and isinstance(tokens[i], tuple) and tokens[i][0] == 'VAL':
                values.append(tokens[i][1])
                i += 1

            if prop_name in current.props:
                current.props[prop_name].extend(values)
            else:
                current.props[prop_name] = values
            continue

        # 理论上不应到达这里
        i += 1

    return root, i


def _extract_moves_and_annotations(root: SGFNode, board_size: int):
    """从节点树中提取走法序列和注解。

    遍历主变（第一个子节点链），收集:
      - moves: 交替黑白走法列表
      - comments: {move_index: comment_text}
      - node_names: {move_index: node_name}
      - key_moves: {move_index: label}

    返回: (moves, comments, node_names, key_moves)
    """
    moves = []
    comments = {}
    node_names = {}
    key_moves = {}

    # 主变 = 沿 first-child 链一路向下遍历
    node = root
    move_idx = 0  # 0-based move counter

    while True:
        # 找到主变上的下一个节点（第一个子节点）
        if not node.children:
            break
        node = node.children[0]

        # 提取走法
        b_moves = node.get_all('B')
        w_moves = node.get_all('W')

        for mv in b_moves:
            coord = parse_sgf_coord(mv, board_size)
            moves.append(coord if coord else (-1, -1))
            _extract_annotations_from_node(node, move_idx, comments, node_names, key_moves)
            move_idx += 1

        for mv in w_moves:
            coord = parse_sgf_coord(mv, board_size)
            moves.append(coord if coord else (-1, -1))
            _extract_annotations_from_node(node, move_idx, comments, node_names, key_moves)
            move_idx += 1

    return moves, comments, node_names, key_moves


def _extract_annotations_from_node(node: SGFNode, move_idx: int,
                                    comments: dict, node_names: dict,
                                    key_moves: dict):
    """从节点属性中提取注解"""
    # SGF 注释 C[...]
    comment = node.get('C')
    if comment:
        comments[move_idx] = comment

    # 节点名称 N[...] 常用于标注妙手
    name = node.get('N')
    if name:
        node_names[move_idx] = name
        # 如果名称包含"妙手""好手""鹰"等关键词，自动标记
        if any(kw in name for kw in ['妙手', '好手', '神之一手', '鹰', '名手',
                                       'Good', 'Excellent', 'Brilliant']):
            key_moves[move_idx] = name


def _extract_variations(root: SGFNode, board_size: int) -> dict:
    """提取分支变化: {主变move_index: [[(r,c),...], ...]}"""
    # 简化版：暂不处理分支，留给后续扩展
    return {}


# ─── 公共接口 ──────────────────────────────

class SGFFile:
    """解析后的 SGF 文件表示"""

    def __init__(self, sgf_text: str):
        self.raw = sgf_text
        self.board_size = 19
        self.black_player = "未知"
        self.white_player = "未知"
        self.result = ""
        self.komi = 0.0
        self.game_name = ""
        self.date = ""
        self.event = ""
        self.place = ""
        self.round = ""
        self.moves: list[tuple] = []           # [(r,c), ...] 黑先交替, (-1,-1)=pass
        self.comments: dict[int, str] = {}     # {move_index: text}
        self.node_names: dict[int, str] = {}   # {move_index: name}
        self.key_moves: dict[int, str] = {}    # {move_index: label}
        self.variations: dict = {}

        self._parse(sgf_text)

    def _parse(self, sgf_text: str):
        tokens = _tokenize(sgf_text)
        root, _ = _parse_node_tree(tokens, 0)

        if not root.children:
            raise ValueError("SGF 文件为空（无棋局节点）")

        game_node = root  # 根节点的第一个子节点是游戏信息节点

        # 解析游戏信息（在根节点或其第一个子节点中）
        info_node = root
        if root.children:
            info_node = root.children[0]

        self.black_player = info_node.get('PB', '未知')
        self.white_player = info_node.get('PW', '未知')
        self.result = info_node.get('RE', '')
        self.game_name = info_node.get('GN', '')
        self.date = info_node.get('DT', '')
        self.event = info_node.get('EV', '')
        self.place = info_node.get('PC', '')
        self.round = info_node.get('RO', '')

        # 棋盘大小
        sz_str = info_node.get('SZ', '19')
        try:
            self.board_size = int(sz_str)
        except ValueError:
            self.board_size = 19

        # 贴目
        km_str = info_node.get('KM', '0')
        try:
            self.komi = float(km_str)
        except ValueError:
            self.komi = 0.0

        # 提取走法和注解
        self.moves, self.comments, self.node_names, self.key_moves = \
            _extract_moves_and_annotations(info_node, self.board_size)

        # 提取分支
        self.variations = _extract_variations(info_node, self.board_size)

    @property
    def move_count(self):
        return len(self.moves)

    def to_game_record(self, game_id: int = 0) -> 'GameRecord':
        """转换为内部 GameRecord 格式"""
        from game_library import GameRecord

        # 从注释/节点名推断风格标签
        style_tags = []
        if self.event:
            style_tags.append(self.event)
        if 'AlphaGo' in f"{self.black_player}{self.white_player}":
            style_tags.append('AI')

        return GameRecord(
            game_id=game_id,
            name=self.game_name or f"{self.black_player} vs {self.white_player}",
            subtitle=f"{self.black_player} vs {self.white_player}"
                     f"{' · ' + self.date if self.date else ''}"
                     f"{' · ' + self.event if self.event else ''}",
            description=self._build_description(),
            moves=self.moves,
            board_size=self.board_size,
            style_tags=style_tags,
            year=self.date[:4] if len(self.date) >= 4 else "",
            players={"黑": self.black_player, "白": self.white_player},
            key_moves=self.key_moves,
            annotations=self.comments,
        )

    def _build_description(self) -> str:
        parts = []
        if self.event:
            parts.append(f"赛事: {self.event}")
        if self.date:
            parts.append(f"日期: {self.date}")
        if self.place:
            parts.append(f"地点: {self.place}")
        if self.result:
            parts.append(f"结果: {self.result}")
        parts.append(f"总手数: {len(self.moves)}手")

        # 添加关键手概要
        if self.key_moves:
            km_summary = []
            for idx, label in sorted(self.key_moves.items()):
                km_summary.append(f"第{idx+1}手「{label}」")
            parts.append(f"关键手: {' · '.join(km_summary)}")

        return "\n".join(parts)


def parse_sgf_text(sgf_text: str) -> SGFFile:
    """解析 SGF 文本字符串，返回 SGFFile 对象"""
    # 预处理：移除 BOM、首尾空白
    sgf_text = sgf_text.strip()
    if sgf_text.startswith('\ufeff'):
        sgf_text = sgf_text[1:]

    return SGFFile(sgf_text)


def parse_sgf_file(filepath: str) -> SGFFile:
    """从文件路径解析 SGF，自动探测编码"""
    import logging
    log = logging.getLogger(__name__)
    sgf_text = None
    for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                sgf_text = f.read()
            log.info(f"SGF 文件以 {enc} 编码解码成功: {filepath}")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if sgf_text is None:
        raise UnicodeDecodeError(f"无法解码 SGF 文件: {filepath}，已尝试 utf-8/gbk/gb2312/gb18030/latin-1")
    return parse_sgf_text(sgf_text)
