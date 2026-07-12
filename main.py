import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from ursina import *

from go_engine import GoEngine, Stone
from ai_player import AIPlayer
from evolution.evolve import AutoEvolver
from commentator import Commentator
from visualizer import GoVisualizer

BOARD_SIZE = 9
MOVE_INTERVAL = 1.5

engine = GoEngine(size=BOARD_SIZE)
ai_black = AIPlayer(Stone.BLACK, name="黑龙", style="balanced")
ai_white = AIPlayer(Stone.WHITE, name="白凤", style="territorial")
commentator = Commentator()
evolver = AutoEvolver()
viz = None

game_over = False
move_timer = 0
move_count = 0
analysis_shown = False
game_count = 0
total_evolutions = 0

def perform_move():
    global game_over, move_count, analysis_shown
    if engine.game_over:
        if not game_over:
            game_over = True
            final_msg = commentator.on_game_over(engine)
            for line in final_msg.split('\n'):
                if line.strip():
                    viz.add_commentary_line(line.strip())
            viz.show_idiom("棋局终了", "AI裁判判定胜负")
        return
    color = engine.current_player
    color_name = "黑" if color == Stone.BLACK else "白"
    ai = ai_black if color == Stone.BLACK else ai_white
    move = ai.get_move(engine)
    r, c = move
    success = engine.make_move(r, c)
    if success:
        move_comment = commentator.on_move(engine, r, c, color_name)
        viz.add_commentary_line(move_comment)
        if (r, c) != (-1, -1):
            viz.place_stone(r, c, color)
        move_count += 1
        analysis_shown = False

def analyze_position():
    global analysis_shown, total_evolutions
    if analysis_shown or engine.game_over:
        return
    analysis_shown = True
    for analyze_color, color_name in [(Stone.BLACK, "黑"), (Stone.WHITE, "白")]:
        groups = engine.get_liberty_groups(analyze_color)
        for g in groups:
            libs = g['liberties']
            result = evolver.evolution.process_liberty_pattern(list(libs), color_name)
            if result:
                comment = commentator.on_liberties_analysis(result)
                viz.add_commentary_line(comment)
                idiom = result.get('idiom', '')
                meaning = result.get('meaning', '')
                viz.show_idiom(idiom, meaning, result.get('mood', ''))
                break
    if move_count > 0 and move_count % 3 == 0:
        patterns = []
        for ac, cn in [(Stone.BLACK, "黑"), (Stone.WHITE, "白")]:
            groups = engine.get_liberty_groups(ac)
            for g in groups:
                r = evolver.evolution.process_liberty_pattern(list(g['liberties']), cn)
                if r:
                    patterns.append(r)
        if patterns:
            report = evolver.evolution.evolve_generation(patterns)
            if report:
                total_evolutions += 1
                evo_comment = commentator.on_evolution(report)
                viz.add_commentary_line(evo_comment)
                status = evolver.get_status()
                viz.show_evolution_status(
                    status['current_generation'],
                    status['total_patterns'],
                    status['total_idioms']
                )

def update():
    global move_timer, game_over
    dt = time.dt
    if viz:
        viz.update(dt)
    if game_over:
        return
    if hasattr(viz, 'game_paused') and viz.game_paused:
        return
    move_timer += dt
    if move_timer >= MOVE_INTERVAL:
        move_timer = 0
        perform_move()
        analyze_position()
    for ac in [Stone.BLACK, Stone.WHITE]:
        groups = engine.get_liberty_groups(ac)
        viz.show_liberties(groups, ac)
    if engine.game_over and not game_over:
        game_over = True
        final_msg = commentator.on_game_over(engine)
        for line in final_msg.split('\n'):
            if line.strip():
                viz.add_commentary_line(line.strip())

def input(key):
    global MOVE_INTERVAL, move_timer
    if key == 'space':
        if hasattr(viz, 'game_paused'):
            viz.game_paused = not viz.game_paused
            viz.add_commentary_line("暂停" if viz.game_paused else "继续")
    elif key == '=' or key == '+':
        MOVE_INTERVAL = max(0.3, MOVE_INTERVAL - 0.2)
        viz.add_commentary_line(f"加速: {MOVE_INTERVAL:.1f}s/步")
    elif key == '-':
        MOVE_INTERVAL = min(5.0, MOVE_INTERVAL + 0.2)
        viz.add_commentary_line(f"减速: {MOVE_INTERVAL:.1f}s/步")
    elif key == 'r':
        reset_game()

def reset_game():
    global engine, game_over, move_timer, move_count, analysis_shown, game_count
    game_count += 1
    engine = GoEngine(size=BOARD_SIZE)
    game_over = False
    move_timer = 0
    move_count = 0
    analysis_shown = False
    if viz:
        viz.clear_liberties()
        viz.stone_entities.clear()
    viz.add_commentary_line(f"=== 第{game_count + 1}局开始 ===")

def main():
    global viz
    app = Ursina(borderless=False)
    viz = GoVisualizer(board_size=BOARD_SIZE)
    viz.add_commentary_line("=== 3D围棋 · 气之进化 ===")
    viz.add_commentary_line("AI黑(黑龙) vs AI白(白凤)")
    viz.add_commentary_line("按Space暂停 +/-调速 R重开")
    status = evolver.get_status()
    viz.show_evolution_status(
        status['current_generation'] or 1,
        status['total_patterns'],
        status['total_idioms']
    )
    app.run()

if __name__ == '__main__':
    main()
