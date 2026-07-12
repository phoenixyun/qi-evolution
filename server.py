"""
3D 围棋 · 气之进化 — FastAPI 后端
====================================
提供 WebSocket 实时对局 + REST API
"""

import os
import sys
import json
import re
import asyncio
import logging
from enum import Enum
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.staticfiles import StaticFiles as StarletteStaticFiles
from starlette.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn


class NoCacheMiddleware(BaseHTTPMiddleware):
    """禁用静态资源缓存 + 修复 MIME 类型"""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript; charset=utf-8'
            response.headers['Cache-Control'] = 'no-store'
        elif path.endswith('.css'):
            response.headers['Content-Type'] = 'text/css; charset=utf-8'
        elif path.endswith('.json'):
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
        return response

# ─── 路径兼容（PyInstaller 打包支持） ──────────
# 打包为 exe 时数据文件解压在 sys._MEIPASS，普通运行时用 __file__ 所在目录
if getattr(sys, 'frozen', False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, _BASE_DIR)

# 加载 .env 配置（LLM API key 等）
from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE_DIR, ".env"))

from go_engine import GoEngine, Stone
from ai_player import AIPlayer, RandomAI
from game_library import GameLibrary, GameReplayController, ReplayMode
from llm_player import create_llm_players, LLMPlayer, render_board, analyze_board, to_sgf as llm_to_sgf, to_numeric

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")

BOARD_SIZE = 19
MOVE_INTERVAL = 1.5

app = FastAPI(title="3D 围棋 · 气之进化")
app.add_middleware(NoCacheMiddleware)


# ─── 游戏会话 ──────────────────────────────────

