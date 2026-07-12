/**
 * 3D 围棋 · 气之进化 — 游戏逻辑 + WebSocket 客户端
 * ==================================================
 * 负责 WebSocket 通信、游戏循环控制、UI 状态同步
 * 支持双棋盘 + 棋谱上传
 */

import { GoScene } from './scene.js';

const $ = id => document.getElementById(id);
const escapeHtml = s => {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
};

// ─── 状态 ──────────────────────────────

let ws = null;
let sceneGame = null;     // 左 — 对战棋盘（纯棋子）
let sceneLiberty = null;  // 右 — 气形棋盘（棋子+气可视化）
let moveTimer = null;
let moveInterval = 1500;
let isPaused = false;
let gameMode = 'ai_selfplay';
let boardState = null;
let reconnectAttempts = 0;
const RECONNECT_DELAY = 2000;
const MAX_RECONNECT_DELAY = 8000;

// ─── WebSocket ──────────────────────────

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        console.log('WebSocket 已连接');
        reconnectAttempts = 0;
        showStatus('已连接');
    };

    ws.onmessage = (event) => handleMessage(JSON.parse(event.data));

    ws.onclose = () => {
        showStatus('重连中...');
        stopGameLoop();
        const delay = Math.min(RECONNECT_DELAY * Math.pow(1.5, reconnectAttempts), MAX_RECONNECT_DELAY);
        reconnectAttempts++;
        setTimeout(connect, delay);
    };

    ws.onerror = () => { /* onclose will fire */ };
}

function send(cmd) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(cmd));
    }
}

// ─── 消息处理 ──────────────────────────

