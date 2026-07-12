"""
LLM 棋手 — 接入大语言模型下围棋
==================================
通过 OpenAI 兼容 API 驱动围棋 AI agent，
每个 agent 有独特的棋风与角色设定。
"""

import os
import re
import json
import logging
from typing import Optional

import httpx

log = logging.getLogger("llm_player")

# ─── 坐标转换 ──────────────────────────────────

COLS = "ABCDEFGHJKLMNOPQRST"  # 19 columns, skip I

def to_sgf(r: int, c: int) -> str:
    """引擎坐标 → SGF 坐标 (e.g. (3,15) → 'Q16')"""
    return f"{COLS[c]}{19 - r}"

def to_numeric(r: int, c: int) -> str:
    """引擎坐标 → 数字坐标 (e.g. (3,15) → '16-4'，列号-行号)"""
    return f"{c + 1}-{19 - r}"

def from_sgf(sgf: str) -> tuple:
    """SGF 坐标 → 引擎坐标 (e.g. 'Q16' → (3,15))"""
    sgf = sgf.strip().upper()
    if sgf == "PASS":
        return None
    m = re.match(r'^([A-HJ-Z])(\d+)$', sgf)
    if not m:
        raise ValueError(f"无法解析坐标: {sgf}")
    col = COLS.index(m.group(1))
    row = 19 - int(m.group(2))
    return row, col


# ─── 棋盘渲染 ──────────────────────────────────

STONE_CHARS = {0: ".", 1: "@", 2: "O"}

def render_board(engine) -> str:
    """将引擎棋盘渲染为紧凑文本（带列标签，单字符）"""
    lines = []
    # 列标签
    col_labels = " ".join(COLS)
    leading = "   "  # 行号占位
    lines.append(f"{leading}{col_labels}")
    # 分隔线
    lines.append(f"{leading}{'─' * 37}")
    for r in range(19):
        row_chars = " ".join(STONE_CHARS[engine.board[r][c]] for c in range(19))
        lines.append(f"{19 - r:2d} {row_chars}")
    return "\n".join(lines)


def render_move_history(engine, max_moves: int = 80) -> str:
    """渲染最近 N 手棋的历史"""
    history = engine.move_history
    if not history:
        return "（尚无落子）"

    # 只取最近 max_moves 手
    if len(history) > max_moves:
        history = history[-max_moves:]
        parts = ["...（省略了前面的棋步）"]
    else:
        parts = []

    for i, (r, c, color) in enumerate(history):
        color_name = "黑" if color == 1 else "白"
        coord = to_sgf(r, c)
        parts.append(f"{i + 1:3d}. {color_name} {coord}")

    return "\n".join(parts)


# ─── 棋盘战术分析 ──────────────────────────────

