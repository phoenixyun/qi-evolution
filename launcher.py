"""
3D围棋 · 气之进化
===================
启动器

直接启动3D可视化+进化模式，两种能力融合一体：
  - AI vs AI 对弈（黑龙 vs 白凤）
  - 气形光柱特效 + 粒子渲染
  - 成语意境实时解说
  - 形状识别 + 自动进化引擎实时迭代
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def main():
    print("=" * 56)
    print("  3D 围 棋 · 气 之 进 化")
    print("  形状识别 × AI成语 × 自动迭代")
    print("=" * 56)
    print()
    print("  按 Space 暂停 | +/- 调速 | R 重开")
    print()
    from main import main as viz_main
    viz_main()


if __name__ == '__main__':
    main()