function handleMessage(data) {
    switch (data.type) {
        case 'board_state':
            boardState = data.board;
            syncBoardToBoth(data.board);
            break;

        case 'stone_placed':
            if (sceneGame) sceneGame.placeStone(data.r, data.c, data.color);
            if (sceneLiberty) sceneLiberty.placeStone(data.r, data.c, data.color);
            break;

        case 'liberty_data':
            if (sceneLiberty && data.both) {
                sceneLiberty.clearLiberties();
                if (data.both.black) sceneLiberty.renderLibertiesForColor(1, data.both.black);
                if (data.both.white) sceneLiberty.renderLibertiesForColor(2, data.both.white);
            }
            break;

        case 'commentary':
            data.lines.forEach(l => addCommentary(l));
            break;

        case 'idiom':
            showIdiom(data);
            break;

        case 'mode_changed':
            gameMode = data.mode_id;
            const agentLabel = data.use_llm
                ? `${data.agent_black} ⚫ vs ${data.agent_white} ⚪`
                : `${data.mode}`;
            const modeEl = $('mode-indicator');
            if (modeEl) modeEl.textContent = `◆ ${agentLabel} ◆`;
            if (data.mode_id === 'ai_selfplay') {
                closeStudyPopup();
                setTimeout(() => startGameLoop(), 300);
            } else if (data.mode_id === 'replay_auto') {
                setTimeout(() => startGameLoop(), 300);
            } else {
                stopGameLoop();
                // 进入打谱研究模式，请求棋谱列表
                send({ type: 'list_games' });
            }
            break;

        case 'evolution_status':
            if ($('evo-gen')) $('evo-gen').textContent = `第${data.gen}代`;
            if ($('evo-patterns')) $('evo-patterns').textContent = `模式:${data.patterns}`;
            if ($('evo-idioms')) $('evo-idioms').textContent = `成语:${data.idioms}`;
            break;

        case 'game_info':
            showGameInfo(data);
            break;

        case 'game_over':
            addCommentary('━━━ 终局 ━━━');
            data.message.split('\n').filter(l => l.trim()).forEach(l => addCommentary(l));
            break;

        case 'reset':
            stopGameLoop();
            isPaused = false;
            if (sceneGame) sceneGame.clearBoard();
            if (sceneLiberty) sceneLiberty.clearBoard();
            if (sceneLiberty) sceneLiberty.clearLiberties();
            const feed = $('commentary-feed');
            if (feed) feed.innerHTML = '';
            if ($('idiom-text')) $('idiom-text').textContent = '';
            if ($('idiom-meaning')) $('idiom-meaning').textContent = '';
            $('game-name').textContent = '';
            $('game-progress').textContent = '';
            $('mode-indicator').textContent = '';
            // 清除 AI 对弈推理日志
            const llmMsg = $('llm-messages');
            if (llmMsg) llmMsg.innerHTML = '<div class="llm-msg system">等待 AI 对弈开始…</div>';
            break;

        case 'upload_result':
            if (data.ok) {
                showToast(`✅ 棋谱导入成功 (${data.moves_count}手)`, 'success');
                // 刷新棋谱列表并自动加载
                send({ type: 'list_games' });
                setTimeout(() => {
                    send({ type: 'load_game', id: data.game_id });
                }, 200);
            } else {
                showToast(`❌ 导入失败: ${data.message || '未知错误'}`, 'error');
            }
            break;

        // ─── AI 对弈推理事件 ──────────────────
        case 'llm_request':
            {
                const m = $('llm-messages');
                if (!m) break;
                // 首次事件到达时清除占位文字
                const sysMsg = m.querySelector('.llm-msg.system');
                if (sysMsg) sysMsg.remove();
                // 收起旧的重叠展开
                const existing = m.querySelectorAll('.llm-msg.request');
                if (existing.length > 3) existing[0].remove();
                const div = document.createElement('div');
                div.className = 'llm-msg request';
                const shortPrompt = (data.user_prompt || '').slice(0, 200);
                const colorSymbol = data.color === '黑' ? '⚫' : '⚪';
                div.innerHTML = `<strong>${colorSymbol} ${data.player} 思考中…</strong><br><span class="llm-prompt-preview">${escapeHtml(shortPrompt)}${(data.user_prompt||'').length>200?'…':''}</span>`;
                div.dataset.color = data.color === '黑' ? 'black' : 'white';
                div.addEventListener('click', () => div.classList.toggle('expanded'));
                m.appendChild(div);
                m.scrollTop = m.scrollHeight;
            }
            break;

        case 'llm_response':
            {
                const m = $('llm-messages');
                if (!m) break;
                const div = document.createElement('div');
                div.className = 'llm-msg response';
                const text = data.text || '';
                // 截取 MOVE 行和前后短句做预览
                let preview = text.slice(0, 300);
                if (text.length > 300) preview += '…';
                const colorSymbol = data.player === '赵刚' ? '⚫' : '⚪';
                div.innerHTML = `<strong>${colorSymbol} ${data.player} 落子: </strong><span class="llm-response-preview">${escapeHtml(preview)}</span>`;
                div.dataset.color = data.player === '赵刚' ? 'black' : 'white';
                div.addEventListener('click', () => div.classList.toggle('expanded'));
                m.appendChild(div);
                m.scrollTop = m.scrollHeight;
            }
            break;

        case 'chat_disabled':
            {
                const m = $('llm-messages');
                if (m) {
                    const msg = document.createElement('div');
                    msg.className = 'llm-msg system';
                    msg.textContent = '⚠️ ' + (data.message || '对话功能已禁用');
                    m.appendChild(msg);
                    m.scrollTop = m.scrollHeight;
                }
            }
            break;

        case 'game_list':
            populateGameSelector(data.games || []);
            break;

        case 'move_list':
            populateMoveList(data);
            // also update game info/progress
            showGameInfo({ name: data.game_name, total_moves: data.total_moves, current_move: data.current_move, black_player: data.black_player, white_player: data.white_player });
            if (data.game_name) {
                showToast(`📖 已加载棋谱: ${data.game_name} (${data.total_moves}手)`, 'success', 2500);
            }
            break;

        case 'error':
            console.error('Server error:', data.message);
            showToast(data.message || '操作失败', 'error');
            break;

        // ─── AI棋评 流式输出（逐行、最多5行） ──
        case 'ai_commentary_start':
            window._commentaryBuffer = '';
            {
                const overlay = $('commentary-overlay');
                if (overlay) {
                    overlay.innerHTML = '';
                    overlay.style.display = 'flex';
                    overlay.style.opacity = '1';
                }
            }
            break;

        case 'ai_commentary_chunk':
            window._commentaryBuffer += data.text || '';
            {
                const overlay = $('commentary-overlay');
                if (!overlay) break;
                // 按句号分行，每句一行
                const segments = window._commentaryBuffer.split('。');
                const complete = segments.slice(0, -1).filter(s => s.trim()).map(s => s + '。');
                const partial = segments[segments.length - 1] || '';
                const showLines = complete.slice(-2);
                if (partial.trim()) showLines.push(partial);
                overlay.innerHTML = showLines.map(l =>
                    `<div class="commentary-line">${l}</div>`
                ).join('');
            }
            break;

        case 'ai_commentary_end':
            {
                const text = window._commentaryBuffer || '';
                window._commentaryBuffer = '';
                if (text) {
                    text.split('。').filter(l => l.trim()).forEach(l => addCommentary(l.trim() + '。'));
                    showToast('🤖 AI棋评完成', 'success', 2000);
                }
                // 3 秒后淡出
                setTimeout(() => {
                    const overlay = $('commentary-overlay');
                    if (!overlay) return;
                    overlay.style.transition = 'opacity 0.8s';
                    overlay.style.opacity = '0';
                    setTimeout(() => {
                        overlay.style.display = 'none';
                        overlay.innerHTML = '';
                        overlay.style.transition = '';
                        overlay.style.opacity = '1';
                    }, 800);
                }, 3000);
            }
            break;
    }
}

