/**
 * 上下文占用环 —— Canvas 2D，高 DPI 感知。
 *
 * 为什么用 Canvas 而不是 SVG：这个环会在每次回答结束后重画（还有主题切换、
 * 窗口缩放），而它的形状只有一条弧 + 一段中心字。用 DOM/SVG 画要么把
 * 元素堆在结构里、要么每次重建节点；Canvas 是一块画布、一次重绘，且
 * 在高分屏上能按 `devicePixelRatio` 直接给足像素，不会糊。
 *
 * ★ 三种状态必须画得**不一样**，否则界面在说谎：
 * - `unmeasured`：没有读数（还没跑过一轮 / 模型没报窗口）→ 虚线圈 + 破折号
 * - `ok` / `near` / `over`：有读数 → 实心弧，颜色随压力变化
 *
 * 一个"0% 的实心弧"会被读成"几乎没用上下文"，而它真实的含义是"没测到"。
 */

const COLORS = {
  ok: '--jade',
  near: '--gold',
  over: '--vermilion',
  unmeasured: '--ink-ghost',
};

function css(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/**
 * 在 canvas 上画一个占用环。
 *
 * @param {HTMLCanvasElement} canvas
 * @param {{measured?: boolean, ratio?: number, pressure?: string}} meter
 * @param {{size?: number, thickness?: number}} [options]
 */
export function drawContextRing(canvas, meter, options = {}) {
  if (!canvas) return;
  const measured = !!(meter && meter.measured);
  const ratio = measured ? Math.max(0, Math.min(1, Number(meter.ratio) || 0)) : 0;
  const pressure = measured ? (meter.pressure || 'ok') : 'unmeasured';

  const size = options.size || Math.max(1, canvas.clientWidth || 28);
  const thickness = options.thickness || Math.max(2, size * 0.11);
  const dpr = window.devicePixelRatio || 1;

  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  canvas.style.width = `${size}px`;
  canvas.style.height = `${size}px`;

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);

  const c = size / 2;
  const r = c - thickness / 2 - 1;
  const start = -Math.PI / 2;

  // 底圈
  ctx.beginPath();
  ctx.arc(c, c, r, 0, Math.PI * 2);
  ctx.strokeStyle = css('--rule', 'rgba(0,0,0,.12)');
  ctx.lineWidth = thickness;
  ctx.stroke();

  const color = css(COLORS[pressure] || COLORS.ok, '#4f6b56');

  if (!measured) {
    // 没测到：虚线圈。**不画任何进度弧** —— 那会是一个凭空的读数。
    ctx.beginPath();
    ctx.setLineDash([Math.max(1.5, thickness * 0.9), Math.max(2, thickness * 1.6)]);
    ctx.arc(c, c, r, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.75;
    ctx.lineWidth = Math.max(1.2, thickness * 0.7);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    return;
  }

  // 进度弧：从 12 点方向顺时针。极小值也给一点可见长度（否则"刚跑过一轮"
  // 看起来像"什么都没发生"）；但永远不画满 —— 满圈是"已经超了"的另一种说法。
  const sweep = Math.max(0.035, ratio) * Math.PI * 2;
  ctx.beginPath();
  ctx.arc(c, c, r, start, start + sweep);
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.lineCap = 'round';
  ctx.stroke();

  // 压力标记：超线时在圈内点一颗实心朱点，靠形状而不是颜色区分
  if (pressure === 'over') {
    ctx.beginPath();
    ctx.arc(c, c, Math.max(1.6, r * 0.2), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }
}