def analyze_board(engine, my_color: int) -> str:
    """生成棋盘的战术分析摘要，帮助 LLM 理解局面关键点"""
    opp_color = 3 - my_color
    my_name = "黑" if my_color == 1 else "白"
    opp_name = "白" if my_color == 1 else "黑"
    lines = []

    # 1. 收集所有棋筋组的统计信息
    def get_all_groups(color):
        """返回所有组的 [(stones, liberties_set, group_size, lib_count), ...]"""
        visited = set()
        groups = []
        for r in range(19):
            for c in range(19):
                if engine.board[r][c] == color and (r, c) not in visited:
                    g = engine.get_group(r, c)
                    visited.update(g)
                    libs = engine.get_liberties(g)
                    groups.append((g, libs, len(g), len(libs)))
        return groups

    my_groups = get_all_groups(my_color)
    opp_groups = get_all_groups(opp_color)

    # 2. 危险信号：气数 ≤ 2 的组
    my_danger = [(g, sz, nc) for g, _, sz, nc in my_groups if nc <= 2]
    opp_danger = [(g, sz, nc) for g, _, sz, nc in opp_groups if nc <= 2]

    lines.append("【战术警报】")
    if my_danger:
        for g, sz, nc in my_danger:
            stones = sorted(g)[:3]
            coords = [to_sgf(r, c) for r, c in stones]
            extra = f"等{sz}子" if sz > 3 else ""
            lines.append(f"  ⚠ 我({my_name})有险棋！{', '.join(coords)}{extra} 仅有{nc}气，需要立即关注")
    if opp_danger:
        for g, sz, nc in opp_danger:
            stones = sorted(g)[:3]
            coords = [to_sgf(r, c) for r, c in stones]
            extra = f"等{sz}子" if sz > 3 else ""
            if nc == 1:
                lines.append(f"  🎯 可提！对方({opp_name}){', '.join(coords)}{extra} 仅剩1气，可以立即提吃！")
            else:
                lines.append(f"  💡 对方({opp_name}){', '.join(coords)}{extra} 仅剩{nc}气，可考虑攻击收紧")
    if not my_danger and not opp_danger:
        lines.append("  当前无紧急死活问题")

    # 3. 提子统计
    bc = engine.captures.get(1, 0)
    wc = engine.captures.get(2, 0)
    if bc + wc > 0:
        lines.append(f"\n【提子累计】黑提白 {bc} 子 | 白提黑 {wc} 子")

    # 4. 地盘粗略估算
    black_stones = int((engine.board == 1).sum())
    white_stones = int((engine.board == 2).sum())
    lines.append(f"\n【兵力统计】棋子数：黑 {black_stones} vs 白 {white_stones}")

    # 粗略地盘：按 3×3 网格分区统计
    regions = [
        ("左下", 13, 19, 0, 6),
        ("下边", 13, 19, 6, 13),
        ("右下", 13, 19, 13, 19),
        ("左边", 6, 13, 0, 6),
        ("中腹", 6, 13, 6, 13),
        ("右边", 6, 13, 13, 19),
        ("左上", 0, 6, 0, 6),
        ("上边", 0, 6, 6, 13),
        ("右上", 0, 6, 13, 19),
    ]
    dominance_lines = []
    for name, r1, r2, c1, c2 in regions:
        area = engine.board[r1:r2, c1:c2]
        b_cnt = int((area == 1).sum())
        w_cnt = int((area == 2).sum())
        if b_cnt > w_cnt + 2:
            dom = "黑优"
        elif w_cnt > b_cnt + 2:
            dom = "白优"
        elif b_cnt > 0 or w_cnt > 0:
            dom = "均衡"
        else:
            dom = "空白"
        dominance_lines.append(f"  {name}: {dom}(黑{b_cnt}vs白{w_cnt})")
    lines.append(f"\n【区域态势】\n" + "\n".join(dominance_lines))

    # 5. 角部状态
    corners = [
        ("左上角", (0,0), [(0,0),(0,2),(2,0),(2,2)]),
        ("右上角", (0,18), [(0,18),(0,16),(2,18),(2,16)]),
        ("左下角", (18,0), [(18,0),(18,2),(16,0),(16,2)]),
        ("右下角", (18,18), [(18,18),(18,16),(16,18),(16,16)]),
    ]
    corner_status = []
    for name, (cr, cc), checks in corners:
        b_near = sum(1 for r, c in checks if engine.board[r][c] == 1)
        w_near = sum(1 for r, c in checks if engine.board[r][c] == 2)
        if engine.board[cr][cc] == 1:
            corner_status.append(f"  {name}: 黑占角")
        elif engine.board[cr][cc] == 2:
            corner_status.append(f"  {name}: 白占角")
        elif b_near > w_near:
            corner_status.append(f"  {name}: 空角(黑近)")
        elif w_near > b_near:
            corner_status.append(f"  {name}: 空角(白近)")
        else:
            corner_status.append(f"  {name}: 空角")
    lines.append(f"\n【四角态势】\n" + "\n".join(corner_status))

    # 6. 最近关键手
    if engine.move_history:
        recent_count = min(10, len(engine.move_history))
        recent = engine.move_history[-recent_count:]
        moves_str = " → ".join(
            f"{'黑' if clr==1 else '白'}{to_sgf(r,c)}"
            for r, c, clr in recent
        )
        lines.append(f"\n【最近{recent_count}手】\n  {moves_str}")

    return "\n".join(lines)



# ─── 提示词模板 ────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """你是 {name}，一位真正的围棋高手，拥有深厚的围棋理论基础和丰富的实战经验。你正在与对手进行正式 19 路围棋对局。

【你的棋风】{style_description}

{style_guidance}

【围棋核心原则】
- 围棋在 19×19 路棋盘上进行，黑先白后，交替落子
- 棋子的"气"是相邻的空交叉点，无气之子将被提走
- 禁止全局同形再现（打劫规则）
- 每手棋都应有明确战略意图：取地、取势、攻击、防守、扩张、侵消、收官

【布局阶段 · 前30手 — 搭建骨架】
- 占空角为先：星位（四四）、小目（三四/四三）、三三、目外（三五/五三）、高目（五四/四五）
- 守角与挂角：占角后顺势缔角巩固实地，或挂角限制对方角部发展
- 拆边定形：沿边展开棋子，二间拆（间距2格）紧密，三间拆（间距3格）均衡，四间拆（间距4格）快速但需注意打入
- 避免布局阶段过早深入对方势力范围孤军作战
- 全局平衡：棋盘四角与四条边都应纳入考量，不可偏废一隅