// ─── Toast 通知 ──────────────────────────

function showToast(message, type = 'info', duration = 3500) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.4s';
        setTimeout(() => toast.remove(), 400);
    }, duration);
}

function populateGameSelector(games) {
    const sel = $('game-selector');
    if (!sel) return;
    const prevVal = sel.value; // 保存当前选中值
    sel.innerHTML = '<option value="">-- 选择棋谱 --</option>';
    for (const g of games) {
        const opt = document.createElement('option');
        opt.value = g.id;
        const label = g.is_study ? '📖 ' : '';
        opt.textContent = `${label}#${g.id} ${g.name}（${g.moves}手）`;
        sel.appendChild(opt);
    }
    // 恢复选中状态（如果该选项仍存在）
    if (prevVal) sel.value = prevVal;
}

function populateMoveList(data) {
    const container = $('move-list-inner');
    if (!container) return;
    container.innerHTML = '';
    const moves = data.moves || [];
    for (const m of moves) {
        const div = document.createElement('div');
        div.className = 'move-line';
        // structure: "1. 黑 K10  (key label)"
        const num = document.createElement('span');
        num.className = 'move-num';
        num.textContent = `${m.move_number}. `;
        const col = document.createElement('span');
        col.className = 'move-color';
        col.textContent = m.color === '黑' ? '⚫' : '⚪';
        const coord = document.createElement('span');
        coord.className = 'move-coord';
        coord.textContent = ` ${m.coord_sgf}`;
        div.appendChild(num);
        div.appendChild(col);
        div.appendChild(coord);
        if (m.is_key && m.key_label) {
            const lbl = document.createElement('span');
            lbl.className = 'move-key';
            lbl.textContent = `  · ${m.key_label}`;
            div.appendChild(lbl);
        }
        if (m.annotation) {
            const ann = document.createElement('div');
            ann.className = 'move-annotation';
            ann.textContent = m.annotation;
            div.appendChild(ann);
        }
        // click to goto this move (server expects 1-based index 'index')
        div.addEventListener('click', () => {
            send({ type: 'goto_move', index: m.move_number });
        });
        container.appendChild(div);
    }
}

function syncBoardToBoth(board) {
    if (sceneGame) {
        sceneGame.clearBoard();
        for (let r = 0; r < board.length; r++) {
            for (let c = 0; c < board[r].length; c++) {
                if (board[r][c] !== 0) sceneGame.placeStone(r, c, board[r][c]);
            }
        }
    }
    if (sceneLiberty) {
        sceneLiberty.clearBoard();
        for (let r = 0; r < board.length; r++) {
            for (let c = 0; c < board[r].length; c++) {
                if (board[r][c] !== 0) sceneLiberty.placeStone(r, c, board[r][c]);
            }
        }
    }
}

// ─── 游戏循环 ──────────────────────────

function startGameLoop() {
    stopGameLoop();
    if (isPaused) return;
    moveTimer = setInterval(() => send({ type: 'next_move' }), moveInterval);
}

function stopGameLoop() {
    if (moveTimer) { clearInterval(moveTimer); moveTimer = null; }
}

// ─── UI 更新 ───────────────────────────

function addCommentary(text) {
    const feed = $('commentary-feed');
    if (!feed) return;
    const div = document.createElement('div');
    div.className = 'comment-line';
    div.textContent = text;
    feed.appendChild(div);
    feed.scrollTop = feed.scrollHeight;
}

