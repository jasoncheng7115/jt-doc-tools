// DragPositionEditor: render a paper, place an asset PNG on it, allow drag/resize, with mm values.
// Coordinates: top-left origin, millimetres. Emits onChange({x_mm, y_mm, width_mm, height_mm, paper_w_mm, paper_h_mm}).
(function () {
  const SNAP_MM = 1.5;

  class DragPositionEditor {
    constructor(opts) {
      this.root = opts.root;
      this.asset_url = opts.asset_url;
      this.bg_url = opts.bg_url || null;
      this.onChange = opts.onChange || (() => {});

      this.paper = { w: opts.paper_w_mm || 210, h: opts.paper_h_mm || 297 };
      const v = opts.value || {};
      this.value = {
        x_mm: v.x_mm ?? 140,
        y_mm: v.y_mm ?? 240,
        width_mm: v.width_mm ?? 40,
        height_mm: v.height_mm ?? 40,
        rotation_deg: v.rotation_deg ?? 0,
      };
      this.lockAspect = opts.lock_aspect ?? true;
      this.aspect = this.value.width_mm / this.value.height_mm || 1;

      this.$paperSelect = this.root.querySelector('.dpe-paper-select');
      this.$x = this.root.querySelector('.dpe-x');
      this.$y = this.root.querySelector('.dpe-y');
      this.$w = this.root.querySelector('.dpe-w');
      this.$h = this.root.querySelector('.dpe-h');
      this.$lock = this.root.querySelector('.dpe-lock');
      this.$rot = this.root.querySelector('.dpe-rot');
      this.$rotReset = this.root.querySelector('.dpe-rot-reset');
      this.$rotRandBtns = this.root.querySelectorAll('.dpe-rot-rand');
      this.$bgBlank = this.root.querySelector('.dpe-bg-blank');
      this.$bgPdf = this.root.querySelector('.dpe-bg-pdf');

      this.$wrap = this.root.querySelector('.dpe-canvas-wrap');
      this.$paper = this.root.querySelector('.dpe-paper');
      this.$bg = this.root.querySelector('.dpe-bg');
      this.$asset = this.root.querySelector('.dpe-asset');
      this.$assetImg = this.$asset.querySelector('img');
      this.$guideV = this.root.querySelector('.dpe-guide.v');
      this.$guideH = this.root.querySelector('.dpe-guide.h');

      this.$assetImg.src = this.asset_url;
      this.$lock.checked = this.lockAspect;
      this._setPaperSelect();

      if (this.bg_url) {
        this.$bg.src = this.bg_url;
        this.$bgPdf.hidden = false;
      } else {
        this.$bg.removeAttribute('src');
      }

      this._bind();
      window.addEventListener('resize', () => this._relayout());
      // Wait for paper layout
      requestAnimationFrame(() => this._relayout());
    }

    _setPaperSelect() {
      const v = `${this.paper.w},${this.paper.h}`;
      for (const opt of this.$paperSelect.options) {
        if (opt.value === v) { this.$paperSelect.value = v; return; }
      }
      // unknown size — add custom
      const o = document.createElement('option');
      o.value = v; o.textContent = `自訂 (${this.paper.w}×${this.paper.h})`;
      this.$paperSelect.appendChild(o);
      this.$paperSelect.value = v;
    }

    _relayout() {
      const wrap = this.$wrap.getBoundingClientRect();
      const pad = 24;
      const aw = Math.max(120, wrap.width - pad * 2);
      const ah = Math.max(120, wrap.height - pad * 2);
      const paperAspect = this.paper.w / this.paper.h;
      let pw = aw, ph = aw / paperAspect;
      if (ph > ah) { ph = ah; pw = ah * paperAspect; }
      this.$paper.style.width = pw + 'px';
      this.$paper.style.height = ph + 'px';
      this.mmPerPx = this.paper.w / pw;
      this._render();
    }

    _mmToPx(mm) { return mm / this.mmPerPx; }
    _pxToMm(px) { return px * this.mmPerPx; }

    _render() {
      const left = this._mmToPx(this.value.x_mm);
      const top = this._mmToPx(this.value.y_mm);
      const w = this._mmToPx(this.value.width_mm);
      const h = this._mmToPx(this.value.height_mm);
      this.$asset.style.left = left + 'px';
      this.$asset.style.top = top + 'px';
      this.$asset.style.width = w + 'px';
      this.$asset.style.height = h + 'px';
      this.$asset.style.transform = `rotate(${this.value.rotation_deg || 0}deg)`;
      this.$asset.style.transformOrigin = '50% 50%';
      this.$x.value = this.value.x_mm.toFixed(1);
      this.$y.value = this.value.y_mm.toFixed(1);
      this.$w.value = this.value.width_mm.toFixed(1);
      this.$h.value = this.value.height_mm.toFixed(1);
      if (this.$rot) this.$rot.value = (this.value.rotation_deg || 0).toFixed(1);
    }

    _emit() {
      this.onChange(this.getValue());
    }

    _clamp() {
      if (this.value.width_mm < 2) this.value.width_mm = 2;
      if (this.value.height_mm < 2) this.value.height_mm = 2;
      if (this.value.width_mm > this.paper.w) this.value.width_mm = this.paper.w;
      if (this.value.height_mm > this.paper.h) this.value.height_mm = this.paper.h;
      if (this.value.x_mm < 0) this.value.x_mm = 0;
      if (this.value.y_mm < 0) this.value.y_mm = 0;
      if (this.value.x_mm + this.value.width_mm > this.paper.w)
        this.value.x_mm = this.paper.w - this.value.width_mm;
      if (this.value.y_mm + this.value.height_mm > this.paper.h)
        this.value.y_mm = this.paper.h - this.value.height_mm;
    }

    _snap(mm, candidates) {
      for (const c of candidates) if (Math.abs(mm - c) < SNAP_MM) return { mm: c, hit: true };
      return { mm, hit: false };
    }

    _showGuides(vHitMm, hHitMm) {
      if (vHitMm != null) {
        this.$guideV.hidden = false;
        this.$guideV.style.left = this._mmToPx(vHitMm) + 'px';
      } else this.$guideV.hidden = true;
      if (hHitMm != null) {
        this.$guideH.hidden = false;
        this.$guideH.style.top = this._mmToPx(hHitMm) + 'px';
      } else this.$guideH.hidden = true;
    }

    _bind() {
      // Drag on body
      this.$asset.addEventListener('pointerdown', (e) => {
        if (e.target.classList.contains('dpe-handle')) return;
        this.$asset.setPointerCapture(e.pointerId);
        const startX = e.clientX, startY = e.clientY;
        const origX = this.value.x_mm, origY = this.value.y_mm;
        const move = (ev) => {
          const dx = this._pxToMm(ev.clientX - startX);
          const dy = this._pxToMm(ev.clientY - startY);
          let nx = origX + dx, ny = origY + dy;
          // Snap to center / edges
          const cx = this.paper.w / 2 - this.value.width_mm / 2;
          const cy = this.paper.h / 2 - this.value.height_mm / 2;
          const sx = this._snap(nx, [0, cx, this.paper.w - this.value.width_mm]);
          const sy = this._snap(ny, [0, cy, this.paper.h - this.value.height_mm]);
          nx = sx.mm; ny = sy.mm;
          this.value.x_mm = nx; this.value.y_mm = ny;
          this._clamp();
          this._render();
          const guideV = sx.hit ? (nx + this.value.width_mm / 2) : null;
          const guideH = sy.hit ? (ny + this.value.height_mm / 2) : null;
          this._showGuides(guideV, guideH);
        };
        const up = (ev) => {
          this.$asset.removeEventListener('pointermove', move);
          this.$asset.removeEventListener('pointerup', up);
          this.$asset.removeEventListener('pointercancel', up);
          this._showGuides(null, null);
          this._emit();
        };
        this.$asset.addEventListener('pointermove', move);
        this.$asset.addEventListener('pointerup', up);
        this.$asset.addEventListener('pointercancel', up);
      });

      // Resize on handles
      this.$asset.querySelectorAll('.dpe-handle').forEach((h) => {
        h.addEventListener('pointerdown', (e) => {
          e.stopPropagation();
          h.setPointerCapture(e.pointerId);
          const handle = h.dataset.h;
          const startX = e.clientX, startY = e.clientY;
          const o = { ...this.value };
          const shift = e.shiftKey;
          const locked = this.lockAspect || shift;
          const move = (ev) => {
            const dx = this._pxToMm(ev.clientX - startX);
            const dy = this._pxToMm(ev.clientY - startY);
            let nx = o.x_mm, ny = o.y_mm, nw = o.width_mm, nh = o.height_mm;
            if (handle === 'se') { nw = o.width_mm + dx; nh = o.height_mm + dy; }
            if (handle === 'ne') { nw = o.width_mm + dx; nh = o.height_mm - dy; ny = o.y_mm + dy; }
            if (handle === 'sw') { nw = o.width_mm - dx; nh = o.height_mm + dy; nx = o.x_mm + dx; }
            if (handle === 'nw') { nw = o.width_mm - dx; nh = o.height_mm - dy; nx = o.x_mm + dx; ny = o.y_mm + dy; }
            if (locked && this.aspect > 0) {
              // enforce aspect based on width change
              const newH = nw / this.aspect;
              const dh = newH - nh;
              nh = newH;
              if (handle === 'ne' || handle === 'nw') ny -= dh;
            }
            if (nw < 5) { nw = 5; }
            if (nh < 5) { nh = 5; }
            this.value = { x_mm: nx, y_mm: ny, width_mm: nw, height_mm: nh };
            this._clamp();
            this._render();
          };
          const up = () => {
            h.removeEventListener('pointermove', move);
            h.removeEventListener('pointerup', up);
            h.removeEventListener('pointercancel', up);
            this.aspect = this.value.width_mm / this.value.height_mm || 1;
            this._emit();
          };
          h.addEventListener('pointermove', move);
          h.addEventListener('pointerup', up);
          h.addEventListener('pointercancel', up);
        });
      });

      // Inputs
      const apply = (changed) => {
        this.value.x_mm = parseFloat(this.$x.value) || 0;
        this.value.y_mm = parseFloat(this.$y.value) || 0;
        let nw = parseFloat(this.$w.value) || 1;
        let nh = parseFloat(this.$h.value) || 1;
        if (this.lockAspect && this.aspect > 0) {
          if (changed === 'w') nh = nw / this.aspect;
          else if (changed === 'h') nw = nh * this.aspect;
        }
        this.value.width_mm = nw;
        this.value.height_mm = nh;
        if (!this.lockAspect) this.aspect = nw / nh || 1;
        this._clamp(); this._render(); this._emit();
      };
      this.$x.addEventListener('change', () => apply('x'));
      this.$y.addEventListener('change', () => apply('y'));
      this.$w.addEventListener('change', () => apply('w'));
      this.$h.addEventListener('change', () => apply('h'));

      if (this.$rot) {
        this.$rot.addEventListener('change', () => {
          const v = parseFloat(this.$rot.value) || 0;
          this.value.rotation_deg = Math.max(-45, Math.min(45, v));
          this._render(); this._emit();
        });
      }
      if (this.$rotReset) this.$rotReset.addEventListener('click', () => {
        this.value.rotation_deg = 0; this._render(); this._emit();
      });
      if (this.$rotRandBtns) this.$rotRandBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          const max = parseFloat(btn.dataset.range) || 3;
          const min = parseFloat(btn.dataset.min) || 0;
          // Pick a magnitude in [min, max], then a random sign.
          const mag = min + Math.random() * (max - min);
          const sign = Math.random() < 0.5 ? -1 : 1;
          this.value.rotation_deg = +(sign * mag).toFixed(1);
          this._render(); this._emit();
        });
      });

      this.$lock.addEventListener('change', () => {
        this.lockAspect = this.$lock.checked;
        if (this.lockAspect) this.aspect = this.value.width_mm / this.value.height_mm || 1;
        this._emit();
      });

      this.$paperSelect.addEventListener('change', () => {
        const [w, h] = this.$paperSelect.value.split(',').map(Number);
        this.paper.w = w; this.paper.h = h;
        this._clamp(); this._relayout(); this._emit();
      });

      // Presets
      this.root.querySelectorAll('[data-preset]').forEach(btn => {
        btn.addEventListener('click', () => {
          this._applyPreset(btn.dataset.preset);
          this._render(); this._emit();
        });
      });

      // Bg toggles
      if (this.$bgBlank) this.$bgBlank.addEventListener('click', () => { this.$bg.style.display = 'none'; });
      if (this.$bgPdf) this.$bgPdf.addEventListener('click', () => { if (this.bg_url) { this.$bg.style.display = 'block'; this.$bg.src = this.bg_url; } });
    }

    _applyPreset(kind) {
      const p = this.paper, w = this.value.width_mm, h = this.value.height_mm;
      const margin = 10;
      switch (kind) {
        case 'tl': this.value.x_mm = margin; this.value.y_mm = margin; break;
        case 'tr': this.value.x_mm = p.w - w - margin; this.value.y_mm = margin; break;
        case 'bl': this.value.x_mm = margin; this.value.y_mm = p.h - h - margin; break;
        case 'br': this.value.x_mm = p.w - w - margin; this.value.y_mm = p.h - h - margin; break;
        case 'hc': this.value.x_mm = (p.w - w) / 2; break;
        case 'vc': this.value.y_mm = (p.h - h) / 2; break;
        case 'center': this.value.x_mm = (p.w - w) / 2; this.value.y_mm = (p.h - h) / 2; break;
        case 'mbottom': this.value.x_mm = (p.w - w) / 2; this.value.y_mm = p.h * 0.72; break;
      }
      this._clamp();
    }

    setBackground(url) {
      this.bg_url = url;
      if (url) {
        this.$bg.src = url;
        this.$bg.style.display = 'block';
        if (this.$bgPdf) this.$bgPdf.hidden = false;
      } else {
        this.$bg.removeAttribute('src');
      }
    }

    setPaper(w_mm, h_mm) {
      this.paper.w = w_mm; this.paper.h = h_mm;
      this._setPaperSelect();
      this._clamp(); this._relayout(); this._emit();
    }

    getValue() {
      return {
        x_mm: +this.value.x_mm.toFixed(2),
        y_mm: +this.value.y_mm.toFixed(2),
        width_mm: +this.value.width_mm.toFixed(2),
        height_mm: +this.value.height_mm.toFixed(2),
        rotation_deg: +(this.value.rotation_deg || 0).toFixed(2),
        paper_w_mm: this.paper.w,
        paper_h_mm: this.paper.h,
        lock_aspect: this.lockAspect,
      };
    }
  }
  window.DragPositionEditor = DragPositionEditor;
})();