【中盘阶段 · 战斗与取舍】
- 寻找对方棋形的薄弱环节（气紧、眼位不足、联络不完整）
- 攻击的终极目的是获利而非杀棋：通过攻击围空、强化自身、压缩对方
- 轻处理孤棋：被攻击时以轻盈的腾挪姿态处理，不轻易下重
- 厚势的价值：外势可以用来扩张中腹、攻击对方、接应己方孤棋
- 先手与后手：抢占先手意味着主导权，后手则需确保获利足够补偿
- 模样的消长：扩张自己模样的同时限制对方模样的膨胀

【官子阶段 · 精确收官】
- 按价值从大到小依次定型：双方先手 > 单方先手 > 双方后手
- 边界定型时注意留有余地，防止对方逆收
- 每一目都要精打细算

【落子前的思考框架】
请先思考以下问题再决定落子位置：
1. 当前局面的焦点区域在哪里？（哪块棋最紧急？）
2. 双方各有哪些薄弱之处？哪些需要补强？哪些可以攻击？
3. 纵观全局，哪里价值最大？（按"大场 > 急所 > 普通"排序）
4. 所选落子是否符合我的棋风定位？

【输出格式 — 严格遵守】
先用 2-3 句话分析当前全局形势（不要逐格描述棋盘），然后用单独一行输出落子：
MOVE: <字母><数字>
- 字母是列（A-T，跳过I），数字是行（1-19，棋盘底部为1）
- 例：MOVE: Q16 表示第 Q 列（即第15列）第 16 行
- 弃权时输出：MOVE: PASS"""

STYLE_GUIDANCE_MAP = {
    "古风稳重": """【古风稳重风格 — 贯彻要点】
- 布局优先占角，重实地与厚势的平衡
- 走厚自身再图发展，不走无把握的棋
- 对手挑衅时以厚势从容应对，避免被动应战
- 选择经典定式，不冒险走无理手
- 形势判断务实，优势简明定型，劣势耐心周旋等待机会""",
    "宇宙流": """【宇宙流风格 — 贯彻要点】
- 不拘泥角部实地，以四线以上高位落子构建中腹大模样
- 从两面张开压迫对手在低位，扩大中央潜力
- 对手打入模样时，通过攻击外围获得利益
- 中腹的"面"优先于边角的"点"
- 重视全局的呼应而非局部的得失""",
    "实地派": """【实地派风格 — 贯彻要点】
- 开局快速抢占角部实地，三线落子优先
- 先取实地后治孤，不惧对方外势
- 敢于打入对方模样，擅长在小空间做活
- 每一步追求实地回报
- 官子精准，锱铢必较""",
    "力战派": """【力战派风格 — 贯彻要点】
- 主动寻找战斗，攻击对方薄弱处
- 不回避复杂局面，擅长劫争与死活
- 以攻击获利为核心手段
- 抓住对手缓手或无理手立刻反击
- 计算深远：既要算自己的手段也要预判对手的最强应对""",
    "均衡流": """【均衡流风格 — 贯彻要点】
