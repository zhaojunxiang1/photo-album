# -*- coding: utf-8 -*-
"""
3D 星空相册 —— 照片化身星尘，组成四种宇宙形态（黑色星空场景）
· 星云模式：照片化作粒子，聚成一团闪烁的星云
· 星球群模式：照片聚成多个球状星系集群
· 照片墙模式：无数照片铺满整个屏幕
· 星系盘模式：照片排成螺旋星系盘（侧视如圆柱）
· 模式间平滑变形过渡（粒子飞行动画）
· 黑色深空背景 + 闪烁星星
· 自动旋转 | 长按拖拽手动旋转 | 滚轮缩放
· 切换模式：点击窗口 / 键盘 1~4 / 顶部操作栏按钮
技术栈: PyQt5（纯 QPainter 软渲染 3D 透视投影，不依赖 OpenGL）
"""

import os
import sys
import math
import random
import time

from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QEasingCurve, QRect
from PyQt5.QtGui import (
    QImage, QPainter, QColor, QFont, QPixmap, QTransform, QPen,
    QPainterPath, QPolygonF
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QCheckBox,
    QFrame
)


# ============================================================
# 3D 星空视图（纯 QPainter 软渲染）
# ============================================================
class GLView(QWidget):
    """星空相册渲染窗口：照片粒子在深空中组成四种形态"""

    # 粒子数量（照片循环填充到粒子上）
    PARTICLE_COUNT = 420
    # 粒子贴片的世界尺寸（逻辑单位）
    PARTICLE_SIZE = 0.42
    # 模式定义: (key, 中文名)
    MODES = [
        ('nebula',  '星云'),
        ('planets', '星球群'),
        ('wall',    '照片墙'),
        ('galaxy',  '星系盘'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self.images = []          # [(path, name)]
        self._pixmaps = {}        # path -> (QPixmap, w, h)（小尺寸缓存，粒子绘制用）
        self._big_cache = {}      # path -> QPixmap（原图缓存，放大查看用）

        # 视角状态
        self.rot_x = -12.0
        self.rot_y = 0.0
        self.zoom = 1.0
        self.auto_rotate = True

        # ---- 粒子系统 ----
        # 每个粒子: {photo(照片索引), x,y,z(当前位置), fx,fy,fz(出发点), tx,ty,tz(目标), delay, seed}
        self.particles = []
        self.mode_index = 0
        # 模式过渡状态
        self._trans = None       # {'start': 时间, 'duration': 秒}

        # 背景星星（固定随机，带闪烁相位）
        # 每个元素为元组: (x, y, z, phase, speed, size)
        self._stars = []
        rng = random.Random(20260101)
        for _ in range(170):
            # 分布在大球壳内，包围整个场景
            self._stars.append((
                rng.uniform(-14, 14),
                rng.uniform(-9, 9),
                rng.uniform(-14, 14),
                rng.uniform(0, math.tau),
                rng.uniform(0.6, 2.2),
                rng.uniform(0.8, 2.4),
            ))

        # 拖拽状态
        self._press_pos = None
        self._manual_rotating = False
        self._moved = False

        # 放大查看状态：None 表示未放大；否则为照片索引
        self._zoomed_photo = None
        # 上一帧投影结果缓存（用于点击命中检测）
        self._hit_items = []

        # 渲染计时器（30fps）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ---------- 数据 ----------
    def set_images(self, paths):
        self._pixmaps.clear()
        self.images = [(p, os.path.basename(p)) for p in paths]
        self._build_particles(first=True)

    def add_images(self, paths):
        changed = False
        for p in paths:
            if p not in [x[0] for x in self.images]:
                self.images.append((p, os.path.basename(p)))
                changed = True
        if changed:
            self._build_particles()

    def clear_images(self):
        self._pixmaps.clear()
        self.images = []
        self.particles = []
        self.update()

    def set_auto_rotate(self, on):
        self.auto_rotate = on

    # ---------- 粒子系统 ----------
    def _build_particles(self, first=False):
        """(重新)构建粒子：照片循环分配到粒子上。
        first=True 时从远处球壳汇聚开场；否则保持当前位置重新瞄准目标。"""
        n = len(self.images)
        if n == 0:
            self.particles = []
            self.update()
            return

        m = self.PARTICLE_COUNT
        rng = random.Random(42)
        old = self.particles
        parts = []
        for i in range(m):
            photo = i % n
            if first:
                # 开场：从远处随机球壳出发，汇聚成第一形态
                u = rng.uniform(-1, 1)
                ph = rng.uniform(0, math.tau)
                s = math.sqrt(1 - u * u)
                r = rng.uniform(13, 17)
                x, y, z = r * s * math.cos(ph), r * u, r * s * math.sin(ph)
            else:
                if i < len(old):
                    x, y, z = old[i][0], old[i][1], old[i][2]
                else:
                    x, y, z = rng.uniform(-8, 8), rng.uniform(-5, 5), rng.uniform(-8, 8)
            # 粒子元组: (photo, x,y,z, fx,fy,fz, tx,ty,tz, delay, seed)
            parts.append([photo, x, y, z, x, y, z, x, y, z, 0.0, rng.random()])
        self.particles = parts
        # 立即瞄准当前模式（含开场动画）
        self._apply_mode(self.mode_index, animate=True)

    # ---------- 四种模式的目标位置 ----------
    def _targets_for_mode(self, key, m):
        """为 m 个粒子生成目标位置（逻辑坐标，场景尺度约 ±8）"""
        rng = random.Random(777 + hash(key) % 1000)
        pts = []

        if key == 'nebula':
            # 星云：主体高斯云团 + 少量远景散星
            for _ in range(m):
                if rng.random() < 0.88:
                    x = rng.gauss(0, 2.1)
                    y = rng.gauss(0, 1.45)
                    z = rng.gauss(0, 2.1)
                else:
                    x = rng.uniform(-9, 9)
                    y = rng.uniform(-6, 6)
                    z = rng.uniform(-9, 9)
                pts.append((x, y, z))

        elif key == 'planets':
            # 星球群：三个球状集群 + 环绕星尘
            balls = [(-3.3, 0.6, 0.0, 1.75),
                     (3.0, -0.7, -0.9, 1.25),
                     (0.5, 2.5, 1.3, 0.85)]
            for i in range(m):
                if rng.random() < 0.78:
                    bx, by, bz, br = balls[i % len(balls)]
                    u = rng.uniform(-1, 1)
                    ph = rng.uniform(0, math.tau)
                    s = math.sqrt(1 - u * u)
                    pts.append((bx + br * s * math.cos(ph),
                                by + br * u,
                                bz + br * s * math.sin(ph)))
                else:
                    r = rng.uniform(4.6, 7.5)
                    a = rng.uniform(0, math.tau)
                    y = rng.gauss(0, 1.3)
                    pts.append((r * math.cos(a), y, r * math.sin(a)))

        elif key == 'wall':
            # 照片墙：网格铺满视野，带轻微景深
            cols = 26
            rows = (m + cols - 1) // cols
            gap = 0.60
            for i in range(m):
                r_i, c_i = i // cols, i % cols
                x = (c_i - (cols - 1) / 2) * gap
                y = ((rows - 1) / 2 - r_i) * gap
                z = rng.gauss(0, 0.10)
                pts.append((x, y, z))

        elif key == 'galaxy':
            # 星系盘：3 条螺旋臂（阿基米德螺线），盘面扁平
            arms = 3
            for i in range(m):
                arm = i % arms
                t = rng.random() ** 0.8          # 沿臂位置，内密外疏
                base_ang = arm * (2 * math.pi / arms)
                theta = base_ang + t * 3.0 * math.pi
                r = 0.35 + t * 6.8
                spread = 0.30 + t * 0.85
                ang = theta + rng.gauss(0, 0.16)
                rr = r + rng.gauss(0, spread)
                x = rr * math.cos(ang)
                z = rr * math.sin(ang)
                y = rng.gauss(0, 0.16 + t * 0.12)
                pts.append((x, y, z))

        return pts

    def _apply_mode(self, mode_index, animate=True):
        """切换模式：为每个粒子设置出发点(当前)、目标点(新模式)、随机延迟"""
        self.mode_index = mode_index % len(self.MODES)
        key = self.MODES[self.mode_index][0]
        m = len(self.particles)
        if m == 0:
            self.update()
            return

        targets = self._targets_for_mode(key, m)
        rng = random.Random(999 + mode_index)
        for p, t in zip(self.particles, targets):
            p[4], p[5], p[6] = p[1], p[2], p[3]     # fx,fy,fz = 当前 x,y,z
            p[7], p[8], p[9] = t                     # tx,ty,tz = 目标
            p[10] = rng.uniform(0.0, 0.30) if animate else 0.0

        self._trans = {'start': time.monotonic(), 'duration': 1.15}
        # 通知 UI 刷新模式按钮高亮（若已挂接）
        cb = getattr(self, '_mode_ui_cb', None)
        if cb is not None:
            cb()
        self.update()

    def cycle_mode(self):
        self._apply_mode((self.mode_index + 1) % len(self.MODES))

    def set_mode(self, mode_index):
        if mode_index != self.mode_index:
            self._apply_mode(mode_index)

    # ---------- 图片缓存 ----------
    def _get_pixmap(self, path, max_dim=96):
        if path not in self._pixmaps:
            pix = QPixmap(path)
            if pix.isNull():
                pix = QPixmap(64, 64)
                pix.fill(QColor(50, 55, 75))
            else:
                pix = pix.scaled(max_dim, max_dim, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            # 缓存 (pixmap, 宽, 高)，避免每帧查询尺寸
            self._pixmaps[path] = (pix, pix.width(), pix.height())
        return self._pixmaps[path]

    # ---------- 渲染循环 ----------
    def _tick(self):
        if self.auto_rotate and not self._manual_rotating:
            self.rot_y = (self.rot_y + 0.4) % 360.0
        # 过渡中或自动旋转时持续重绘
        trans_active = self._trans is not None and (
            time.monotonic() - self._trans['start']
            < self._trans['duration'] + 0.4)   # +0.4s 覆盖最大粒子延迟
        if self.auto_rotate or trans_active:
            self.update()

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.pos()
            self._moved = False
            self._manual_rotating = False

    def mouseMoveEvent(self, e):
        cb = getattr(self, '_bar_hover_cb', None)
        if cb is not None:
            cb(e.y() <= 64)

        if self._press_pos is not None:
            dx = e.x() - self._press_pos.x()
            dy = e.y() - self._press_pos.y()
            # 移动超过阈值才判定为拖拽（旋转）
            if not self._manual_rotating and (abs(dx) > 4 or abs(dy) > 4):
                self._manual_rotating = True
                self._moved = True
            if self._manual_rotating:
                self.rot_y += dx * 0.6
                self.rot_x += dy * 0.6
                self.rot_x = max(-85, min(85, self.rot_x))
                self._press_pos = e.pos()
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            # 只有「没有拖动」才算单击（暂停/放大）
            if not self._manual_rotating:
                if self._zoomed_photo is not None:
                    self._zoomed_photo = None
                else:
                    hit = self._pick_photo(e.pos())
                    if hit is not None:
                        self._zoomed_photo = hit
                    else:
                        # 点击空白：暂停/恢复自动旋转
                        self.auto_rotate = not self.auto_rotate
                        # 同步顶部栏复选框状态
                        cb = getattr(self, '_auto_cb', None)
                        if cb is not None:
                            cb.blockSignals(True)
                            cb.setChecked(self.auto_rotate)
                            cb.blockSignals(False)
            self._press_pos = None
            self._manual_rotating = False
            self._moved = False
            self.update()

    def _pick_photo(self, pos):
        """根据点击坐标命中检测：返回照片索引或 None。
        命中的是最上层（z2 最大、最后绘制）且覆盖该点的粒子。"""
        px, py = pos.x(), pos.y()
        base = self.PARTICLE_SIZE * (min(self.width(), self.height()) / 10.0 * self.zoom)
        best = None
        best_z = -1e9
        for z2, sx, sy, f, photo in self._hit_items:
            side = base * f
            if side < 1.5:
                continue
            half = side / 2
            if abs(px - sx) <= half and abs(py - sy) <= half:
                if z2 > best_z:
                    best_z = z2
                    best = photo
        return best

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        self.zoom *= (1.1 if delta > 0 else 0.9)
        self.zoom = max(0.3, min(3.0, self.zoom))
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            self._apply_mode(k - Qt.Key_1)
        elif k == Qt.Key_Space:
            self.cycle_mode()
        elif k == Qt.Key_Escape:
            if self._zoomed_photo is not None:
                self._zoomed_photo = None
                self.update()
            else:
                w = self.window()
                if w is not None and w.isFullScreen():
                    w.showNormal()

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 关闭 SmoothPixmapTransform：贴片本身已是小图，缩放平滑开销大且收益低
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        w, h = self.width(), self.height()
        # 深空背景
        painter.fillRect(0, 0, w, h, QColor(4, 5, 12))

        cx, cy = w / 2, h / 2
        scale = min(w, h) / 10.0 * self.zoom
        dist = 9.0

        if not self.images:
            painter.setPen(QColor(140, 145, 160))
            painter.setFont(QFont("Microsoft YaHei", 16))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter,
                             "请把照片文件放到本程序同目录下的 example 文件夹\n然后重新启动程序")
            return

        now = time.monotonic()
        self._paint_stars(painter, cx, cy, scale, dist, now)

        # 放大查看：全屏展示单张照片
        if self._zoomed_photo is not None:
            self._paint_zoomed(painter, w, h)
            return

        self._paint_particles(painter, cx, cy, scale, dist, now)

        # 底部提示
        mode_name = self.MODES[self.mode_index][1]
        hint = (f"当前模式：{mode_name}   点击照片 → 放大   单击空白 → 暂停/旋转   "
                f"拖拽 → 旋转   滚轮 → 缩放   键盘 1~4 → 切换模式   Esc → 退出全屏")
        painter.setPen(QColor(190, 195, 210, 150))
        painter.setFont(QFont("Microsoft YaHei", 10))
        painter.drawText(QRectF(0, h - 32, w, 24), Qt.AlignCenter, hint)

    def _paint_stars(self, painter, cx, cy, scale, dist, now):
        """背景闪烁星星"""
        rx = math.radians(self.rot_x)
        ry = math.radians(self.rot_y)
        cos_rx, sin_rx = math.cos(rx), math.sin(rx)
        cos_ry, sin_ry = math.cos(ry), math.sin(ry)

        painter.setPen(Qt.NoPen)
        _w = self.width() + 4
        _h = self.height() + 4
        for s in self._stars:
            x, y, z, phase, speed, size = s
            x1 = x * cos_ry + z * sin_ry
            z1 = -x * sin_ry + z * cos_ry
            y1 = y * cos_rx - z1 * sin_rx
            z2 = y * sin_rx + z1 * cos_rx
            denom = dist - z2
            if denom <= 1:
                continue
            f = dist / denom
            sx = cx + x1 * scale * f
            sy = cy - y1 * scale * f
            if sx < -4 or sx > _w or sy < -4 or sy > _h:
                continue
            # 闪烁
            tw = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * speed + phase))
            alpha = int(120 * tw * f)
            painter.setBrush(QColor(220, 225, 255, max(20, min(200, alpha))))
            painter.drawEllipse(QPointF(sx, sy), size * f, size * f)

    def _paint_particles(self, painter, cx, cy, scale, dist, now):
        """绘制照片粒子（深度排序，远小暗、近大亮）"""
        # 推进过渡动画
        prog = 1.0
        if self._trans is not None:
            el = now - self._trans['start']
            prog = min(1.0, el / self._trans['duration'])

        rx = math.radians(self.rot_x)
        ry = math.radians(self.rot_y)
        cos_rx, sin_rx = math.cos(rx), math.sin(rx)
        cos_ry, sin_ry = math.cos(ry), math.sin(ry)

        _W = self.width()
        _H = self.height()
        _images = self.images
        _get_pixmap = self._get_pixmap

        # 先投影再排序
        items = []
        items_append = items.append
        for p in self.particles:
            # 每粒子缓动（含随机延迟）—— 直接用列表索引读取
            delay = p[10]
            t = prog - delay
            if t <= 0:
                x, y, z = p[4], p[5], p[6]
            elif t >= 1.0:
                x, y, z = p[7], p[8], p[9]
            else:
                # InOutCubic
                if t < 0.5:
                    e = 4.0 * t * t * t
                else:
                    e = 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0
                x = p[4] + (p[7] - p[4]) * e
                y = p[5] + (p[8] - p[5]) * e
                z = p[6] + (p[9] - p[6]) * e
            # 更新当前位置（供下次过渡作为出发点）
            p[1], p[2], p[3] = x, y, z

            x1 = x * cos_ry + z * sin_ry
            z1 = -x * sin_ry + z * cos_ry
            y1 = y * cos_rx - z1 * sin_rx
            z2 = y * sin_rx + z1 * cos_rx
            denom = dist - z2
            if denom <= 0.8:
                continue
            f = dist / denom
            sx = cx + x1 * scale * f
            sy = cy - y1 * scale * f
            if sx < -60 or sx > _W + 60 or sy < -60 or sy > _H + 60:
                continue
            items_append((z2, sx, sy, f, p[0]))

        items.sort(key=lambda it: it[0])  # 远的先画

        # 缓存本帧投影结果（用于点击命中检测）
        self._hit_items = items

        base = self.PARTICLE_SIZE * scale
        _side = base
        for z2, sx, sy, f, photo in items:
            pix, pw, ph = _get_pixmap(_images[photo][0])
            # 粒子尺寸随深度缩放；亮度随深度衰减（近亮远暗）
            side = _side * f
            if side < 1.5:
                continue
            opacity = 0.30 + (f - 0.8) * 0.75
            if opacity > 1.0:
                opacity = 1.0
            elif opacity < 0.18:
                opacity = 0.18
            painter.setOpacity(opacity)
            painter.drawPixmap(QRectF(sx - side / 2, sy - side / 2, side, side),
                               pix, QRectF(0, 0, pw, ph))
        painter.setOpacity(1.0)

    def _paint_zoomed(self, painter, w, h):
        """放大查看单张照片（全屏居中，半透明遮罩 + 文件名）"""
        photo = self._zoomed_photo
        if photo is None or photo >= len(self.images):
            self._zoomed_photo = None
            return
        path = self.images[photo][0]
        name = self.images[photo][1]

        # 加载原始大图（独立缓存，不污染粒子小图缓存）
        if path not in self._big_cache:
            pix = QPixmap(path)
            self._big_cache[path] = pix
        pix = self._big_cache[path]

        if pix.isNull():
            self._zoomed_photo = None
            return

        # 半透明遮罩
        painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 210))

        # 居中缩放显示，留边距
        margin = 40
        avail_w = w - margin * 2
        avail_h = h - margin * 2
        pw, ph = pix.width(), pix.height()
        scale_f = min(avail_w / pw, avail_h / ph)
        dw, dh = pw * scale_f, ph * scale_f
        dx = (w - dw) / 2
        dy = (h - dh) / 2

        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(1.0)
        painter.drawPixmap(QRectF(dx, dy, dw, dh), pix, QRectF(0, 0, pw, ph))
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        # 文件名 + 关闭提示
        painter.setPen(QColor(240, 242, 248))
        painter.setFont(QFont("Microsoft YaHei", 13))
        painter.drawText(QRectF(0, h - 50, w, 24), Qt.AlignCenter, name)
        painter.setPen(QColor(170, 175, 190, 200))
        painter.setFont(QFont("Microsoft YaHei", 11))
        painter.drawText(QRectF(0, h - 28, w, 20), Qt.AlignCenter,
                         "单击 / Esc 关闭")


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D 星空相册 —— 星云 · 星球群 · 照片墙 · 星系盘")
        self.resize(1200, 760)

        # 全屏沉浸式：中央只有一个 3D 星空视图
        self.gl_view = GLView()
        self.setCentralWidget(self.gl_view)

        # 顶部滑出操作栏（鼠标移到顶部时滑出，移开自动隐藏）
        self._build_top_bar()

        # 让 GLView 在鼠标移动到顶部区域时回调本窗口
        self.gl_view._bar_hover_cb = self._on_bar_hover
        self._bar_visible = False

    # ---------- 顶部滑出操作栏 ----------
    def _build_top_bar(self):
        self.top_bar = QFrame(self.gl_view)
        self.top_bar.setStyleSheet(
            "QFrame { background: rgba(20, 22, 34, 230); border-bottom: 1px solid rgba(120,140,180,120); }"
        )
        self.top_bar.setFixedHeight(56)
        self.top_bar.show()

        lay = QHBoxLayout(self.top_bar)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(8)

        btn_style = (
            "QPushButton { background: rgba(255,255,255,18); color: #e8e8f0; "
            "border: 1px solid rgba(140,160,200,80); border-radius: 5px; padding: 6px 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,40); }"
        )
        mode_style_on = (
            "QPushButton { background: rgba(90,140,255,90); color: #ffffff; "
            "border: 1px solid rgba(140,180,255,180); border-radius: 5px; padding: 6px 12px; }"
        )

        self.btn_add = QPushButton("添加照片")
        self.btn_add.setStyleSheet(btn_style)
        self.btn_add.clicked.connect(self.add_photos)
        lay.addWidget(self.btn_add)

        self.btn_folder = QPushButton("添加文件夹")
        self.btn_folder.setStyleSheet(btn_style)
        self.btn_folder.clicked.connect(self.add_folder)
        lay.addWidget(self.btn_folder)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.setStyleSheet(btn_style)
        self.btn_clear.clicked.connect(self.clear_album)
        lay.addWidget(self.btn_clear)

        lay.addSpacing(10)

        # 四种模式按钮
        self._mode_btns = []
        for i, (key, name) in enumerate(GLView.MODES):
            b = QPushButton(name)
            b.clicked.connect(lambda checked, idx=i: self.gl_view.set_mode(idx))
            lay.addWidget(b)
            self._mode_btns.append(b)

        lay.addSpacing(10)

        self.auto_cb = QCheckBox("自动旋转")
        self.auto_cb.setChecked(True)
        self.auto_cb.setStyleSheet("QCheckBox { color: #e8e8f0; }")
        self.auto_cb.toggled.connect(self.gl_view.set_auto_rotate)
        lay.addWidget(self.auto_cb)

        # 让 GLView 在点击空白切换旋转时同步复选框状态
        self.gl_view._auto_cb = self.auto_cb

        lay.addSpacing(8)

        lay.addWidget(QLabel("缩放"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(30, 300)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.valueChanged.connect(
            lambda v: setattr(self.gl_view, 'zoom', v / 100.0))
        lay.addWidget(self.zoom_slider)

        lay.addStretch(1)

        hint = QLabel("点击照片放大 · 单击空白暂停旋转 · 拖拽旋转 · 滚轮缩放 · 1~4 切模式 · Esc 退出全屏")
        hint.setStyleSheet("color: rgba(200,200,210,160);")
        lay.addWidget(hint)

        # 动画：上下滑入滑出
        self._bar_anim = QPropertyAnimation(self.top_bar, b"geometry", self)
        self._bar_anim.setDuration(220)
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 初始状态：藏在顶部之外
        self.top_bar.move(0, -self.top_bar.height() - 2)

        # 模式高亮刷新
        self.gl_view._mode_ui_cb = self._refresh_mode_btns
        self._refresh_mode_btns()

    def _refresh_mode_btns(self):
        mode_style_on = (
            "QPushButton { background: rgba(90,140,255,110); color: #ffffff; "
            "border: 1px solid rgba(150,190,255,200); border-radius: 5px; padding: 6px 12px; }"
        )
        btn_style = (
            "QPushButton { background: rgba(255,255,255,18); color: #e8e8f0; "
            "border: 1px solid rgba(140,160,200,80); border-radius: 5px; padding: 6px 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,40); }"
        )
        cur = self.gl_view.mode_index
        for i, b in enumerate(self._mode_btns):
            b.setStyleSheet(mode_style_on if i == cur else btn_style)

    def _bar_geometry(self, visible):
        w = self.gl_view.width()
        h = self.top_bar.height()
        if visible:
            return QRect(0, 0, w, h)
        return QRect(0, -h - 2, w, h)

    def _show_bar(self, on):
        self._bar_anim.stop()
        self._bar_anim.setStartValue(self.top_bar.geometry())
        self._bar_anim.setEndValue(self._bar_geometry(on))
        self._bar_anim.start()
        self._bar_visible = on

    def _on_bar_hover(self, near_top):
        if near_top:
            if not self._bar_visible:
                self._show_bar(True)
        else:
            if self._bar_visible:
                self._show_bar(False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'top_bar'):
            self.top_bar.setGeometry(self._bar_geometry(self._bar_visible))

    # ---------- 相册操作 ----------
    def add_photos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择照片", "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.gif *.webp);;所有文件 (*.*)")
        if files:
            self.gl_view.add_images(files)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp')
            files = []
            for root, _, names in os.walk(folder):
                for n in names:
                    if n.lower().endswith(exts):
                        files.append(os.path.join(root, n))
            if files:
                self.gl_view.add_images(files)
            else:
                QMessageBox.information(self, "提示", "该文件夹下没有找到图片文件。")

    def clear_album(self):
        ret = QMessageBox.question(self, "确认", "确定要清空相册吗？")
        if ret == QMessageBox.Yes:
            self.gl_view.clear_images()

    def load_example(self, example_dir):
        if os.path.isdir(example_dir):
            exts = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp')
            samples = [os.path.join(example_dir, f) for f in os.listdir(example_dir)
                       if f.lower().endswith(exts)]
            if samples:
                self.gl_view.set_images(samples)
                return True
        return False


# ============================================================
# 入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = MainWindow()
    win.showFullScreen()  # 全屏启动

    # 首次启动自动加载：当前目录下的 example 文件夹（若存在）
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    win.load_example(os.path.join(base, "example"))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
