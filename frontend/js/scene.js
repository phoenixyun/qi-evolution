/**
 * 3D 围棋 · 气之进化 — Three.js 场景渲染引擎
 * ==============================================
 * 19路棋盘 · 双凸棋子 · 连片气形可视化 · 沉浸式视觉
 *
 * 设计理念:
 * - 气形不再用孤立柱子，而是用凸包多边形 + 发光边缘 + 地面脉冲光点
 * - 黑方气 = 半透墨黑（烟墨暗影），白方气 = 白气（银雾玉质），一眼可辨
 * - 棋子使用双凸透镜形状 (Biconvex) 模拟真实围棋质感
 * - 全场景 ACES 色调映射，物理级光照
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ─── 常量 ──────────────────────────────

const BOARD_SIZE = 19;
const OFFSET = 9;
const CELL = 0.95;
const STONE_RADIUS = 0.20;          /* 棋子缩小到42%格宽 — 让气显现！ */
const STONE_SEGMENTS = 24;
const STONE_OPACITY_BLACK = 0.65;  /* 黑棋半透 — 气韵可穿透 */
const STONE_OPACITY_WHITE = 0.50;  /* 白棋更透 — 玉质感 */

const C = {
    // ── 棋盘：榧木色 (Kaya) 传统围棋盘配色 ──
    boardBase: 0x8b5a2b,       // 鞍褐 — 棋盘底座厚木框
    boardSurface: 0xdeb887,    // 原木色 — 榧木面板 (Burlywood)
    gridLine: 0x2a1508,        // 深褐 — 太刀盛漆线
    gridEdge: 0x1a0a00,        // 极深褐 — 边框
    starPoint: 0x0a0200,       // 纯黑 — 星位
    // ── 棋子 ──
    black: 0x1a1a2e,
    blackEmissive: 0x2244aa,
    blackHighlight: 0x3366cc,
    white: 0xeee8dd,
    whiteEmissive: 0x887755,
    whiteHighlight: 0xffd700,
    // ── 气形 ──
    // 黑方 → 半透明墨灰烟影
    libBlack: 0x2a1e1e,
    libBlackDark: 0x0e0808,
    libBlackEdge: 0x4a3333,
    // 白方 → 白气（珍珠白/银雾，契合白子玉质感）
    libWhite: 0xeeeeff,
    libWhiteDark: 0x555577,
    libWhiteEdge: 0xffffff,
    // ── 场景 ──
    bg: 0x0d0b08,
    fog: 0x0d0b08,
};

// ─── 工具函数 ──────────────────────────

function wpos(r, c) {
    return new THREE.Vector3(
        (c - OFFSET) * CELL,
        0,
        (r - OFFSET) * CELL
    );
}

/** 2D 凸包 (Monotone Chain) — 输入 [{x,z}], 返回 CCW 有序数组 */
function convexHull2D(points) {
    if (points.length <= 2) return points;
    const sorted = [...points].sort((a, b) => a.x - b.x || a.z - b.z);
    const cross = (o, a, b) => (a.x - o.x) * (b.z - o.z) - (a.z - o.z) * (b.x - o.x);
    let lower = [];
    for (const p of sorted) {
        while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
        lower.push(p);
    }
    let upper = [];
    for (const p of sorted.reverse()) {
        while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
        upper.push(p);
    }
    lower.pop(); upper.pop();
    return lower.concat(upper);
}

/** 双凸透镜片 (biconvex) — 两个球冠拼接 */
function createBiconvexStone(radius, segments, material) {
    const group = new THREE.Group();
    const geo = new THREE.SphereGeometry(radius, segments, segments, 0, Math.PI * 2, 0, Math.PI * 0.48);
    const top = new THREE.Mesh(geo, material);
    top.position.y = radius * 0.55;
    group.add(top);
    const bottom = new THREE.Mesh(geo, material);
    bottom.position.y = -radius * 0.55;
    bottom.rotation.x = Math.PI;
    group.add(bottom);
    return group;
}

// ─── 场景类 ──────────────────────────────