class GameSession:
    """每个 WebSocket 连接对应一个游戏会话"""

    def __init__(self):
        self.engine = GoEngine(size=BOARD_SIZE)
        self.game_lib = GameLibrary()
        # ═══ 导入 SGF 打谱棋谱 ═══
        self._import_study_games()
        self.replay_ctrl = GameReplayController(self.engine)
        self.move_count = 0
        self.use_llm = False
        self._busy = False  # 防止 LLM 调用期间请求积压
        self._generation = 0  # 代际：reset/切换模式后递增，next_move 完成后校验
        self._skip_pending = False  # reset/stop 后为 True，跳过缓冲区中积压的 next_move

        # 尝试创建 LLM 棋手；若 .env 配置不足则回退到本地 AI
        llm_black, llm_white = create_llm_players()
        if llm_black and llm_white:
            self.ai_black = llm_black
            self.ai_white = llm_white
            self.use_llm = True
            log.info(f"✓ LLM 棋手模式: {llm_black.name}(黑) vs {llm_white.name}(白)")
        else:
            self.ai_black = AIPlayer(Stone.BLACK, name="黑龙", style="balanced")
            self.ai_white = AIPlayer(Stone.WHITE, name="白凤", style="territorial")
            log.info("✓ 本地 AI 模式（未检测到 LLM 配置）")

        # ═══ AI 棋评专用 API 配置（可选，不填则复用 agent 1） ═══
        self.commentary_api_key = os.getenv("AI_COMMENTARY_KEY") or os.getenv("AI_AGENT_1_KEY") or ""
        self.commentary_base_url = os.getenv("AI_COMMENTARY_BASE_URL") or "https://api.deepseek.com/v1"
        self.commentary_model = os.getenv("AI_COMMENTARY_MODEL") or "deepseek-chat"

    def _import_study_games(self):
        """导入 SGF 打谱棋谱到棋谱库（内嵌 + sgf/ 目录）"""
        # 1. 导入内嵌经典棋谱
        try:
            from sgf_games_data import get_all_study_sgfs
            sgf_list = get_all_study_sgfs()
            for sgf_text in sgf_list:
                gid = self.game_lib.import_from_sgf(sgf_text)
                log.info(f"✓ 导入打谱棋谱 #{gid}: {self.game_lib.games[-1].name} "
                         f"({self.game_lib.games[-1].move_count}手)")
        except Exception as e:
            log.warning(f"导入内嵌 SGF 棋谱失败: {e}")

        # 2. 扫描 sgf/ 目录，导入外部棋谱文件
        sgf_dir = os.path.join(_BASE_DIR, "sgf")
        if os.path.isdir(sgf_dir):
            import glob
            for filepath in sorted(glob.glob(os.path.join(sgf_dir, "*.sgf"))):
                try:
                    # 尝试多种编码读取 SGF
                    sgf_text = None
                    for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
                        try:
                            with open(filepath, 'r', encoding=enc) as f:
                                sgf_text = f.read()
                            break
                        except (UnicodeDecodeError, LookupError):
                            continue
                    if sgf_text is None:
                        log.warning(f"无法解码 SGF 文件: {filepath}")
                        continue
                    gid = self.game_lib.import_from_sgf(sgf_text)
                    basename = os.path.basename(filepath)
                    # 若棋盘未命名，用文件名
                    if self.game_lib.games[-1].name == "未知 vs 未知":
                        self.game_lib.games[-1].name = os.path.splitext(basename)[0]
                    log.info(f"✓ 导入外部棋谱 #{gid}: "
                             f"{self.game_lib.games[-1].name} "
                             f"({self.game_lib.games[-1].move_count}手) [来自 {basename}]")
                except Exception as e:
                    log.warning(f"导入外部 SGF 失败 ({filepath}): {e}")

    def parse_and_store_game(self, text: str, name: Optional[str] = None):
        """使用 LLM（若可用）解析用户输入的棋谱文本并存储到棋谱库，返回结果字典。"""
        # 1) 尝试用 LLM 解析
        moves = None
        if self.use_llm and hasattr(self.ai_black, 'parse_game_text'):
            try:
                moves = self.ai_black.parse_game_text(text)
            except Exception as e:
                log.warning(f"LLM 解析棋谱失败: {e}")

        # 2) 回退到本地解析（简单坐标抽取 A-T + 数字）
        if not moves:
            raw = text or ""
            coords = []
            found = re.findall(r'([A-HJ-Z]\d+|PASS)', raw, re.IGNORECASE)
            for s in found:
                s = s.upper()
                if s == 'PASS':
                    coords.append((-1, -1))
                else:
                    try:
                        from llm_player import from_sgf
                        rc = from_sgf(s)
                        if rc is not None:
                            coords.append(rc)
                    except Exception:
                        continue
            if coords:
                moves = coords

        if not moves:
            return {"ok": False, "message": "无法解析棋谱"}

        # 存储到棋谱库，使用服务器默认棋盘尺寸
        gid = self.game_lib.add_game(name or "用户棋谱", moves, subtitle="用户上传", description="由用户上传并解析的棋谱", board_size=BOARD_SIZE)
        return {"ok": True, "game_id": gid, "moves_count": len(moves)}

    # ─── 核心操作 ──────────────────────────────

    async def chat_with_player(self, player_name: str, message: str):
        """用户与 AI 棋手对话，返回 AI 的回复"""
        if not self.use_llm:
            return None

        # 识别目标棋手
        target = None
        if player_name == self.ai_black.name:
            target = self.ai_black
        elif player_name == self.ai_white.name:
            target = self.ai_white

        if target is None:
            return None

        # 同步调用，对聊天容忍更长超时
        try:
            reply = await asyncio.to_thread(target.chat, self.engine, message)
            return reply
        except Exception as e:
            log.warning(f"与 {target.name} 聊天失败: {e}")
            return None

    async def next_move(self):
        """执行一步棋（AI 或回放），返回事件列表。有防重入锁。"""
        # 防止请求积压导致棋子爆发（AI 长调用或快速回放都可能积压）
        if self._busy:
            return [], False

        events = []

        if self.engine.game_over:
            return self._game_over_events(), False

        gen = self._generation  # 记录本手开始时的代际
        success = False
        llm_events = []
        self._busy = True
        try:
            if self.replay_ctrl.is_ai_mode():
                # 把同步的 LLM 调用扔进线程池，避免阻塞 asyncio 事件循环
                # 否则前端轮询消息会堵在缓冲区，等调用结束瞬间全部触发
                success, llm_events = await asyncio.to_thread(self._ai_move)
            elif self.replay_ctrl.is_replay_mode():
                success = self._replay_step()
        finally:
            self._busy = False

        # 代际校验：若本手执行期间发生了 reset/模式切换，丢弃过期结果
        if gen != self._generation:
            log.info(f"[next_move] 代际过期 gen={gen} != current={self._generation}，丢弃结果")
            return [], False

        # LLM 请求/响应事件（无论落子是否成功都要发送）
        events += llm_events

        if success:
            self.move_count += 1
            events.append(self._stone_event())
            events += self._capture_events()
            events += self._liberty_events()
            events.append(self._game_info_event())

        if self.engine.game_over:
            events += self._game_over_events()

        return events, success

    def prev_move(self):
        """回退一手（回放模式）"""
        if not self.replay_ctrl.is_replay_mode():
            return [], False

        result = self.replay_ctrl.prev_move()
        if result is None:
            return [], False

        # 发送完整棋盘状态 + 同步所有棋子
        events = [self._board_state_event()]
        events += self._liberty_events()
        events.append(self._game_info_event())
        return events, True

    def toggle_mode(self):
        """切换模式"""
        old_mode = self.replay_ctrl.mode
        new_mode = self.replay_ctrl.toggle_mode()
        mode_names = {
            ReplayMode.AI_SELFPLAY: "AI自对弈",
            ReplayMode.REPLAY: "棋谱回放",
            ReplayMode.REPLAY_AUTO: "自动回放",
        }

        events = [{
            "type": "mode_changed",
            "mode": mode_names.get(new_mode, "未知"),
            "mode_id": new_mode,
            "agent_black": self.ai_black.name,
            "agent_white": self.ai_white.name,
            "use_llm": self.use_llm,
        }]

        # 离开需要 next_move 的模式时，递增代际使进行中的 LLM 调用失效
        if old_mode in (ReplayMode.AI_SELFPLAY, ReplayMode.REPLAY_AUTO):
            self._generation += 1

        if new_mode == ReplayMode.AI_SELFPLAY:
            self.replay_ctrl.game = None
            self.replay_ctrl.move_index = 0
            self.replay_ctrl.finished = False
            self._reset_state()
            events.append({"type": "reset"})
            events.append(self._board_state_event())
        else:
            if self.replay_ctrl.game is None:
                self.replay_ctrl.load_game(self.game_lib.current)
            self._reset_state()
            events.append({"type": "reset"})
            events.append(self._board_state_event())
            events.append(self._game_intro_events())

        return events

    def set_mode(self, mode_id: str, black_name: str = None, white_name: str = None):
        """直接设置模式（ai_selfplay / replay）"""
        if mode_id not in (ReplayMode.AI_SELFPLAY, ReplayMode.REPLAY, ReplayMode.REPLAY_AUTO):
            return [{"type": "error", "message": f"未知模式: {mode_id}"}]

        # 更新 AI 棋手名字（如果提供）
        if black_name:
            self.ai_black.name = black_name
        if white_name:
            self.ai_white.name = white_name

        mode_names = {
            ReplayMode.AI_SELFPLAY: "AI自对弈",
            ReplayMode.REPLAY: "棋谱回放",
            ReplayMode.REPLAY_AUTO: "自动回放",
        }

        # 已在目标模式 — 重置棋盘并重启对局
        if self.replay_ctrl.mode == mode_id and not black_name and not white_name:
            self._generation += 1
            self._reset_state()
            return [
                {"type": "reset"},
                {"type": "mode_changed", "mode": mode_names.get(mode_id, "未知"),
                 "mode_id": mode_id, "agent_black": self.ai_black.name,
                 "agent_white": self.ai_white.name, "use_llm": self.use_llm},
                self._board_state_event(),
            ]

        old_mode = self.replay_ctrl.mode
        # 离开需要 next_move 的模式时，递增代际使进行中的 LLM 调用失效
        if old_mode in (ReplayMode.AI_SELFPLAY, ReplayMode.REPLAY_AUTO):
            self._generation += 1
        self.replay_ctrl.mode = mode_id

        if mode_id == ReplayMode.AI_SELFPLAY:
            self.replay_ctrl.game = None
            self.replay_ctrl.move_index = 0
            self.replay_ctrl.finished = False
            self._reset_state()
        else:
            if self.replay_ctrl.game is None:
                self.replay_ctrl.load_game(self.game_lib.current)
            self._reset_state()

        events = [
            {"type": "reset"},
            {
                "type": "mode_changed",
                "mode": mode_names.get(mode_id, "未知"),
                "mode_id": mode_id,
                "agent_black": self.ai_black.name,
                "agent_white": self.ai_white.name,
                "use_llm": self.use_llm,
            },
            self._board_state_event(),
        ]

        if mode_id != ReplayMode.AI_SELFPLAY:
            events += self._game_intro_events()
            events += self.get_move_list()  # 进入研究模式时自动显示走法列表

        return events

    def load_game(self, game_id: int):
        """加载指定棋谱"""
        game = self.game_lib.get_game(game_id - 1)
        if not game:
            return [{"type": "error", "message": f"棋谱 #{game_id} 不存在"}]

        self.replay_ctrl.load_game(game)
        self._reset_state()
        events = [{"type": "reset"}, self._board_state_event()]
        # 模式切换到 REPLAY
        self.replay_ctrl.mode = ReplayMode.REPLAY
        events.append({
            "type": "mode_changed",
            "mode": "棋谱回放",
            "mode_id": ReplayMode.REPLAY,
        })
        events.append(self._game_info_event())
        events += self._game_intro_events()
        events += self.get_move_list()  # 自动发送走法列表
        return events

    # ─── 打谱研究模式 ──────────────────────────

    def goto_move(self, index: int):
        """跳到指定手数 (1-based)，返回事件列表"""
        if not self.replay_ctrl.is_replay_mode():
            return [{"type": "error", "message": "仅在棋谱回放模式下可用"}]

        if not self.replay_ctrl.game:
            return [{"type": "error", "message": "未加载棋谱"}]

        # index is 1-based, convert to 0-based for controller
        zero_idx = max(0, min(index - 1, self.replay_ctrl.game.move_count - 1))
        result = self.replay_ctrl.goto_move(zero_idx)
        self.move_count = zero_idx + 1
        self.analysis_shown = False
        self.commentary_lines = []

        events = [self._board_state_event()]
        events += self._liberty_events()
        events.append(self._game_info_event())

        # 发送关键手标记
        key_event = self._key_move_event()
        if key_event:
            events.append(key_event)

        # 发送该手的注释
        ann = self.replay_ctrl.game.get_annotation(zero_idx)
        if ann:
            events.append({
                "type": "move_annotation",
                "move_number": zero_idx + 1,
                "text": ann,
            })

        return events

    async def analyze_move(self, index: int):
        """用 LLM 流式分析指定手数 (1-based)，逐块 yield 分析文本"""
        if not self.use_llm:
            yield {"type": "error", "message": "LLM 未启用，请检查 .env 配置"}
            return

        game = self.replay_ctrl.game
        if not game:
            yield {"type": "error", "message": "未加载棋谱"}
            return

        zero_idx = max(0, min(index - 1, game.move_count - 1))
        r, c, color = game.get_move(zero_idx)
        color_name = "黑" if color == Stone.BLACK else "白"
        player_name = game.black_player if color == Stone.BLACK else game.white_player
        coord_str = to_numeric(r, c) if not (r == -1 and c == -1) else "PASS"

        # 构建分析 prompt
        board_text = render_board(self.engine)
        analysis_text = analyze_board(self.engine, color)
        key_label = game.get_key_move_label(zero_idx)
        annotation = game.get_annotation(zero_idx)

        prompt = f"""请以围棋专家的身份，深度分析这一手棋。

【棋谱信息】
  棋谱: {game.name}
  对阵: {game.black_player}(黑) vs {game.white_player}(白)
  当前分析: 第{zero_idx+1}手 ({color_name}棋 · {player_name})
  落子位置: {coord_str}
  {f'标注: {key_label}' if key_label else ''}
  {f'SGF注释: {annotation[:200]}' if annotation else ''}

【当前棋盘状态 (第{zero_idx+1}手之后)】
{board_text}

【战术分析】
{analysis_text}

请从以下几个维度深度分析这一手:
1. 战略意图: 这手棋在当前局面下的目的和战略意义
2. 技术含量: 这手棋运用了哪些围棋技术(手筋/定式/弃子/腾挪等)
3. 全局影响: 这手棋如何改变了双方势力的消长
4. 历史地位: 如果这是历史名局中的一手，它在围棋史上的意义
{f'5. 妙处解析: 为什么这一手被称为"{key_label}"？它妙在何处？' if key_label else ''}

请用 4-6 段文字回答，语言生动有见解，模拟围棋评论家的口吻。"""

        # 使用 LLM 棋手的 API 凭据发起流式分析请求
        try:
            ai = self.ai_black if hasattr(self.ai_black, 'api_key') else self.ai_white
            if not hasattr(ai, 'api_key'):
                yield {"type": "error", "message": "LLM 客户端不可用"}
                return

            import httpx

            # 先发送头部信息
            yield {
                "type": "move_analysis_start",
                "move_number": zero_idx + 1,
                "coord": coord_str,
                "color": color_name,
                "player": player_name,
                "key_label": key_label or "",
            }

            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    f"{ai.base_url}/chat/completions",
                    json={
                        "model": ai.model,
                        "messages": [
                            {"role": "system", "content": "你是一位围棋评论家，擅长深度分析棋局中的每一步妙手。你的分析既有技术深度，又有文学感染力。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 1500,
                        "stream": True,
                    },
                    headers={
                        "Authorization": f"Bearer {ai.api_key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {}).get("content", "")
                            if delta:
                                yield {"type": "move_analysis_chunk", "text": delta}
                        except json.JSONDecodeError:
                            continue

            yield {"type": "move_analysis_end"}
        except Exception as e:
            log.warning(f"分析走法失败: {e}")
            yield {"type": "error", "message": f"分析失败: {str(e)}"}
            yield {"type": "move_analysis_end"}

    async def ai_commentary(self):
        """用 LLM 流式点评当前全局局面，逐块 yield 文本到气形棋盘覆盖层"""
        if not self.use_llm:
            yield {"type": "error", "message": "LLM 未启用，请检查 .env 配置"}
            return

        board_text = render_board(self.engine)
        analysis_text = analyze_board(self.engine, self.engine.current_player)
        total_moves = len(self.engine.move_history)

        # 开局阶段（10手内）才打招呼，中后盘直接点评
        opening_instruction = "" if total_moves > 10 else "开场先跟观众打个招呼（一句话即可），然后"

        prompt = f"""请以资深围棋评论家的身份，对当前全局局面进行精彩点评。

【当前棋盘状态】
{board_text}

【战术分析数据】
{analysis_text}

{opening_instruction}请从以下角度点评（3-5段即可）：
1. 全局态势：双方势力范围、实空与外势的对比
2. 当前焦点：棋盘上最关键的争夺区域
3. 下一手展望：双方可能的战略方向
4. 风格点评：结合棋局进程的趣味观察

要求语言生动有趣，类似围棋直播解说风格，每段 2-4 句话。"""

        try:
            if not self.commentary_api_key:
                yield {"type": "error", "message": "AI棋评未配置 API Key，请在 .env 中设置 AI_COMMENTARY_KEY"}
                return

            import httpx

            yield {"type": "ai_commentary_start"}

            async with httpx.AsyncClient(timeout=90.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.commentary_base_url}/chat/completions",
                    json={
                        "model": self.commentary_model,
                        "messages": [
                            {"role": "system", "content": "你是一位资深围棋评论家，擅长用生动有趣的语言点评全局棋局。你的点评既有专业深度，又能让普通爱好者听懂。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.8,
                        "max_tokens": 1200,
                        "stream": True,
                    },
                    headers={
                        "Authorization": f"Bearer {self.commentary_api_key}",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {}).get("content", "")
                            if delta:
                                yield {"type": "ai_commentary_chunk", "text": delta}
                        except json.JSONDecodeError:
                            continue

            yield {"type": "ai_commentary_end"}

        except Exception as e:
            log.warning(f"AI棋评失败: {e}")
            yield {"type": "error", "message": f"AI棋评请求失败: {str(e)}"}
            yield {"type": "ai_commentary_end"}

    def get_move_list(self):
        """获取当前棋谱的完整走法列表"""
        game = self.replay_ctrl.game
        if not game:
            return [{"type": "error", "message": "未加载棋谱"}]

        summary = game.get_move_summary()
        current = self.replay_ctrl.move_index  # 当前已走到的索引

        return [{
            "type": "move_list",
            "game_name": game.name,
            "total_moves": game.move_count,
            "current_move": current,
            "black_player": game.black_player,
            "white_player": game.white_player,
            "moves": summary,
        }]

    def list_games(self):
        """列出棋谱库中所有可用棋谱"""
        games = []
        for i, g in enumerate(self.game_lib.games):
            games.append({
                "id": i + 1,
                "name": g.name,
                "moves": g.move_count,
                "black": g.black_player,
                "white": g.white_player,
                "year": g.year if hasattr(g, 'year') else "",
                "is_study": bool(g.key_moves) if hasattr(g, 'key_moves') else False,
            })
        return [{"type": "game_list", "games": games}]

    def load_sgf(self, sgf_text: str = "", sgf_b64: str = ""):
        """从 SGF 文本或 base64 编码直接加载棋谱"""
        import base64 as _base64
        if sgf_b64:
            raw_bytes = _base64.b64decode(sgf_b64)
            sgf_text = None
            for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
                try:
                    sgf_text = raw_bytes.decode(enc)
                    log.info(f"SGF 文件以 {enc} 编码解码成功")
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if sgf_text is None:
                return [{"type": "error", "message": "无法解码 SGF 文件编码"}]
        try:
            gid = self.game_lib.import_from_sgf(sgf_text)
            events = self.load_game(gid)
            events += self.list_games()
            return events
        except Exception as e:
            return [{"type": "error", "message": f"SGF 解析失败: {e}"}]

    # ─── 原有方法 ──────────────────────────────

    def reset(self):
        """重置对局"""
        self._generation += 1  # 使进行中的 LLM 调用失效
        self._skip_pending = True  # 跳过缓冲区中已积压的 next_move
        self._reset_state()
        events = [{"type": "reset"}, self._board_state_event()]
        events += self._liberty_events()
        return events

    def get_full_state(self):
        """获取完整状态"""
        return {
            "type": "full_state",
            "board": self.engine.board.tolist(),
            "current_player": int(self.engine.current_player),
            "move_count": self.move_count,
            "game_over": self.engine.game_over,
            "captures": {str(k): v for k, v in self.engine.captures.items()},
            "mode": self.replay_ctrl.mode,
            "game": self._game_info_data(),
        }

    # ─── 内部方法 ──────────────────────────────

    def _ai_move(self):
        color = self.engine.current_player
        ai = self.ai_black if color == Stone.BLACK else self.ai_white
        try:
            move = ai.get_move(self.engine)
            # 收集 LLM 请求/响应事件（供前端面板展示）
            llm_events = self._collect_llm_events(ai)
            if move is None:
                log.warning(f"[_ai_move] {ai.name} 返回 None，跳过本手")
                return False, llm_events
            r, c = move
            success = self.engine.make_move(r, c)
            return success, llm_events
        except Exception as e:
            log.exception(f"[_ai_move] AI {ai.name} 计算落子时异常: {e}")
            return False, []

    def _collect_llm_events(self, ai) -> list:
        """收集 AI 棋手最近一次 LLM 调用的请求/响应，生成前端事件"""
        events = []
        # AIPlayer（本地 AI）没有 last_llm_request/response 属性，LLMPlayer 才有
        req = getattr(ai, 'last_llm_request', None)
        resp = getattr(ai, 'last_llm_response', None)

        if req:
            # 截断过长的 system_prompt 和 user_prompt
            req_display = {
                "player": req.get("player", "?"),
                "color": req.get("color", "?"),
                "model": req.get("model", "?"),
                "system_prompt": (req.get("system_prompt", "") or "")[:800],
                "user_prompt": (req.get("user_prompt", "") or "")[:1200],
            }
            events.append({"type": "llm_request", **req_display})
        if resp:
            events.append({"type": "llm_response", "text": resp[:1500], "player": ai.name})

        return events

    def _replay_step(self):
        result = self.replay_ctrl.next_move()
        if result:
            return True
        return False

    def _reset_state(self):
        self.engine.__init__(size=BOARD_SIZE)
        self.replay_ctrl.engine = self.engine
        self.move_count = 0

    def _stone_event(self):
        if not self.engine.last_move:
            return {"type": "noop"}
        r, c = self.engine.last_move
        last_entry = self.engine.move_history[-1]
        color = last_entry[2]
        return {
            "type": "stone_placed",
            "r": int(r),
            "c": int(c),
            "color": int(color),
        }

    def _capture_events(self):
        # 捕获事件不在独立跟踪中，简化处理
        return []

    def _key_move_event(self):
        """若当前位置是关键手，返回高亮事件"""
        game = self.replay_ctrl.game
        if not game:
            return None
        current_idx = self.replay_ctrl.move_index - 1
        if current_idx < 0 or not game.is_key_move(current_idx):
            return None
        r, c = game.moves[current_idx]
        return {
            "type": "key_move",
            "move_number": current_idx + 1,
            "label": game.get_key_move_label(current_idx),
            "r": int(r),
            "c": int(c),
            "color": 1 if current_idx % 2 == 0 else 2,
            "annotation": game.get_annotation(current_idx),
        }

    def _liberty_events(self):
        """返回一个内含双方气数据的事件"""
        both = {}
        for ac in [Stone.BLACK, Stone.WHITE]:
            groups = self.engine.get_liberty_groups(ac)
            if groups:
                data = []
                for g in groups:
                    data.append({
                        "size": g["size"],
                        "liberty_count": g["liberty_count"],
                        "liberties": [[int(r), int(c)] for r, c in g["liberties"]],
                        "stones": [[int(r), int(c)] for r, c in g["stones"]],
                    })
                both["black" if ac == Stone.BLACK else "white"] = data
        if both:
            return [{"type": "liberty_data", "both": both}]
        return []

    def _game_info_data(self):
        info = self.replay_ctrl.get_current_move_info()
        game = self.replay_ctrl.game
        if info:
            return {
                "name": game.name if game else "",
                "black_player": game.black_player if game else self.ai_black.name,
                "white_player": game.white_player if game else self.ai_white.name,
                "current_move": info.get("move_number", 0),
                "total_moves": info.get("total", 0),
                "position": info.get("position", ""),
                "color": info.get("color", ""),
            }
        return {
            "name": game.name if game else "AI自对弈",
            "black_player": game.black_player if game else self.ai_black.name,
            "white_player": game.white_player if game else self.ai_white.name,
            "current_move": self.move_count,
            "total_moves": 0,
            "position": "",
            "color": "",
        }

    def _game_info_event(self):
        data = self._game_info_data()
        return {"type": "game_info", **data}

    def _board_state_event(self):
        return {
            "type": "board_state",
            "board": self.engine.board.tolist(),
            "current_player": int(self.engine.current_player),
            "move_count": self.move_count,
        }

    def _game_intro_events(self):
        return []

    def _game_over_events(self):
        return [{"type": "game_over", "message": "棋局结束"}]


# ─── WebSocket 端点 ────────────────────────────

async def _ws_send_json(ws, data):
    import json as _json
    await ws.send_text(_json.dumps(data, ensure_ascii=False))


@app.websocket("/ws")
async def game_websocket(websocket: WebSocket):
    await websocket.accept()
    session = GameSession()
    log.info("WebSocket 连接已建立")

    # 发送初始状态
    ai_intro = f"AI黑({session.ai_black.name}) vs AI白({session.ai_white.name})"
    if session.use_llm:
        ai_intro += " ✦ LLM 驱动"
    init_events = [
        session._board_state_event(),
        {"type": "mode_changed", "mode": "AI自对弈", "mode_id": ReplayMode.AI_SELFPLAY,
            "agent_black": session.ai_black.name, "agent_white": session.ai_white.name, "use_llm": session.use_llm,
            "initial": True},
    ]
    for ev in init_events:
        await _ws_send_json(websocket, ev)

    # ─── AI棋评：后台直接发 WS（不阻塞主循环） ──
    # Uvicorn websockets 底层有 send_lock，并发安全
    async def _ai_commentary_direct(ws):
        """后台直接发 AI棋评到 WebSocket，不走队列"""
        try:
            async for ev in session.ai_commentary():
                await _ws_send_json(ws, ev)
        except Exception as e:
            log.warning(f"AI棋评直接发送异常: {e}")

    try:
        while True:
            data = await websocket.receive_json()

            cmd = data.get("type", "")

            if cmd == "next_move":
                if session._skip_pending:
                    continue  # 跳过 reset/stop 前积压的请求
                events, success = await session.next_move()
                if success:
                    for ev in events:
                        await _ws_send_json(websocket, ev)
                elif events:  # game_over 时也可能有事件但 success=False
                    for ev in events:
                        await _ws_send_json(websocket, ev)

            elif cmd == "prev_move":
                events, success = session.prev_move()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "toggle_mode":
                events = session.toggle_mode()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "set_mode":
                mode_id = data.get("mode_id", "")
                black_name = data.get("black_name", None)
                white_name = data.get("white_name", None)
                events = session.set_mode(mode_id, black_name, white_name)
                session._skip_pending = False  # 新模式，清除跳过标记
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "load_game":
                game_id = data.get("id", 1)
                events = session.load_game(game_id)
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "list_games":
                events = session.list_games()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "reset":
                events = session.reset()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "stop":
                events = session.reset()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "chat":
                # 聊天模块已禁用（按新配置）
                await _ws_send_json(websocket, {"type": "chat_disabled", "message": "聊天模块已被禁用"})

            elif cmd == "upload_game":
                text = data.get("text", "")
                name = data.get("name", None)
                result = await asyncio.to_thread(session.parse_and_store_game, text, name)
                await _ws_send_json(websocket, {"type": "upload_result", **result})

            elif cmd == "get_state":
                await _ws_send_json(websocket, session.get_full_state())

            # ═══ 打谱研究模式 ──────────────────────
            elif cmd == "goto_move":
                idx = data.get("index", 1)
                events = session.goto_move(idx)
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "analyze_move":
                idx = data.get("index", 1)
                async for ev in session.analyze_move(idx):
                    await _ws_send_json(websocket, ev)

            elif cmd == "get_move_list":
                events = session.get_move_list()
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "load_sgf":
                sgf_text = data.get("sgf_text", "") or data.get("sgf", "")
                sgf_b64 = data.get("sgf_b64", "")
                events = session.load_sgf(sgf_text, sgf_b64=sgf_b64)
                for ev in events:
                    await _ws_send_json(websocket, ev)

            elif cmd == "ai_commentary":
                asyncio.create_task(_ai_commentary_direct(websocket))

    except WebSocketDisconnect:
        log.info("WebSocket 连接已断开")


# ─── 静态文件服务 ──────────────────────────────

frontend_dir = os.path.join(_BASE_DIR, "frontend")
os.makedirs(frontend_dir, exist_ok=True)
os.makedirs(os.path.join(frontend_dir, "js"), exist_ok=True)
os.makedirs(os.path.join(frontend_dir, "css"), exist_ok=True)

app.mount("/", StarletteStaticFiles(directory=frontend_dir, html=True), name="frontend")


# ─── 启动 ──────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  3D 围棋 · 气之进化")
    print(f"  启动服务器: http://localhost:8765")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