function showIdiom(data) {
    const text = $('idiom-text');
    const meaning = $('idiom-meaning');
    if (data.idiom && text) {
        text.textContent = `『${data.idiom}』`;
        text.className = `idiom-mood-${data.mood || 'neutral'}`;
    }
    if (data.meaning && meaning) {
        meaning.textContent = `${data.meaning}  [${data.shape || ''} · ${Math.round(data.score || 0)}分]`;
    }
}

function showGameInfo(data) {
    // 显示黑白棋手名字
    const playersEl = $('game-players');
    if (playersEl && (data.black_player || data.white_player)) {
        const black = data.black_player || '?';
        const white = data.white_player || '?';
        playersEl.innerHTML = `<span class="player-black">⚫ ${black}</span> <span class="player-vs">vs</span> <span class="player-white">⚪ ${white}</span>`;
    } else if (playersEl) {
        playersEl.innerHTML = '';
    }
    if (data.name) $('game-name').textContent = `『${data.name}』`;
    if (data.total_moves > 0 && data.current_move > 0) {
        const pct = Math.round((data.current_move / data.total_moves) * 100);
        const barLen = 20;
        const filled = Math.round(pct / 100 * barLen);
        $('game-progress').textContent =
            `${data.color || ''} ${data.position || ''}  ${'█'.repeat(filled)}${'░'.repeat(barLen - filled)}  ${data.current_move}/${data.total_moves}`;
    } else if (data.total_moves > 0) {
        $('game-progress').textContent = `共 ${data.total_moves} 手，准备开始`;
    } else {
        $('game-progress').textContent = `已走 ${data.current_move || 0} 手`;
    }
}

function showStatus(text) {
    const el = $('status-indicator');
    if (el) el.textContent = text;
}

// ─── 上传棋谱 ────────────────────────

function uploadGame() {
    const textArea = $('upload-text');
    const nameInput = $('upload-name');
    if (!textArea) return;
    const text = textArea.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    $('upload-result').textContent = '解析中...';
    $('upload-result').style.color = '#6a8a7a';
    send({ type: 'upload_game', text: text, name: nameInput ? nameInput.value.trim() : null });
}

function initUpload() {
    const sendBtn = $('upload-send');
    if (sendBtn) sendBtn.addEventListener('click', uploadGame);
    const textArea = $('upload-text');
    if (textArea) {
        textArea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.ctrlKey) {
                e.preventDefault();
                uploadGame();
            }
        });
    }
}

// ─── 键盘控制 ──────────────────────────

document.addEventListener('keydown', (e) => {
    // 如果焦点在输入框内，不处理快捷键
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    switch (e.key) {
        case ' ':
            e.preventDefault();
            isPaused = !isPaused;
            addCommentary(isPaused ? '⏸ 暂停' : '▶ 继续');
            if (!isPaused && (gameMode === 'ai_selfplay' || gameMode === 'replay_auto')) startGameLoop();
            break;
        case '=':
        case '+':
            moveInterval = Math.max(300, moveInterval - 200);
            addCommentary(`加速: ${(moveInterval/1000).toFixed(1)}s/步`);
            if (!isPaused) { stopGameLoop(); startGameLoop(); }
            break;
        case '-':
            moveInterval = Math.min(5000, moveInterval + 200);
            addCommentary(`减速: ${(moveInterval/1000).toFixed(1)}s/步`);
            if (!isPaused) { stopGameLoop(); startGameLoop(); }
            break;
        case 'm': case 'M':
            stopGameLoop();
            send({ type: 'toggle_mode' });
            break;
        case 'r': case 'R':
            stopGameLoop();
            send({ type: 'reset' });
            break;
        case 'ArrowRight':
            e.preventDefault();
            send({ type: 'next_move' });
            break;
        case 'ArrowLeft':
            e.preventDefault();
            send({ type: 'prev_move' });
            break;
    }
});

// ─── 按钮事件 ──────────────────────────
// 在 DOM 加载后绑定（main 中调用 setupButtons）

function closeStudyPopup() {
    const popup = $('study-popup');
    if (popup) popup.classList.add('collapsed');
}

