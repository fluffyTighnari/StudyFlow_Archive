/**
 * 对话索引轨 —— Canvas 绘制的「按你的话」时间轴。
 *
 * 参考 DeepSeek 的对话大纲：一条细轨贴着聊天区，每一段是你说过的一句话；
 * 悬停展开历史列表，点击跳到那条消息。区别在这里用 Canvas 而不是一列 DOM 点：
 * 这个东西每次滚动都会更新（视口指示、悬停高亮、当前条），
 * 用一块画布重绘比维护几十个绝对定位节点更稳，也更容易做到高分屏不糊。
 *
 * 它**只读**：不改动消息、不写库、不产生任何认知写入。
 */

function css(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export class ThreadIndex {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{onSelect: (item: any) => void, onHover?: (index: number) => void}} options
   */
  constructor(canvas, { onSelect, onHover, onToggle } = {}) {
    this.canvas = canvas;
    this.onSelect = onSelect || (() => {});
    this.onHover = onHover || (() => {});
    // 触屏/键盘没有 hover：点轨道空白处显式开合面板。
    this.onToggle = onToggle || (() => {});
    this.items = [];
    this.hoverIndex = -1;
    this.activeIndex = -1;
    this.viewport = { start: 0, end: 1 };
    this.width = 26;
    this.height = 320;

    this._onMove = this._onMove.bind(this);
    this._onLeave = this._onLeave.bind(this);
    this._onClick = this._onClick.bind(this);
    this._onResize = () => this.resize();
    canvas.addEventListener('pointermove', this._onMove);
    canvas.addEventListener('pointerleave', this._onLeave);
    canvas.addEventListener('click', this._onClick);
    window.addEventListener('resize', this._onResize);
  }

  destroy() {
    window.removeEventListener('resize', this._onResize);
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.width = rect.width || 26;
    this.height = rect.height || 320;
    this.draw();
  }

  /** items: `{ id, label, fraction }`（fraction 0..1 是按内容高度的位置）。 */
  update(items) {
    this.items = Array.isArray(items) ? items : [];
    this.hoverIndex = -1;
    const rect = this.canvas.getBoundingClientRect();
    this.width = rect.width || 26;
    this.height = rect.height || 320;
    this.draw();
  }

  setViewport(start, end) {
    this.viewport = {
      start: clamp(start, 0, 1),
      end: clamp(end, 0, 1),
    };
    this.draw();
  }

  setActive(index) {
    if (this.activeIndex === index) return;
    this.activeIndex = index;
    this.draw();
  }

  yFor(fraction) {
    const pad = 14;
    return pad + clamp(fraction, 0, 1) * Math.max(1, this.height - pad * 2);
  }

  indexAt(event) {
    const rect = this.canvas.getBoundingClientRect();
    const y = event.clientY - rect.top;
    let best = -1;
    let bestDistance = 10;
    for (let i = 0; i < this.items.length; i += 1) {
      const distance = Math.abs(this.yFor(this.items[i].fraction) - y);
      if (distance < bestDistance) {
        best = i;
        bestDistance = distance;
      }
    }
    return best;
  }

  _onMove(event) {
    const index = this.indexAt(event);
    if (index !== this.hoverIndex) {
      this.hoverIndex = index;
      this.canvas.style.cursor = index >= 0 ? 'pointer' : 'default';
      this.draw();
      this.onHover(index);
    }
  }

  _onLeave() {
    if (this.hoverIndex !== -1) {
      this.hoverIndex = -1;
      this.draw();
      this.onHover(-1);
    }
  }

  _onClick(event) {
    const index = this.indexAt(event);
    if (index >= 0) this.onSelect(this.items[index], index);
    else this.onToggle();
  }

  draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.width;
    const h = this.height;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    const ctx = this.canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    if (!this.items.length) return;

    const center = w / 2;
    const top = 14;
    const bottom = h - 14;
    const ink = css('--ink-ghost', '#8a816e');
    const rule = css('--rule', 'rgba(0,0,0,.12)');
    const accent = css('--vermilion', '#b04430');

    // 视口指示：你现在看到的是整段对话的哪一块
    const vStart = this.yFor(this.viewport.start);
    const vEnd = this.yFor(this.viewport.end);
    ctx.fillStyle = css('--rule-soft', 'rgba(0,0,0,.04)');
    ctx.beginPath();
    if (typeof ctx.roundRect === 'function') {
      ctx.roundRect(center - 6, vStart, 12, Math.max(6, vEnd - vStart), 6);
    } else {
      ctx.rect(center - 6, vStart, 12, Math.max(6, vEnd - vStart));
    }
    ctx.fill();

    // 主轴
    ctx.beginPath();
    ctx.moveTo(center, top);
    ctx.lineTo(center, bottom);
    ctx.strokeStyle = rule;
    ctx.lineWidth = 1;
    ctx.stroke();

    for (let i = 0; i < this.items.length; i += 1) {
      const y = this.yFor(this.items[i].fraction);
      const isHover = i === this.hoverIndex;
      const isActive = i === this.activeIndex;
      const half = isHover ? 7 : isActive ? 5 : 3;
      ctx.beginPath();
      ctx.moveTo(center - half, y);
      ctx.lineTo(center + half, y);
      ctx.lineCap = 'round';
      ctx.lineWidth = isHover || isActive ? 2.2 : 1.6;
      ctx.strokeStyle = isHover || isActive ? accent : ink;
      ctx.globalAlpha = isHover || isActive ? 1 : 0.55;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
}