- 根据局势灵活应变，不固守一种策略
- 实地与势力动态平衡
- 优势时简化局面，劣势时积极求变
- 注重全局配合与棋子效率
- 每手棋都放在全局最大处""",
}

DEFAULT_GUIDANCE = """【通用策略】
- 先判断全局形势再选择落点
- 落子前确认该点有明确战略目的
- 参考经典棋形与定式，保持棋子的效率"""


# ─── 棋谱参考映射（9×9 → 19×19 缩放）─────────────────
# 棋谱中每个 (r, c) 是 9×9 坐标，通过 r*2+1, c*2+1 映射到 19×19
# 只取前 12 手作为开局参考，避免信息过载

def _scale_moves_9to19(moves_9x9, max_moves=12):
    """将 9×9 棋谱坐标缩放到 19×19，只取前 max_moves 手"""
    scaled = []
    for r, c in moves_9x9[:max_moves]:
        r19 = r * 2 + 1  # 0→1, 4→9, 8→17
        c19 = c * 2 + 1
        scaled.append((r19, c19))
    return scaled


def _format_reference_moves(moves_19x19):
    """将参考棋谱手数格式化为 SGF 坐标字符串"""
    lines = []
    for i, (r, c) in enumerate(moves_19x19):
        color = "黑" if i % 2 == 0 else "白"
        lines.append(f"  {i+1:2d}. {color} {to_sgf(r, c)}")
    return "\n".join(lines)


# 风格 → 参考棋谱映射（来自内置棋谱库的前12手，已缩放至19×19）
STYLE_GAME_REFERENCES = {
    "古风稳重": [
        {
            "name": "耳赤之曲（秀策 vs 幻庵因硕 · 1846）",
            "desc": "秀策流经典布局：占角坚实、定式厚重、不急不躁",
            "moves": _scale_moves_9to19([
                (6, 6), (2, 2), (4, 6), (4, 2), (6, 4), (2, 4),
                (5, 3), (3, 5), (7, 5), (1, 3), (5, 5), (3, 3),
            ]),
        },
        {
            "name": "石佛（李昌镐 vs 马晓春 · 1996）",
            "desc": "李昌镐式均衡布局：步步精准、后发先至、官子决胜",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (1, 1), (7, 7), (4, 4), (2, 6),
                (3, 2), (5, 6), (2, 3), (6, 5), (4, 1), (4, 7),
            ]),
        },
    ],
    "宇宙流": [
        {
            "name": "宇宙流（武宫正树 vs 加藤正夫 · 1978）",
            "desc": "武宫宇宙流：高位展开、构建中央大模样、取势弃地",
            "moves": _scale_moves_9to19([
                (4, 4), (2, 2), (6, 2), (2, 6), (4, 2), (4, 6),
                (3, 4), (5, 4), (5, 2), (3, 6), (6, 3), (2, 5),
            ]),
        },
        {
            "name": "新布局革命（吴清源 vs 秀哉 · 1933）",
            "desc": "吴清源新布局：星位+三三+天元，打破传统、自由奔放",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (1, 1), (6, 2), (4, 4), (2, 6),
                (2, 4), (6, 4), (3, 3), (5, 5), (1, 3), (7, 5),
            ]),
        },
    ],
    "实地派": [
        {
            "name": "剃刀（坂田荣男 vs 藤泽秀行 · 1963）",
            "desc": "坂田实地派：锐利切割、快速抢占实地、局部精准",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (4, 4), (2, 6), (6, 2), (4, 6),
                (3, 3), (5, 5), (2, 4), (6, 4), (3, 2), (5, 6),
            ]),
        },
        {
            "name": "斗魂（赵治勋 vs 小林光一 · 1983）",
            "desc": "赵治勋治孤：先取实地后治孤、在三线扎根、不惧外势",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (1, 1), (7, 7), (2, 4), (6, 4),
                (4, 2), (4, 6), (3, 1), (5, 7), (1, 3), (7, 5),
            ]),
        },
    ],
    "力战派": [
        {
            "name": "天煞星（加藤正夫 vs 林海峰 · 1977）",
            "desc": "加藤力战：主动攻击、寸土不让、抓住对手弱点猛烈攻击",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (6, 2), (2, 6), (1, 1), (7, 7),
                (3, 2), (5, 6), (2, 3), (6, 5), (4, 2), (4, 6),
            ]),
        },
        {
            "name": "斗魂（赵治勋 vs 小林光一 · 1983）",
            "desc": "赵治勋斗魂：绝境逆转、顽强治孤、在战斗中求生",
            "moves": _scale_moves_9to19([
                (2, 2), (6, 6), (1, 1), (7, 7), (2, 4), (6, 4),
                (4, 2), (4, 6), (3, 1), (5, 7), (1, 3), (7, 5),
            ]),
        },
    ],
    "均衡流": [
        {
            "name": "中日擂台赛（聂卫平 vs 小林光一 · 1985）",
            "desc": "聂卫平大局流：全局均衡、形势判断准确、不畏强敌",
            "moves": _scale_moves_9to19([
                (4, 4), (2, 2), (6, 6), (2, 6), (4, 2), (4, 6),
                (3, 4), (5, 4), (4, 3), (4, 5), (2, 4), (6, 4),
            ]),
        },
        {
            "name": "耳赤之曲（秀策 vs 幻庵因硕 · 1846）",
            "desc": "秀策均衡流：厚实布局、不急不缓、面面俱到",
            "moves": _scale_moves_9to19([
                (6, 6), (2, 2), (4, 6), (4, 2), (6, 4), (2, 4),
                (5, 3), (3, 5), (7, 5), (1, 3), (5, 5), (3, 3),
            ]),
        },
    ],
}


# ─── LLM 棋手 ──────────────────────────────────

class LLMPlayer:
    """由大语言模型驱动的围棋 AI 棋手"""

    def __init__(
        self,
        color: int,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        name: str = "棋手",
        style: str = "均衡流",
        style_description: str = "",
        timeout: int = 30,
        temperature: float = 0.7,
    ):
        self.color = color
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name
        self.style = style
        self.style_description = style_description or f"擅长{style}风格的下法"
        self.timeout = timeout
        self.temperature = temperature
        self.move_count = 0

        # 最近一次 LLM 调用的请求/响应（供外部读取后通过 WebSocket 展示）
        self.last_llm_request: dict = {}
        self.last_llm_response: str = ""

        # 棋谱参考（开局阶段注入 prompt）
        self.opening_references = STYLE_GAME_REFERENCES.get(style, [])

        guidance = STYLE_GUIDANCE_MAP.get(style, DEFAULT_GUIDANCE)
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            name=name,
            style_description=self.style_description,
            style_guidance=guidance,
        )

    # ─── 公共接口 ──────────────────────────────

    def get_move(self, engine) -> tuple:
        """LLM 分析并返回落子坐标 (r, c)，含落子质量检查"""
        legal = engine.get_legal_moves()
        if not legal:
            log.warning(f"[{self.name}] 没有合法着点 → PASS")
            return (-1, -1)

        user_prompt = self._build_prompt(engine, legal)

        # 第一遍 LLM 调用
        move_str = self._call_llm(user_prompt)

        if move_str is None:
            log.warning(f"[{self.name}] LLM 未返回有效坐标，使用回退着法")
            return self._fallback_move(engine, legal)

        if move_str.upper() == "PASS":
            log.info(f"[{self.name}] 选择 PASS")
            return (-1, -1)

        try:
            r, c = from_sgf(move_str)
            if not engine.is_valid_move(r, c):
                log.warning(f"[{self.name}] LLM 返回非法着点 {move_str}，改用启发式")
                return self._fallback_move(engine, legal)

            # ── 落子质量检查 ──
            quality_issue = self._check_move_quality(engine, r, c)
            if quality_issue:
                log.warning(f"[{self.name}] 落子质量警告: {move_str} → {quality_issue}")
                retry_prompt = (
                    f"{user_prompt}\n\n"
                    f"⚠️ 请注意：你上次选择的 MOVE: {move_str} 存在问题：{quality_issue}\n"
                    f"请重新分析局面，选择更合理的落子位置。"
                )
                retry_move = self._call_llm(retry_prompt)
                if retry_move and retry_move.upper() != "PASS":
                    try:
                        r2, c2 = from_sgf(retry_move)
                        if engine.is_valid_move(r2, c2):
                            issue2 = self._check_move_quality(engine, r2, c2)
                            if not issue2:
                                log.info(f"[{self.name}] 重试后选择 {retry_move} (通过质量检查)")
                                return r2, c2
                            else:
                                log.warning(f"[{self.name}] 重试后仍有问题 ({retry_move}:{issue2})，接受原选择")
                    except ValueError:
                        pass
                log.info(f"[{self.name}] 重试未给出更好选择，接受原选择 {move_str}")

            self.move_count += 1
            log.info(f"[{self.name}] 落子 {move_str} ({r},{c}) 第{self.move_count}手")
            return r, c
        except (ValueError, IndexError):
            log.warning(f"[{self.name}] 无法解析 '{move_str}'，改用启发式")
            return self._fallback_move(engine, legal)

    def _check_move_quality(self, engine, r: int, c: int) -> str:
        """检查落子是否有明显问题。返回描述字符串或空字符串"""
        my_color = self.color
        opp_color = 3 - my_color

        # 模拟落子
        engine.board[r][c] = my_color

        # 检查1: 落子后自己是否有气
        my_group = engine.get_group(r, c)
        my_libs = engine.get_liberties(my_group)
        if len(my_libs) == 0:
            engine.board[r][c] = 0
            return "落子后自身无气（自杀），这不应该被 is_valid_move 允许，可能是引擎bug"
        if len(my_libs) == 1 and len(my_group) > 1:
            engine.board[r][c] = 0
            return f"落子后整块棋（{len(my_group)}子）只剩1气，等于把自己送入被打吃的险境"

        # 检查2: 是否把棋下在对方厚势的包围圈里（四面都是对手且无发展空间）
        opp_neighbors = 0
        empty_neighbors = 0
        my_neighbors = 0
        for nr, nc in [(r-1,c),(r+1,c),(r,c-1),(r,c+1)]:
            if 0 <= nr < 19 and 0 <= nc < 19:
                if engine.board[nr][nc] == opp_color:
                    opp_neighbors += 1
                elif engine.board[nr][nc] == 0:
                    empty_neighbors += 1
                elif engine.board[nr][nc] == my_color:
                    my_neighbors += 1

        # 检查3: 是否填入自己的假眼（四面全是自己的棋）
        if empty_neighbors == 0 and opp_neighbors == 0 and my_neighbors == 4:
            engine.board[r][c] = 0
            return "这是填自己的眼位！四面全是自己的棋，放入后无任何外气也无法做眼"

        if opp_neighbors == 4 and empty_neighbors == 0 and my_neighbors == 0:
            engine.board[r][c] = 0
            return "四面全被对方包围且无己方接应，这颗子进入后仅剩的气会被立刻收紧"

        # 检查4: 是否错过明显的提子机会（如果存在对方的1气组却没走）
        # 已经通过 analyze_board 提示了，这里只做 soft check

        engine.board[r][c] = 0
        return ""  # 通过检查

    # ─── 内部方法 ──────────────────────────────

    def _build_prompt(self, engine, legal_moves: list) -> str:
        """构建发给 LLM 的提示 — 提供完整局面 + 战术分析，让 LLM 做出专业决策"""
        board_text = render_board(engine)
        history_text = render_move_history(engine, max_moves=50)
        analysis_text = analyze_board(engine, self.color)

        color_name = "黑" if self.color == 1 else "白"
        color_symbol = "@(黑)" if self.color == 1 else "O(白)"
        opp_name = "白" if self.color == 1 else "黑"
        opp_symbol = "O(白)" if self.color == 1 else "@(黑)"

        # 判断棋局阶段
        total_moves = len(engine.move_history)
        if total_moves < 30:
            phase = "布局阶段 — 以占角、守角/挂角、拆边构建骨架为核心任务。优先考虑空角与未安定的角部。"
        elif total_moves < 130:
            phase = "中盘阶段 — 攻击、防守、扩张、侵消，在全局对抗中争取主动。关注薄弱棋组与大场。"
        else:
            phase = "收官阶段 — 按价值从大到小依次定型，精确计算每一手。优先边界定形。"

        # 棋谱摘要
        if engine.move_history:
            last_moves = engine.move_history[-6:]
            recent = []
            for r, c, clr in last_moves:
                tag = "黑" if clr == 1 else "白"
                recent.append(f"{tag}{to_sgf(r, c)}")
            last_opp_info = f"最近6手：{' → '.join(recent)}"
        else:
            last_opp_info = ""

        # 开局参考棋谱（只在布局前半段注入，后半段 LLM 已建立自己的局面理解）
        opening_ref = ""
        if total_moves < 20 and self.opening_references:
            ref_parts = []
            for ref in self.opening_references[:2]:  # 最多 2 个参考棋谱
                formatted = _format_reference_moves(ref["moves"])
                ref_parts.append(
                    f"【参考棋谱 · {ref['name']}】\n"
                    f"理念：{ref['desc']}\n"
                    f"{formatted}"
                )
            if ref_parts:
                opening_ref = (
                    "\n══════════════════════════════════\n"
                    "【开局参考 · 与你棋风匹配的历史名局】\n"
                    "以下是真实历史名局的布局走势（已转换为19路坐标），可作为布局思路的启发参考。\n"
                    "不必逐手照搬，重在汲取其布局理念与定式选择：\n\n"
                    + "\n\n".join(ref_parts) +
                    "\n══════════════════════════════════"
                )

        # 棋盘上已有棋子总数
        stone_count = int((engine.board != 0).sum())
        empty_count = 361 - stone_count

        # 合法着点统计
        legal_gist = f"可选空点共 {len(legal_moves)} 个"
        legal_samples = " | ".join(
            to_sgf(r, c) for r, c in legal_moves[:25]
        )
        if len(legal_moves) > 25:
            legal_samples += f" ... 等{len(legal_moves)}个"

        prompt = f"""当前局面（19×19棋盘，"@"=黑 "O"=白 "."=空；棋盘左下角为 A1）：