function setupButtons() {
    const btnModeAi = $('btn-mode-ai');
    const btnModeStudy = $('btn-mode-study');
    const btnReset = $('btn-reset');
    const btnPrev = $('btn-prev');
    const btnNext = $('btn-next');
    const btnSpeedDown = $('btn-speed-down');
    const btnSpeedUp = $('btn-speed-up');
    const btnPause = $('btn-pause');
    const btnStop = $('btn-stop');

    if (btnModeAi) btnModeAi.addEventListener('click', () => {
        stopGameLoop(); isPaused = false;
        send({ type: 'set_mode', mode_id: 'ai_selfplay' });
        const popup = $('study-popup');
        if (popup) popup.classList.add('collapsed');
    });
    if (btnModeStudy) btnModeStudy.addEventListener('click', () => {
        stopGameLoop(); isPaused = false;
        send({ type: 'set_mode', mode_id: 'replay' });
        const popup = $('study-popup');
        if (popup) popup.classList.remove('collapsed');
    });
    if (btnReset) btnReset.addEventListener('click', () => { stopGameLoop(); send({ type: 'reset' }); closeStudyPopup(); });
    if (btnPrev) btnPrev.addEventListener('click', () => send({ type: 'prev_move' }));
    if (btnNext) btnNext.addEventListener('click', () => send({ type: 'next_move' }));
    if (btnSpeedDown) btnSpeedDown.addEventListener('click', () => {
        moveInterval = Math.min(5000, moveInterval + 200);
        if (!isPaused) { stopGameLoop(); startGameLoop(); }
    });
    if (btnSpeedUp) btnSpeedUp.addEventListener('click', () => {
        moveInterval = Math.max(300, moveInterval - 200);
        if (!isPaused) { stopGameLoop(); startGameLoop(); }
    });
    if (btnPause) btnPause.addEventListener('click', () => {
        isPaused = !isPaused;
        addCommentary(isPaused ? '⏸ 暂停' : '▶ 继续');
        if (!isPaused && (gameMode === 'ai_selfplay' || gameMode === 'replay_auto')) startGameLoop();
    });
    if (btnStop) btnStop.addEventListener('click', () => {
        stopGameLoop();
        send({ type: 'stop' });
        closeStudyPopup();
    });

    // 弹出面板关闭按钮
    const btnCloseStudy = $('btn-close-study');
    if (btnCloseStudy) btnCloseStudy.addEventListener('click', () => {
        const popup = $('study-popup');
        if (popup) popup.classList.add('collapsed');
    });

    // 棋谱选择器
    const gameSelector = $('game-selector');
    if (gameSelector) {
        gameSelector.addEventListener('change', () => {
            const id = parseInt(gameSelector.value);
            if (id && ws && ws.readyState === WebSocket.OPEN) {
                send({ type: 'load_game', id });
            }
        });
    }

    // 跳转按钮
    const btnGoto = $('btn-goto');
    if (btnGoto) {
        btnGoto.addEventListener('click', () => {
            const num = parseInt($('input-move-num')?.value || '1');
            send({ type: 'goto_move', index: num });
        });
    }

    // 自动打谱按钮
    const btnAutoPlay = $('btn-auto-play');
    if (btnAutoPlay) {
        btnAutoPlay.addEventListener('click', () => {
            send({ type: 'set_mode', mode_id: 'replay_auto' });
        });
    }

    // SGF 文件加载
    const btnLoadSgf = $('btn-load-sgf');
    const sgfInput = $('sgf-file-input');
    if (btnLoadSgf && sgfInput) {
        btnLoadSgf.addEventListener('click', () => sgfInput.click());
        sgfInput.addEventListener('change', (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            showToast(`📂 正在加载: ${file.name}`, 'info', 2000);
            const reader = new FileReader();
            reader.onload = (ev) => {
                const arrayBuffer = ev.target?.result;
                if (arrayBuffer && ws && ws.readyState === WebSocket.OPEN) {
                    // 以 Base64 发送，让服务端自动探测编码
                    const bytes = new Uint8Array(arrayBuffer);
                    let binary = '';
                    for (let i = 0; i < bytes.length; i++) {
                        binary += String.fromCharCode(bytes[i]);
                    }
                    const b64 = btoa(binary);
                    send({ type: 'load_sgf', sgf_b64: b64 });
                }
            };
            reader.onerror = () => {
                showToast(`❌ 文件读取失败: ${file.name}`, 'error');
            };
            reader.readAsArrayBuffer(file);
            // 重置 input 以便再次选择同一文件
            sgfInput.value = '';
        });
    }
    // ─── AI棋评按钮 ────────────────────
    const btnAiCommentary = $('btn-ai-commentary');
    if (btnAiCommentary) {
        btnAiCommentary.addEventListener('click', () => {
            const popup = $('study-popup');
            if (popup) popup.classList.add('collapsed');
            if (ws && ws.readyState === WebSocket.OPEN) {
                send({ type: 'ai_commentary' });
                showToast('🤖 AI棋评生成中...', 'info', 3000);
            }
        });
    }

    // ─── LLM对话按钮 ────────────────────
    const btnToggleLlm = $('btn-toggle-llm');
    const btnCloseLlm = $('btn-close-llm');
    const btnLlmSend = $('btn-llm-send');
    const llmInput = $('llm-input');
    const llmMessages = $('llm-messages');

    function toggleLlmOverlay() {
        const overlay = $('llm-overlay');
        if (overlay) overlay.classList.toggle('visible');
    }

    if (btnToggleLlm) btnToggleLlm.addEventListener('click', toggleLlmOverlay);
    if (btnCloseLlm) btnCloseLlm.addEventListener('click', toggleLlmOverlay);

    function sendLlmMessage() {
        if (!llmInput || !llmMessages) return;
        const text = llmInput.value.trim();
        if (!text) return;
        // 添加用户消息
        const userMsg = document.createElement('div');
        userMsg.className = 'llm-msg user';
        userMsg.textContent = text;
        llmMessages.appendChild(userMsg);
        llmInput.value = '';
        llmMessages.scrollTop = llmMessages.scrollHeight;
        // 发送给后端
        if (ws && ws.readyState === WebSocket.OPEN) {
            send({ type: 'chat', text });
        } else {
            const botMsg = document.createElement('div');
            botMsg.className = 'llm-msg bot';
            botMsg.textContent = '⚠️ 未连接到服务器';
            llmMessages.appendChild(botMsg);
        }
    }

    if (btnLlmSend) btnLlmSend.addEventListener('click', sendLlmMessage);
    if (llmInput) {
        llmInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') sendLlmMessage();
        });
    }

    // ─── 视图模式切换 ────────────────────
    let currentViewMode = 'dual-h';
    let rotationEnabled = false;

    /** 重置当前视图的相机状态（不含旋转） */
    function applyCurrentView() {
        // 先关闭平铺和旋转
        if (sceneGame) { sceneGame.setFlatView(false); sceneGame.setInteractive(true); sceneGame.setAutoRotate(false); }
        if (sceneLiberty) { sceneLiberty.setFlatView(false); sceneLiberty.setInteractive(true); sceneLiberty.setAutoRotate(false); }

        switch (currentViewMode) {
            case 'dual-h':
                // 并排：默认视角
                break;
            case 'flat':
                // 平铺俯视
                if (sceneGame) sceneGame.setFlatView(true);
                if (sceneLiberty) sceneLiberty.setFlatView(true);
                break;
        }

        // 如果旋转开关已打开，再叠加旋转
        if (rotationEnabled) {
            if (sceneGame) { sceneGame.setInteractive(false); sceneGame.setAutoRotate(true); }
            if (sceneLiberty) { sceneLiberty.setInteractive(false); sceneLiberty.setAutoRotate(true); }
        }

        setTimeout(() => {
            if (sceneGame) sceneGame._onResize();
            if (sceneLiberty) sceneLiberty._onResize();
        }, 50);
    }

    function applyViewMode(mode) {
        currentViewMode = mode;
        document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
        const activeBtn = $(mode === 'dual-h' ? 'btn-view-dual-h' : 'btn-view-flat');
        if (activeBtn) activeBtn.classList.add('active');
        applyCurrentView();
    }

    function toggleRotation() {
        rotationEnabled = !rotationEnabled;
        const btn = $('btn-toggle-rotate');
        if (btn) btn.classList.toggle('active', rotationEnabled);
        applyCurrentView();
    }

    const btnViewDualH = $('btn-view-dual-h');
    const btnViewFlat = $('btn-view-flat');
    const btnToggleRotate = $('btn-toggle-rotate');

    if (btnViewDualH) btnViewDualH.addEventListener('click', () => applyViewMode('dual-h'));
    if (btnViewFlat) btnViewFlat.addEventListener('click', () => applyViewMode('flat'));
    if (btnToggleRotate) btnToggleRotate.addEventListener('click', toggleRotation);

    // 初始化弹窗拖拽与缩放
    initStudyPopupDrag();
}

    // 保存/加载弹窗状态
    function saveStudyPopupState(state) {
        try { localStorage.setItem('study_popup_state', JSON.stringify(state)); } catch (e) { }
    }

    function loadStudyPopupState() {
        try { const s = localStorage.getItem('study_popup_state'); return s ? JSON.parse(s) : null; } catch (e) { return null; }
    }

    function initStudyPopupDrag() {
        const popup = $('study-popup');
        const header = document.querySelector('#study-popup #study-popup-header');
        if (!popup || !header) return;

        // apply saved state
        const saved = loadStudyPopupState();
        if (saved) {
            if (saved.left) popup.style.left = saved.left;
            if (saved.top) { popup.style.top = saved.top; popup.style.bottom = 'auto'; }
            if (saved.width) popup.style.width = saved.width;
            if (saved.height) { popup.style.maxHeight = 'none'; popup.style.height = saved.height; }
        }

        let drag = null;
        // 拖动功能已禁用，保留代码
        // header.addEventListener('mousedown', (e) => {
        //     e.preventDefault();
        //     drag = { sx: e.clientX, sy: e.clientY, ol: popup.getBoundingClientRect().left, ot: popup.getBoundingClientRect().top };
        //     document.addEventListener('mousemove', onDragMove);
        //     document.addEventListener('mouseup', onDragEnd);
        // });

        function onDragMove(e) {
            if (!drag) return;
            const nx = drag.ol + (e.clientX - drag.sx);
            const ny = drag.ot + (e.clientY - drag.sy);
            popup.style.left = nx + 'px';
            popup.style.top = ny + 'px';
            popup.style.bottom = 'auto';
        }

        function onDragEnd() {
            if (!drag) return;
            const rect = popup.getBoundingClientRect();
            saveStudyPopupState({ left: popup.style.left, top: popup.style.top, width: popup.style.width, height: popup.style.height });
            drag = null;
            document.removeEventListener('mousemove', onDragMove);
            document.removeEventListener('mouseup', onDragEnd);
        }

        // add resize handle
        let handle = popup.querySelector('.resize-handle');
        // 尺寸调整功能已禁用，保留代码
        // if (!handle) {
        //     handle = document.createElement('div');
        //     handle.className = 'resize-handle';
        //     popup.appendChild(handle);
        // }

        let resizing = null;
        // handle.addEventListener('mousedown', (e) => {
        //     e.stopPropagation(); e.preventDefault();
        //     const rect = popup.getBoundingClientRect();
        //     resizing = { sw: rect.width, sh: rect.height, sx: e.clientX, sy: e.clientY };
        //     document.addEventListener('mousemove', onResizeMove);
        //     document.addEventListener('mouseup', onResizeEnd);
        // });

        function onResizeMove(e) {
            if (!resizing) return;
            const dw = e.clientX - resizing.sx;
            const dh = e.clientY - resizing.sy;
            const newW = Math.max(300, resizing.sw + dw);
            const newH = Math.max(120, resizing.sh + dh);
            popup.style.width = newW + 'px';
            popup.style.height = newH + 'px';
            popup.style.maxHeight = 'none';
        }

        function onResizeEnd() {
            if (!resizing) return;
            saveStudyPopupState({ left: popup.style.left, top: popup.style.top, width: popup.style.width, height: popup.style.height });
            resizing = null;
            document.removeEventListener('mousemove', onResizeMove);
            document.removeEventListener('mouseup', onResizeEnd);
        }
    }

// ─── 启动 ──────────────────────────────

async function main() {
    // 双棋盘：左 — 对战棋盘（纯棋子），右 — 气形棋盘（棋子+气）
    sceneGame = new GoScene('board-game-container', { renderLiberties: false });
    sceneLiberty = new GoScene('board-liberty-container', { renderLiberties: true });

    // 暴露调试接口
    window.__sceneGame = sceneGame;
    window.__sceneLiberty = sceneLiberty;
    window.__boardState = () => boardState;

    // 隐藏加载屏幕
    const loading = document.getElementById('loading');
    if (loading) {
        setTimeout(() => loading.classList.add('hidden'), 600);
    }

    setupButtons();
    initUpload();
    connect();
    setTimeout(() => {
        if (gameMode === 'ai_selfplay' && !isPaused) startGameLoop();
    }, 1500);
}

main();