export class GoScene {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        this.stones = new Map();
        this.libertyMeshes = [];
        this.t = 0;
        this.renderLiberties = options.renderLiberties !== false;
        this._cameraDefaultPos = null;   // 平铺模式恢复用
        this._cameraDefaultTarget = null;
        this._isFlat = false;
        this._initRenderer();
        this._initScene();
        this._initLights();
        this._createBoard();
        this._createStars();
        this._animate();
        window.addEventListener('resize', () => this._onResize());
    }

    // ─── 初始化 ──────────────────────────

    _initRenderer() {
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.renderer = new THREE.WebGLRenderer({
            antialias: true,
            powerPreference: 'high-performance',
        });
        this.renderer.setSize(w, h);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.0;
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.container.appendChild(this.renderer.domElement);
    }

    _initScene() {
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(C.bg);
        this.scene.fog = new THREE.FogExp2(C.fog, 0.003);

        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 120);
        this.camera.position.set(0, 16, 22);
        this.camera.lookAt(0, 0, 0);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.target.set(0, 0, 0);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.06;
        // 禁用用户交互（拖动/缩放），保留自动旋转
        this.controls.enableRotate = false;
        this.controls.enablePan = false;
        this.controls.enableZoom = false;
        this.controls.minDistance = 8;
        this.controls.maxDistance = 55;
        this.controls.maxPolarAngle = Math.PI / 2.15;
        this.controls.minPolarAngle = Math.PI / 12;
        this.controls.autoRotate = true;
        this.controls.autoRotateSpeed = 0.15;
        this.controls.update();

        // 保存默认视角，供 setFlatView 恢复
        this._cameraDefaultPos = this.camera.position.clone();
        this._cameraDefaultTarget = this.controls.target.clone();
    }

    _initLights() {
        const ambient = new THREE.AmbientLight(0x443322, 0.5);
        const ambient2 = new THREE.AmbientLight(0x554433, 0.3);
        this.scene.add(ambient2);
        this.scene.add(ambient);

        const hemi = new THREE.HemisphereLight(0xccaa88, 0x332211, 0.6);
        this.scene.add(hemi);

        const sun = new THREE.DirectionalLight(0xffeedd, 1.4);
        sun.position.set(10, 16, 8);
        sun.castShadow = true;
        sun.shadow.mapSize.width = 2048;
        sun.shadow.mapSize.height = 2048;
        const d = 14;
        sun.shadow.camera.left = -d; sun.shadow.camera.right = d;
        sun.shadow.camera.top = d; sun.shadow.camera.bottom = -d;
        sun.shadow.camera.near = 1; sun.shadow.camera.far = 30;
        sun.shadow.bias = -0.001;
        this.scene.add(sun);

        const fill = new THREE.DirectionalLight(0xccaa88, 0.3);
        fill.position.set(-6, 8, -8);
        this.scene.add(fill);

        const rim = new THREE.DirectionalLight(0xcca888, 0.15);
        rim.position.set(-5, -2, 10);
        this.scene.add(rim);
    }

    // ─── 棋盘 ────────────────────────────

    _createBoard() {
        const g = new THREE.Group();

        const baseSize = (BOARD_SIZE - 1) * CELL + 1.6;
        const base = new THREE.Mesh(
            new THREE.BoxGeometry(baseSize, 0.5, baseSize),
            new THREE.MeshStandardMaterial({
                color: C.boardBase,
                roughness: 0.55,
                metalness: 0.0,
            })
        );
        base.position.y = -0.25;
        base.receiveShadow = true;
        g.add(base);

        const surfSize = (BOARD_SIZE - 1) * CELL + 0.8;
        const surfMat = new THREE.MeshStandardMaterial({
            color: C.boardSurface,
            roughness: 0.7,
            metalness: 0.0,
        });
        const surf = new THREE.Mesh(
            new THREE.PlaneGeometry(surfSize, surfSize),
            surfMat
        );
        surf.rotation.x = -Math.PI / 2;
        surf.position.y = 0.005;
        surf.receiveShadow = true;
        g.add(surf);

        const lm = new THREE.LineBasicMaterial({
            color: C.gridLine,
            transparent: true,
            opacity: 0.65,
        });
        const half = OFFSET * CELL;
        for (let i = 0; i < BOARD_SIZE; i++) {
            const pos = (i - OFFSET) * CELL;
            g.add(new THREE.Line(
                new THREE.BufferGeometry().setFromPoints([
                    new THREE.Vector3(-half, 0.015, pos),
                    new THREE.Vector3(half, 0.015, pos),
                ]), lm
            ));
            g.add(new THREE.Line(
                new THREE.BufferGeometry().setFromPoints([
                    new THREE.Vector3(pos, 0.015, -half),
                    new THREE.Vector3(pos, 0.015, half),
                ]), lm
            ));
        }

        const em = new THREE.LineBasicMaterial({
            color: C.gridEdge,
            transparent: true,
            opacity: 0.4,
        });
        const hf = half + 0.5;
        const corners = [
            new THREE.Vector3(-hf, 0.015, -hf),
            new THREE.Vector3(hf, 0.015, -hf),
            new THREE.Vector3(hf, 0.015, hf),
            new THREE.Vector3(-hf, 0.015, hf),
            new THREE.Vector3(-hf, 0.015, -hf),
        ];
        g.add(new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(corners), em
        ));

        const starMat = new THREE.MeshBasicMaterial({ color: C.starPoint });
        const starGeo = new THREE.SphereGeometry(0.10, 8, 8);
        const starPositions = [
            [3,3],[3,9],[3,15],
            [9,3],[9,9],[9,15],
            [15,3],[15,9],[15,15],
        ];
        for (const [r, c] of starPositions) {
            const p = wpos(r, c);
            const s = new THREE.Mesh(starGeo, starMat);
            s.position.set(p.x, 0.025, p.z);
            g.add(s);
        }

        const cornerMat = new THREE.MeshBasicMaterial({
            color: 0x6a4a2a,
            transparent: true,
            opacity: 0.3,
        });
        const cornerGeo = new THREE.SphereGeometry(0.06, 6, 6);
        for (const sign of [[-1,-1],[-1,1],[1,-1],[1,1]]) {
            const c2 = new THREE.Mesh(cornerGeo, cornerMat);
            c2.position.set(sign[0] * (half + 0.35), 0.02, sign[1] * (half + 0.35));
            g.add(c2);
        }

        this.scene.add(g);
    }

    // ─── 星空 + 星座系统 ──────────────────

    _createStars() {
        // ── 800 颗背景星 ──
        const count = 800;
        const pos = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos(2 * Math.random() - 1);
            const r = 40 + Math.random() * 30;
            pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
            pos[i * 3 + 1] = Math.abs(r * Math.cos(phi));
            pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        const mat = new THREE.PointsMaterial({
            color: 0x6688cc, size: 0.06, transparent: true, opacity: 0.45,
            sizeAttenuation: true, fog: false,
        });
        const starField = new THREE.Points(geo, mat);
        starField.position.y = 5;
        this.scene.add(starField);

        // ── 星座亮星点 ──
        const conDefs = [
            { name:'猎户', color:0xaaccff, stars:[
                [-18,16,-44],[-16,10,-46],[-24, 6,-42],[-12, 5,-47],[-17,14,-43],[-13,12,-45],[ -7,19,-49],[-26,18,-40]
            ] },
            { name:'大熊', color:0xffddaa, stars:[
                [22,12,-48],[26,14,-46],[30,12,-44],[34,16,-42],[30,20,-40],[24,21,-42],[20,18,-46]
            ] },
            { name:'仙后', color:0xccddff, stars:[
                [ 8,24,-50],[12,28,-48],[16,25,-47],[20,28,-46],[24,23,-48]
            ] },
            { name:'天蝎', color:0xff8866, stars:[
                [-30, 8,-40],[-28, 6,-42],[-24, 3,-44],[-20, 0,-46],[-16,-3,-48],[-14,-7,-50],[-18,-12,-48]
            ] },
            { name:'狮子', color:0xffcc88, stars:[
                [12, 6,-52],[16, 4,-50],[20, 0,-48],[22,-4,-46],[18,-7,-48],[14,-4,-50],[10, 8,-51]
            ] },
            { name:'天鹅', color:0x99ccff, stars:[
                [28,20,-38],[32,22,-40],[36,24,-42],[40,22,-44],[44,20,-46],[36,28,-40],[36,18,-44]
            ] },
            { name:'金牛', color:0xffaa66, stars:[
                [-10,18,-36],[ -6,14,-38],[  0,12,-40],[  6,14,-42],[ 12,18,-44],[-14,14,-37]
            ] },
            { name:'天琴', color:0xddddff, stars:[
                [34,26,-48],[30,23,-50],[32,20,-52],[36,21,-51],[38,24,-50]
            ] },
            { name:'双子', color:0xffddee, stars:[
                [14,20,-34],[10,18,-36],[ 6,18,-38],[18,18,-35],[22,18,-37],[14,14,-36]
            ] },
            { name:'天鹰', color:0xccbbff, stars:[
                [-22,22,-36],[-24,20,-38],[-20,20,-38],[-16,18,-40],[-28,18,-40],[-22,16,-42]
            ] },
        ];

        for (const def of conDefs) {
            const pts = def.stars.map(s => new THREE.Vector3(s[0], s[1], s[2]));
            const starGeo = new THREE.BufferGeometry();
            const starArr = new Float32Array(pts.flatMap(v => [v.x, v.y, v.z]));
            starGeo.setAttribute('position', new THREE.BufferAttribute(starArr, 3));
            const starMat = new THREE.PointsMaterial({
                color: def.color, size: 0.16, transparent: true, opacity: 0.8,
                sizeAttenuation: true, fog: false,
            });
            this.scene.add(new THREE.Points(starGeo, starMat));
        }
    }



    // ─── 棋子管理 ────────────────────────

    placeStone(r, c, color) {
        const key = `${r},${c}`;
        if (this.stones.has(key)) return;
        const pos = wpos(r, c);
        const isBlack = color === 1;
        const col = isBlack ? C.black : C.white;
        const emissive = isBlack ? C.blackEmissive : C.whiteEmissive;

        /* ── 半透棋子：右边气棋盘保持通透，左边对战棋盘不透 ── */
        const blackOpacity = this.renderLiberties ? STONE_OPACITY_BLACK : 1.0;
        const whiteOpacity = this.renderLiberties ? STONE_OPACITY_WHITE : 1.0;
        const isTransparent = this.renderLiberties;  // 左边不透，右边半透

        let mat;
        if (this.renderLiberties) {
            // 右侧气形棋盘：无光照无阴影，纯色半透
            mat = new THREE.MeshBasicMaterial({
                color: col,
                transparent: true,
                opacity: isBlack ? blackOpacity : whiteOpacity,
                side: THREE.DoubleSide,
                depthWrite: false,
            });
        } else {
            // 左侧对战棋盘：保留 PBR 光照质感
            mat = new THREE.MeshPhysicalMaterial({
                color: col,
                roughness: isBlack ? 0.45 : 0.10,
                metalness: 0.0,
                clearcoat: isBlack ? 0.1 : 0.7,
                clearcoatRoughness: 0.2,
                transparent: false,
                opacity: 1.0,
                emissive: emissive,
                emissiveIntensity: 0.08,
                envMapIntensity: 0.3,
                side: THREE.DoubleSide,
                depthWrite: true,
            });
        }

        const group = createBiconvexStone(STONE_RADIUS, STONE_SEGMENTS, mat);
        group.position.set(pos.x, 0.06, pos.z);
        group.children.forEach(c => { c.castShadow = !this.renderLiberties; });
        this.scene.add(group);

        /* ── 底座微光 — 收到光晕但不主导视觉 ── */
        const glowCol = isBlack ? C.blackHighlight : C.whiteHighlight;
        const glow = new THREE.Mesh(
            new THREE.RingGeometry(STONE_RADIUS * 0.2, STONE_RADIUS * 1.1, 24),
            new THREE.MeshBasicMaterial({
                color: glowCol,
                transparent: true,
                opacity: isBlack ? 0.06 : 0.04,
                side: THREE.DoubleSide,
                depthWrite: false,
            })
        );
        glow.rotation.x = -Math.PI / 2;
        glow.position.set(pos.x, 0.010, pos.z);
        this.scene.add(glow);

        this.stones.set(key, { group, glow, color, pos });
    }

    removeStone(r, c) {
        const key = `${r},${c}`;
        const e = this.stones.get(key);
        if (!e) return;
        this.scene.remove(e.group);
        this.scene.remove(e.glow);
        e.group.children.forEach(ch => { ch.geometry.dispose(); ch.material.dispose(); });
        e.glow.geometry.dispose();
        e.glow.material.dispose();
        this.stones.delete(key);
    }

    pulseStone(r, c, color) {
        const key = `${r},${c}`;
        const e = this.stones.get(key);
        if (!e) return;

        // 高亮光环
        const pos = wpos(r, c);
        const ring = new THREE.Mesh(
            new THREE.TorusGeometry(STONE_RADIUS * 0.85, 0.06, 16, 32),
            new THREE.MeshBasicMaterial({
                color: color === 1 ? 0x66ccff : 0xffcc66,
                transparent: true,
                opacity: 0.9,
                depthTest: false,
                depthWrite: false,
            })
        );
        ring.rotation.x = -Math.PI / 2;
        ring.position.set(pos.x, 0.09, pos.z);
        ring.name = 'pulse-ring';
        this.scene.add(ring);

        // 渐隐动画
        const start = performance.now();
        const duration = 2000;
        const tick = (now) => {
            const elapsed = now - start;
            const t = Math.min(elapsed / duration, 1.0);
            ring.material.opacity = 0.9 * (1 - t);
            ring.scale.setScalar(1 + t * 0.5);
            if (t < 1) {
                requestAnimationFrame(tick);
            } else {
                this.scene.remove(ring);
                ring.geometry.dispose();
                ring.material.dispose();
            }
        };
        requestAnimationFrame(tick);
    }

    clearBoard() {
        for (const [, e] of this.stones) {
            this.scene.remove(e.group);
            this.scene.remove(e.glow);
            e.group.children.forEach(ch => { ch.geometry.dispose(); ch.material.dispose(); });
            e.glow.geometry.dispose();
            e.glow.material.dispose();
        }
        this.stones.clear();
        this.clearLiberties();
    }

    // ─── 气形可视化 ────────────────────────

    renderLibertiesForColor(color, groups) {
        if (!groups || !groups.length) return;

        const isBlackLib = color === 1;
        const hullColor = isBlackLib ? C.libBlack : C.libWhite;
        const hullDark = isBlackLib ? C.libBlackDark : C.libWhiteDark;
        const edgeColor = isBlackLib ? C.libBlackEdge : C.libWhiteEdge;
        const stoneSide = color === 1 ? 2 : 1;  // 对方棋子颜色

        for (const g of groups) {
            const libPositions = g.liberties.map(([r, c]) => {
                const p = wpos(r, c);
                return { x: p.x, z: p.z };
            });
            const count = libPositions.length;
            const intensity = Math.min(1, 0.4 + count * 0.08);

            // ── A. 凸包多边形 — 连片气的大地辉光 ──
            if (count >= 3) {
                const hull = convexHull2D(libPositions);
                if (hull.length >= 3) {
                    const shape = new THREE.Shape();
                    // ⚠️ rotation.x=-π/2 会使 shape.y → world.-z, 所以 z 取反
                    shape.moveTo(hull[0].x, -hull[0].z);
                    for (let i = 1; i < hull.length; i++) shape.lineTo(hull[i].x, -hull[i].z);
                    shape.closePath();
                    const polyMat = new THREE.MeshBasicMaterial({
                        color: hullColor,
                        transparent: true,
                        opacity: 0.35 * intensity,   /* 更亮 */
                        side: THREE.DoubleSide,
                        depthWrite: false,
                    });
                    const poly = new THREE.Mesh(new THREE.ShapeGeometry(shape), polyMat);
                    poly.rotation.x = -Math.PI / 2;
                    poly.position.y = 0.035;
                    poly.userData.isLibertyPoly = true;
                    this.scene.add(poly);
                    this.libertyMeshes.push(poly);

                    const pts = hull.map(p => new THREE.Vector3(p.x, 0.042, p.z));
                    pts.push(pts[0].clone());
                    const lineMat = new THREE.LineBasicMaterial({
                        color: edgeColor,
                        transparent: true,
                        opacity: 0.6 * intensity,
                    });
                    const line = new THREE.Line(
                        new THREE.BufferGeometry().setFromPoints(pts), lineMat
                    );
                    line.userData.isLibertyEdge = true;
                    this.scene.add(line);
                    this.libertyMeshes.push(line);
                }
            }

            // ── B. (已移除) 棋→气蜘蛛网连线 — 视觉杂乱，取消 ──

            // ── C. 发光气点 (地面脉冲光珠，替代竖直立柱) ──
            for (const p of libPositions) {
                const dotMat = new THREE.MeshBasicMaterial({
                    color: hullColor,
                    transparent: true,
                    opacity: 0.55 * intensity,
                });
                const dot = new THREE.Mesh(
                    new THREE.SphereGeometry(0.07, 8, 8),
                    dotMat
                );
                dot.position.set(p.x, 0.04, p.z);
                dot.userData.isLibertyDot = true;
                dot.userData._baseInt = intensity;
                this.scene.add(dot);
                this.libertyMeshes.push(dot);

                /* 地面光环 */
                const ringMat = new THREE.MeshBasicMaterial({
                    color: hullColor,
                    transparent: true,
                    opacity: 0.25 * intensity,
                    side: THREE.DoubleSide,
                    depthWrite: false,
                });
                const ring = new THREE.Mesh(
                    new THREE.RingGeometry(0.08, 0.32, 20),
                    ringMat
                );
                ring.rotation.x = -Math.PI / 2;
                ring.position.set(p.x, 0.012, p.z);
                this.scene.add(ring);
                this.libertyMeshes.push(ring);
            }
        }

        // ── D. 为当前被分析方的每颗棋子添加气色底座辉光 ──
        for (const [, e] of this.stones) {
            if (e.color !== color) continue;
            const libGlow = new THREE.Mesh(
                new THREE.RingGeometry(STONE_RADIUS * 0.5, STONE_RADIUS * 1.8, 24),
                new THREE.MeshBasicMaterial({
                    color: hullColor,
                    transparent: true,
                    opacity: 0.12,
                    side: THREE.DoubleSide,
                    depthWrite: false,
                })
            );
            libGlow.rotation.x = -Math.PI / 2;
            libGlow.position.set(e.pos.x, 0.008, e.pos.z);
            libGlow.userData.isLibertyStoneGlow = true;
            this.scene.add(libGlow);
            this.libertyMeshes.push(libGlow);
        }
    }

    clearLiberties() {
        for (const m of this.libertyMeshes) {
            this.scene.remove(m);
            if (m.geometry) m.geometry.dispose();
            if (m.material) m.material.dispose();
        }
        this.libertyMeshes = [];
    }

    /** 启用/禁用用户交互（拖拽旋转、平移、缩放） */
    setInteractive(enabled) {
        this.controls.enableRotate = enabled;
        this.controls.enablePan = enabled;
        this.controls.enableZoom = enabled;
        // 如果启用交互，关闭 autoRotate 避免抢控制
        if (enabled && this.controls.autoRotate) {
            this.controls.autoRotate = false;
        }
    }

    /** 启用/禁用自动旋转 */
    setAutoRotate(enabled) {
        this.controls.autoRotate = enabled;
        this.controls.autoRotateSpeed = 0.15;
    }

    /** 切换到平铺俯视视角（相机正上方俯拍） */
    setFlatView(enabled) {
        this._isFlat = enabled;
        if (enabled) {
            // 保存当时视角
            this._cameraDefaultPos = this.camera.position.clone();
            this._cameraDefaultTarget = this.controls.target.clone();
            // 移动到正上方俯视
            this.camera.position.set(0, 30, 0.001);
            this.controls.target.set(0, 0, 0);
            this.controls.update();
            this.camera.lookAt(0, 0, 0);
            // 禁用自动旋转，允许用户交互来平移/缩放
            this.controls.autoRotate = false;
            this.controls.enableRotate = false;
            this.controls.enablePan = true;
            this.controls.enableZoom = true;
        } else {
            // 恢复默认视角
            if (this._cameraDefaultPos) {
                this.camera.position.copy(this._cameraDefaultPos);
                this.controls.target.copy(this._cameraDefaultTarget);
                this.controls.update();
                this.camera.lookAt(this._cameraDefaultTarget);
            }
            this.controls.enablePan = false;
            this.controls.enableZoom = false;
        }
    }

    /** 当前是否平铺俯视模式 */
    isFlatView() { return this._isFlat; }

    _onResize() {
        this.camera.aspect = this.container.clientWidth / this.container.clientHeight;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    }

    _animate() {
        requestAnimationFrame(() => this._animate());
        this.controls.update();
        this.t += 0.016;
        const breathe = Math.sin(this.t * 1.2) * 0.5 + 0.5;

        for (let i = 0; i < this.libertyMeshes.length; i++) {
            const m = this.libertyMeshes[i];
            if (!m.geometry || !m.userData) continue;
            const phase = i * 0.7;

            if (m.userData.isLibertyDot) {
                /* 发光气点脉冲：缩放 + 明灭 */
                m.scale.setScalar(1 + 0.25 * Math.sin(this.t * 2.5 + phase));
                m.material.opacity = (0.35 + 0.25 * Math.sin(this.t * 2.8 + phase)) * (m.userData._baseInt || 1);
            } else if (m.userData.isLibertyPoly) {
                /* 凸包多边形缓慢呼吸 */
                m.material.opacity = (0.12 + 0.12 * breathe) * (m.userData._baseInt || 1);
            } else if (m.userData.isLibertyEdge) {
                /* 边缘线呼吸 */
                m.material.opacity = (0.35 + 0.25 * breathe) * (m.userData._baseInt || 1);
            } else if (m.userData.isLibertyStoneGlow) {
                /* 棋子底座气色辉光脉动 */
                m.material.opacity = 0.06 + 0.08 * breathe;
            }
        }

        this.renderer.render(this.scene, this.camera);
    }

    dispose() {
        this.clearBoard();
        this.renderer.dispose();
        if (this.container.contains(this.renderer.domElement))
            this.container.removeChild(this.renderer.domElement);
    }
}