{board_text}
{opening_ref}

══════════════════════════════════
{analysis_text}
══════════════════════════════════

当前阶段：{phase}
手数：已落{stone_count}子，剩{empty_count}空点
你执{color_name}（{color_symbol}），对手执{opp_name}（{opp_symbol}）。

{last_opp_info}
可选落点示例（{legal_gist}）：{legal_samples}

══════════════════════════════════
请根据以上战报分析全局形势，然后选择最符合你棋风且最具价值的一手棋。

先用2-3句话评估局面关键点（结合战术警报！有可提的子要优先提吃，有险棋要优先补强），然后单独一行给出落子：
MOVE: <坐标>"""
        return prompt


    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """调用 LLM API，返回解析后的坐标字符串"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 1024,
        }

        # 存储请求信息供外部读取
        self.last_llm_request = {
            "model": self.model,
            "player": self.name,
            "color": "黑" if self.color == 1 else "白",
            "system_prompt": self.system_prompt,
            "user_prompt": user_prompt,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                # 记录原始返回以便排查（调试级别）
                try:
                    raw = data["choices"][0]["message"]["content"].strip()
                except Exception:
                    raw = str(data)

                # 存储原始响应
                self.last_llm_response = raw
                log.info(f"[{self.name}] LLM raw response: {raw[:300]}")

            # 优先提取 'MOVE: <坐标>' 明确指示
            m = re.search(r'MOVE:\s*([A-HJ-Z]+\d+|PASS)', raw, re.IGNORECASE)
            if m:
                parsed = m.group(1).upper()
                log.info(f"[{self.name}] 解析到 MOVE: {parsed}")
                return parsed

            # 若无明确 MOVE 标签，仅在响应中出现单一坐标时接受裸坐标
            all_coords = re.findall(r'([A-HJ-Z]\d+)', raw, re.IGNORECASE)
            if len(all_coords) == 1:
                parsed = all_coords[0].upper()
                log.info(f"[{self.name}] 响应含单一坐标，接受: {parsed}")
                return parsed

            # 检测 PASS 关键词
            if re.search(r'PASS|pass|停手|放弃', raw):
                log.info(f"[{self.name}] 解析到 PASS 表示")
                return "PASS"

            # 多坐标或不明确的响应 -> 返回 None 触发回退策略
            log.warning(f"[{self.name}] 响应不明确（坐标数={len(all_coords)}），使用回退: {raw[:200]}")
            return None
        except httpx.TimeoutException:
            self.last_llm_response = f"[超时] {self.timeout}s"
            log.error(f"[{self.name}] LLM API 超时 ({self.timeout}s)")
            return None
        except httpx.HTTPStatusError as e:
            self.last_llm_response = f"[HTTP {e.response.status_code}] {e.response.text[:300]}"
            log.error(f"[{self.name}] LLM API HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            self.last_llm_response = f"[异常] {e}"
            log.error(f"[{self.name}] LLM API 异常: {e}")
            return None

    def parse_game_text(self, text: str, max_moves: int = 200) -> Optional[list]:
        """使用 LLM 解析用户输入的棋谱文本，返回按顺序的坐标列表 [(r,c), ...]。
        坐标使用引擎坐标（0-based 行, 列），无法解析则返回 None。
        """
        prompt = (
            "请从用户提供的棋谱文本中按顺序抽取落子序列。"
            " 输出要求：仅返回用英文逗号或空格分隔的坐标列表，坐标格式使用列字母+行数字（例如 Q16 或 PASS），不要任何说明文本。"
            f" 仅返回最多 {max_moves} 手。用户文本开始：\n'''{text}'''"
        )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 2048,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                try:
                    raw = data["choices"][0]["message"]["content"].strip()
                except Exception:
                    raw = str(data)
                log.info(f"[{self.name}] parse_game_text raw: {raw[:400]}")

            # 提取所有坐标（包括 PASS）
            coords = re.findall(r'([A-HJ-Z]\d+|PASS)', raw, re.IGNORECASE)
            if not coords:
                return None
            moves = []
            for s in coords[:max_moves]:
                s = s.upper()
                if s == "PASS":
                    moves.append((-1, -1))
                else:
                    try:
                        rc = from_sgf(s)
                        if rc is None:
                            continue
                        moves.append(rc)
                    except Exception:
                        continue
            return moves if moves else None
        except Exception as e:
            log.error(f"[{self.name}] parse_game_text 调用失败: {e}")
            return None

    # 棋盘上的星位（标准19路棋盘9个星位）
    _STAR_POINTS = {
        (3,3),(3,9),(3,15),
        (9,3),(9,9),(9,15),
        (15,3),(15,9),(15,15),
    }

    def _fallback_move(self, engine, legal_moves: list) -> tuple:
        """LLM 未返回有效结果时的回退 — 基于围棋基本理论的启发式着法"""
        if not legal_moves:
            return None

        my_color = self.color
        opp_color = 2 if my_color == 1 else 1
        total_moves = len(engine.move_history)

        # 阶段判断
        if total_moves < 20:
            phase = "opening"
        elif total_moves < 150:
            phase = "midgame"
        else:
            phase = "endgame"

        # 预处理：收集双方棋子坐标（一次扫描）
        opp_positions = []
        my_positions = []
        for r in range(19):
            for c in range(19):
                v = engine.board[r][c]
                if v == opp_color:
                    opp_positions.append((r, c))
                elif v == my_color:
                    my_positions.append((r, c))

        all_occupied = opp_positions + my_positions

        def score_move(r: int, c: int) -> float:
            s = 0.0

            if phase == "opening":
                # 星位是布局阶段的天然好点
                if (r, c) in self._STAR_POINTS:
                    s += 25.0

                # 角的价值最大：距离最近角的曼哈顿距离越小越好
                corner_dist = min(
                    r + c,                    # 左上角 (0,0)
                    r + (18 - c),             # 右上角 (0,18)
                    (18 - r) + c,             # 左下角 (18,0)
                    (18 - r) + (18 - c),      # 右下角 (18,18)
                )
                s += max(0, 10 - corner_dist) * 2.5

                # 布局不走一二线（太低效）
                edge_dist = min(r, 18 - r, c, 18 - c)
                if edge_dist <= 1:
                    s -= 25.0
                elif edge_dist == 2:  # 三线 — 实地线
                    s += 4.0
                elif edge_dist == 3:  # 四线 — 势力线
                    s += 2.0

                # 已占角附近优先发展（挂角/守角）
                if opp_positions:
                    min_opp = min(abs(r - or_) + abs(c - oc) for or_, oc in opp_positions)
                    if 3 <= min_opp <= 5:
                        s += 8.0

            elif phase == "midgame":
                # 中盘核心：落点既要有价值，又要在"局势焦点"附近
                if opp_positions:
                    # 离对方棋子 2-3 格为最佳攻防距离
                    min_opp = min(abs(r - or_) + abs(c - oc) for or_, oc in opp_positions)
                    if min_opp == 2 or min_opp == 3:
                        s += 10.0
                    elif min_opp == 4 or min_opp == 5:
                        s += 5.0
                    elif min_opp == 1:  # 紧贴对方——只在特定战术下有价值
                        s += 1.0

                if my_positions:
                    # 与己方棋子的关联：3-6 格为最佳呼应距离
                    min_my = min(abs(r - mr) + abs(c - mc) for mr, mc in my_positions)
                    if 3 <= min_my <= 6:
                        s += 8.0
                    elif min_my <= 2:
                        s += 3.0  # 连接或补棋
                    else:
                        s += 2.0  # 全局展开

                # 倾向空地宽阔处（有发展潜力）
                empty_nearby = 0
                for dr in range(-3, 4):
                    for dc in range(-3, 4):
                        if abs(dr) + abs(dc) > 3:
                            continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 19 and 0 <= nc < 19 and engine.board[nr][nc] == 0:
                            empty_nearby += 1
                s += empty_nearby * 0.4

            else:  # endgame
                # 收官：贴近已有棋子的边界空点价值最大
                if all_occupied:
                    min_any = min(abs(r - sr) + abs(c - sc) for sr, sc in all_occupied)
                    s += max(0, 8 - min_any * 1.2)

                # 边界附近的官子优先
                edge_dist = min(r, 18 - r, c, 18 - c)
                if edge_dist <= 2:
                    s += (3 - edge_dist) * 2.5

            # 通用：与所有已有棋子距离过近则适度降权
            if all_occupied:
                min_any_dist = min(abs(r - sr) + abs(c - sc) for sr, sc in all_occupied)
                if min_any_dist == 1:
                    s *= 0.4

            return s

        # 选取得分最高的合法着点
        best = legal_moves[0]
        best_score = score_move(best[0], best[1])
        for move in legal_moves[1:]:
            sc = score_move(move[0], move[1])
            if sc > best_score:
                best_score = sc
                best = move

        log.info(
            f"[{self.name}] fallback phase={phase} "
            f"total_moves={total_moves} "
            f"chosen=({best[0]},{best[1]}) score={best_score:.1f}"
        )
        return best

    def __repr__(self):
        return f"<LLMPlayer {self.name} [{self.style}] color={'黑' if self.color==1 else '白'}>"


# ─── 工厂函数 ──────────────────────────────────

def load_llm_players_from_env() -> list:
    """从 .env 文件加载所有 LLM 棋手配置"""
    from dotenv import load_dotenv
    load_dotenv()

    players = []
    i = 1
    while True:
        key = os.getenv(f"AI_AGENT_{i}_KEY")
        if not key:
            break
        base_url = os.getenv(f"AI_AGENT_{i}_BASE_URL", "https://api.deepseek.com/v1")
        model = os.getenv(f"AI_AGENT_{i}_MODEL", "deepseek-chat")
        name = os.getenv(f"AI_AGENT_{i}_NAME", f"棋手{i}")
        style = os.getenv(f"AI_AGENT_{i}_STYLE", "均衡流")
        desc = os.getenv(f"AI_AGENT_{i}_DESC", "")
        players.append({
            "key": key,
            "base_url": base_url,
            "model": model,
            "name": name,
            "style": style,
            "desc": desc,
            "index": i,
        })
        i += 1

    return players


def create_llm_players(color_black: int = 1, color_white: int = 2) -> tuple:
    """创建黑/白两个 LLMPlayer 实例"""
    agents = load_llm_players_from_env()

    if len(agents) < 2:
        log.warning("LLM agent 不足 2 个（.env 中至少需要配置 AI_AGENT_1 和 AI_AGENT_2）")
        return None, None

    black_cfg = agents[0]
    white_cfg = agents[1] if len(agents) > 1 else agents[0]

    black = LLMPlayer(
        color=color_black,
        api_key=black_cfg["key"],
        base_url=black_cfg["base_url"],
        model=black_cfg["model"],
        name=black_cfg["name"],
        style=black_cfg["style"],
        style_description=black_cfg["desc"],
    )
    white = LLMPlayer(
        color=color_white,
        api_key=white_cfg["key"],
        base_url=white_cfg["base_url"],
        model=white_cfg["model"],
        name=white_cfg["name"],
        style=white_cfg["style"],
        style_description=white_cfg["desc"],
    )

    log.info(f"LLM 棋手就绪: {black} vs {white}")
    return black, white
