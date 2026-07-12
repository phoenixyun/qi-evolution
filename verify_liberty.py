import sys, numpy as np
sys.path.insert(0, 'd:/python/go')
from go_engine import GoEngine, Stone

# Final position board data (234/234 moves, captured from browser)
board_csv = [
    '0,0,0,0,0,0,0,1,1,2,0,0,0,2,2,2,1,0,0',
    '0,0,1,2,0,0,1,1,2,2,2,0,2,2,1,1,0,1,0',
    '0,0,0,1,2,0,1,2,2,1,1,2,1,1,0,0,0,0,1',
    '0,0,1,0,0,0,1,2,2,2,2,1,2,1,1,0,1,1,2',
    '0,0,1,2,0,0,1,2,1,2,1,2,1,0,0,1,1,2,2',
    '0,1,1,0,0,0,1,2,1,1,1,2,2,1,1,2,0,2,0',
    '1,2,1,0,1,1,2,1,1,1,2,1,1,0,2,2,2,0,0',
    '0,1,2,1,1,2,2,1,1,2,2,0,1,1,1,2,1,0,0',
    '2,1,2,1,1,1,2,2,2,1,2,1,0,2,1,0,2,0,0',
    '0,2,2,2,2,0,0,0,2,1,2,1,1,2,1,2,0,0,0',
    '0,0,0,0,2,0,2,2,2,1,0,2,2,2,1,2,0,0,0',
    '0,0,2,0,2,2,1,2,1,2,2,0,0,2,1,2,0,0,0',
    '0,1,2,2,1,1,1,1,1,1,2,2,2,1,2,2,0,0,0',
    '0,2,1,1,1,2,1,0,1,2,1,1,1,1,1,2,1,0,0',
    '0,2,2,0,1,2,1,2,2,1,2,1,1,2,2,1,2,0,0',
    '0,0,0,2,2,2,1,1,2,2,1,1,1,2,1,2,2,0,0',
    '0,0,0,0,0,0,2,2,1,1,0,0,0,1,1,1,2,0,0',
    '0,0,0,0,2,2,2,1,1,0,0,0,0,0,0,1,1,2,0',
    '0,0,0,0,2,1,1,1,0,0,0,0,0,0,0,1,2,2,0',
]

board = np.array([list(map(int, row.split(','))) for row in board_csv], dtype=int)
engine = GoEngine(19)
engine.board = board

bcount = np.count_nonzero(board == 1)
wcount = np.count_nonzero(board == 2)
ecount = np.count_nonzero(board == 0)

def pos(r,c):
    return chr(65+c) + str(r+1)

print('='*60)
print('LIBERTY VERIFICATION REPORT (Final Position)')
print(f'Board: 19x19, B={bcount} W={wcount} Empty={ecount}')
print('='*60)

# 1) Zero-liberty stones
print('\n[1] ZERO-LIBERTY STONES (dead)')
dead_found = False
for r in range(19):
    for c in range(19):
        if board[r][c] != 0:
            g = engine.get_group(r,c)
            if len(engine.get_liberties(g)) == 0:
                cname = 'B' if board[r][c]==1 else 'W'
                print(f'  {pos(r,c)} ({cname}) - 0 liberties, DEAD')
                dead_found = True
if not dead_found:
    print('  (none - all alive)')

# 2) Liberty group summary
print('\n[2] LIBERTY GROUP SUMMARY')
for cname, cval, clabel in [('BLACK',1,'B'), ('WHITE',2,'W')]:
    groups = engine.get_liberty_groups(cval)
    total_stones = sum(g['size'] for g in groups)
    total_libs = sum(g['liberty_count'] for g in groups)
    atari = [g for g in groups if g['liberty_count']==1]
    danger = [g for g in groups if g['liberty_count']==2]
    print(f'\n  {cname}: {len(groups)} groups, {total_stones} stones, {total_libs} liberties')
    print(f'    Atari(1lib): {len(atari)} groups')
    print(f'    Danger(2lib): {len(danger)} groups')
    
    # Check liberty points are all empty
    errors = 0
    for g in groups:
        for r,c in g['liberties']:
            if board[r][c] != 0:
                errors += 1
                if errors <= 5:
                    print(f'    ERROR: liberty {pos(r,c)} is not empty!')
    if errors == 0:
        print(f'    ALL liberty points are empty: OK')

# 3) Coverage check
print('\n[3] COVERAGE CHECK')
black_set = set()
white_set = set()
for g in engine.get_liberty_groups(Stone.BLACK):
    black_set.update(g['stones'])
for g in engine.get_liberty_groups(Stone.WHITE):
    white_set.update(g['stones'])

board_black = {(r,c) for r in range(19) for c in range(19) if board[r][c]==Stone.BLACK}
board_white = {(r,c) for r in range(19) for c in range(19) if board[r][c]==Stone.WHITE}

missing_b = board_black - black_set
missing_w = board_white - white_set
if missing_b:
    print(f'  BLACK missing from groups: {[pos(r,c) for r,c in missing_b]}')
else:
    print(f'  BLACK: all stones covered OK')
if missing_w:
    print(f'  WHITE missing from groups: {[pos(r,c) for r,c in missing_w]}')
else:
    print(f'  WHITE: all stones covered OK')

# 4) Spot check: large groups
print('\n[4] SPOT CHECK - Large groups')
for cval, cname in [(1,'BLACK'),(2,'WHITE')]:
    groups = engine.get_liberty_groups(cval)
    big = sorted(groups, key=lambda g: -g['size'])[:3]
    for g in big:
        stones_sorted = sorted(g['stones'])
        libs_sorted = sorted(g['liberties'])
        stones_str = ','.join(pos(r,c) for r,c in stones_sorted[:5])
        libs_str = ','.join(pos(r,c) for r,c in libs_sorted[:5])
        if len(stones_sorted) > 5: stones_str += f'...(+{len(stones_sorted)-5})'
        if len(libs_sorted) > 5: libs_str += f'...(+{len(libs_sorted)-5})'
        print(f'  {cname} {g["size"]}stones/{g["liberty_count"]}libs')
        print(f'    stones: [{stones_str}]')
        print(f'    libs:   [{libs_str}]')

# 5) Score
print('\n[5] FINAL SCORE')
try:
    bs, ws, bt, wt = engine.calculate_score()
    print(f'  BLACK: {bs:.1f} ({bcount} stones + {bt} territory)')
    print(f'  WHITE: {ws:.1f} ({wcount} stones + {wt} territory + 6.5 komi)')
    result = 'BLACK' if bs > ws else 'WHITE'
    margin = abs(bs-ws)
    print(f'  RESULT: {result} wins by {margin:.1f}')
except Exception as e:
    print(f'  Score error: {e}')

print('\n' + '='*60)
print('VERIFICATION COMPLETE')
print('='*60)
