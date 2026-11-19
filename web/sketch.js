/*
 * Decision Final — visualización generativa (p5.js, modo instancia).
 *
 *   createDecisionSketch(contenedor, payload, audio, opciones) → instancia p5
 *
 * `payload` es `Sonification.to_payload()` (Python): datos preprocesados + notas.
 * Sincronía: posición en los datos = audio.currentTime / payload.duration_s,
 * y cada nota del audio dispara un pulso visual en el mismo instante.
 *
 * Estados (matriz de la ideación §4), con transición progresiva entre ellos:
 *   bajo  (0–30)   partículas lentas y circulares, azules, estelas largas
 *   medio (31–70)  geometrías en rotación con textura distorsionada, verdes/cálidos
 *   alto  (71–100) trazos acelerados, glitch, flashes rojo/blanco
 * Capa acumulativa (abajo): trazo histórico, banda media ± desviación,
 * umbrales y mapa de calor de rangos; lo que aún no ha sonado queda atenuado.
 *
 * Accesibilidad: con `prefers-reduced-motion` se desactivan glitch y flashes, y
 * los flashes nunca superan 3 por segundo (WCAG 2.3.1).
 */
(function (global) {
  "use strict";

  const PALETTES = [
    { bg: [6, 12, 30], a: [60, 130, 255], b: [120, 215, 255], accent: [200, 235, 255] }, // bajo
    { bg: [14, 18, 10], a: [110, 205, 90], b: [255, 175, 60], accent: [255, 228, 140] }, // medio
    { bg: [22, 3, 6], a: [255, 45, 55], b: [255, 255, 255], accent: [255, 130, 110] }, // alto
  ];
  const MIN_FLASH_INTERVAL = 0.34; // s → máx. ~3 flashes por segundo

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  /** Interpolación lineal de un arreglo en un índice fraccional. */
  function sampleAt(arr, f) {
    const i = clamp(Math.floor(f), 0, arr.length - 1);
    const j = Math.min(i + 1, arr.length - 1);
    const k = f - i;
    return arr[i] * (1 - k) + arr[j] * k;
  }

  /** Primer índice con events[i].t >= t (búsqueda binaria). */
  function lowerBound(events, t) {
    let lo = 0, hi = events.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (events[mid].t < t) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /** Mezcla las tres paletas según pesos [bajo, medio, alto]. */
  function mixPalette(w) {
    const out = {};
    for (const key of ["bg", "a", "b", "accent"]) {
      out[key] = [0, 1, 2].map(
        (c) => w[0] * PALETTES[0][key][c] + w[1] * PALETTES[1][key][c] + w[2] * PALETTES[2][key][c]
      );
    }
    return out;
  }

  /** Suavizado independiente de los FPS: `perFrame` es la fracción a 60 fps. */
  function smoothFactor(perFrame, frames60) {
    return 1 - Math.pow(1 - perFrame, frames60);
  }

  function formatTime(s) {
    s = Math.max(0, s);
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return m + ":" + String(r).padStart(2, "0");
  }

  function createDecisionSketch(container, payload, audio, options) {
    const opts = Object.assign(
      { particles: 420, historyHeight: 0.22, maxHeight: 640, primary: null, reduceMotion: null },
      options || {}
    );
    if (opts.reduceMotion === null) {
      opts.reduceMotion = !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }

    const series = payload.data.series;
    const names = Object.keys(series);
    const primary = opts.primary || (payload.roles && payload.roles.level) || names[0];
    const S = series[primary];
    const n = S.norm.length;
    const duration = payload.duration_s;
    const events = payload.events || [];
    const thresholds = payload.data.thresholds || [30, 70];
    const levelNames = payload.data.levels || ["bajo", "medio", "alto"];
    let midiLo = 127, midiHi = 0;
    for (const e of events) {
      midiLo = Math.min(midiLo, e.midi);
      midiHi = Math.max(midiHi, e.midi);
    }
    if (midiHi <= midiLo) { midiLo -= 12; midiHi += 12; }

    const sketch = function (p) {
      let W = 800, H = 450, sceneH = 350;
      let historyLayer = null;
      const particles = [];
      const pulses = [];
      let evPtr = 0, lastT = -1, lastFlashAt = -10;
      const weights = [1, 0, 0];
      let energy = 0, flash = 0, flashRed = false, rot = 0, audioTime = 0;
      // Todo avanza en tiempo real, no por cuadro: con pocos FPS (equipo lento,
      // pestaña en segundo plano) la imagen sigue sincronizada con el audio.
      let k60 = 1; // cuadros equivalentes a 60 fps desde el cuadro anterior (para suavizados)
      let kMove = 1; // igual, pero acotado (evita teletransportes tras una pausa larga)
      const stats = { pulses: 0, flashTimes: [], frames: 0 };
      p.decisionStats = stats;

      function computeSize() {
        W = Math.max(280, Math.floor(container.clientWidth || 800));
        H = Math.round(Math.min(W * 9 / 16, opts.maxHeight));
        sceneH = Math.round(H * (1 - opts.historyHeight));
      }

      function newParticle(anywhere) {
        const x = p.random(W), y = p.random(sceneH);
        // vida al azar: si todas murieran a la vez, la escena "parpadearía"
        const life = anywhere ? p.random(1, 240) : p.random(90, 240);
        return { x: x, y: y, px: x, py: y, r: p.random(2, 7), seed: p.random(1000), life: life };
      }

      function buildHistory() {
        if (historyLayer) historyLayer.remove();
        const hH = H - sceneH;
        const g = p.createGraphics(W, hH);
        g.pixelDensity(1);
        g.background(10, 12, 18);
        const strip = 8, pad = 6;
        const plotH = hH - strip - pad * 2;
        const Y = (v) => pad + (1 - clamp(v, 0, 100) / 100) * plotH;
        const F = (x) => (x / (W - 1)) * (n - 1);

        g.stroke(255, 45);
        g.strokeWeight(1);
        for (const th of thresholds) g.line(0, Y(th), W, Y(th));

        g.noStroke();
        g.fill(255, 255, 255, 26);
        g.beginShape();
        for (let x = 0; x < W; x++) g.vertex(x, Y(sampleAt(S.mean, F(x)) + sampleAt(S.std, F(x))));
        for (let x = W - 1; x >= 0; x--) g.vertex(x, Y(sampleAt(S.mean, F(x)) - sampleAt(S.std, F(x))));
        g.endShape(p.CLOSE);

        g.noFill();
        g.strokeWeight(1);
        g.stroke(255, 55);
        for (const name of names) {
          if (name === primary) continue;
          g.beginShape();
          for (let x = 0; x < W; x += 2) g.vertex(x, Y(sampleAt(series[name].norm, F(x))));
          g.endShape();
        }

        g.strokeWeight(2);
        for (let x = 1; x < W; x++) {
          const lv = S.level[Math.round(F(x))];
          g.stroke(PALETTES[lv].a[0], PALETTES[lv].a[1], PALETTES[lv].a[2]);
          g.line(x - 1, Y(sampleAt(S.norm, F(x - 1))), x, Y(sampleAt(S.norm, F(x))));
        }

        g.strokeWeight(1);
        for (let x = 0; x < W; x++) {
          const c = PALETTES[S.level[Math.round(F(x))]].a;
          g.stroke(c[0], c[1], c[2], 220);
          g.line(x, hH - strip, x, hH);
        }
        historyLayer = g;
      }

      function currentTime() {
        if (audio) return audio.currentTime || 0;
        return (p.millis() / 1000) % duration; // modo demo sin audio
      }

      function spawnPulse(e) {
        const y = p.map(e.midi, midiLo, midiHi, sceneH * 0.85, sceneH * 0.15);
        const x = e.level === 2 ? W / 2 + p.random(-W * 0.1, W * 0.1) : p.random(W * 0.1, W * 0.9);
        pulses.push({ x: x, y: y, r: 4, grow: 1.5 + e.vel * 5 + e.level * 2, life: 1,
                      decay: 1 / Math.max(8, e.d * 60), level: e.level, vel: e.vel });
        stats.pulses++;
        if (e.level === 2 && !opts.reduceMotion && audioTime - lastFlashAt >= MIN_FLASH_INTERVAL) {
          flash = Math.min(1, flash + 0.45 * e.vel);
          flashRed = !flashRed;
          lastFlashAt = audioTime;
          stats.flashTimes.push(audioTime);
          if (stats.flashTimes.length > 100) stats.flashTimes.shift();
        }
      }

      function handleEvents(t) {
        const jumped = t < lastT || t - lastT > 0.5;
        if (lastT < 0 || jumped) {
          evPtr = lowerBound(events, t); // búsqueda/salto: no disparar las notas saltadas
          return;
        }
        while (evPtr < events.length && events[evPtr].t <= t) {
          spawnPulse(events[evPtr]);
          evPtr++;
        }
      }

      function drawParticles(pal, w, speed) {
        const zt = p.millis() * 0.00008;
        // A más velocidad, vida más corta: el campo de flujo tiene atractores y,
        // sin reaparecer, las partículas rápidas se amontonan en pocos segundos.
        const ageStep = (1 + speed * 0.6) * kMove;
        const step = speed * kMove;
        for (let i = 0; i < particles.length; i++) {
          let q = particles[i];
          q.life -= ageStep;
          if (q.life <= 0) q = particles[i] = newParticle(false);
          q.px = q.x; q.py = q.y;
          const ang = p.noise(q.x * 0.0022, q.y * 0.0022, zt + q.seed * 0.0001) * p.TWO_PI * 2;
          q.x += Math.cos(ang) * step;
          q.y += Math.sin(ang) * step;
          if (q.x < 0 || q.x > W || q.y < 0 || q.y > sceneH) {
            q.x = (q.x + W) % W; q.y = (q.y + sceneH) % sceneH;
            q.px = q.x; q.py = q.y; // sin trazo a través del borde
          }
          if (w[0] > 0.02) { // bajo: círculos suaves
            p.noStroke();
            p.fill(pal.a[0], pal.a[1], pal.a[2], 150 * w[0]);
            p.circle(q.x, q.y, q.r * (1 + energy));
          }
          if (w[1] > 0.02) { // medio: trazos cortos
            p.stroke(pal.b[0], pal.b[1], pal.b[2], 140 * w[1]);
            p.strokeWeight(1.2);
            p.line(q.px, q.py, q.x, q.y);
          }
          if (w[2] > 0.02) { // alto: trazos acelerados y largos
            p.stroke(pal.b[0], pal.b[1], pal.b[2], 220 * w[2]);
            p.strokeWeight(1 + 1.5 * w[2]);
            const k = 3;
            p.line(q.x - (q.x - q.px) * k, q.y - (q.y - q.py) * k, q.x, q.y);
          }
        }
      }

      function drawGeometry(pal, w1, std, rotSpeed) {
        rot += rotSpeed * kMove;
        const cx = W / 2, cy = sceneH / 2;
        const base = sceneH * 0.07 * (0.8 + energy * 0.6) * (1 + std / 60);
        p.noFill();
        for (let i = 0; i < 5; i++) {
          const sides = 3 + i;
          const r = base * (i + 1);
          const dir = i % 2 ? 1 : -1;
          const c = i % 2 ? pal.a : pal.b;
          p.stroke(c[0], c[1], c[2], 210 * w1);
          p.strokeWeight(1.5);
          p.beginShape();
          for (let v = 0; v < sides; v++) {
            const a = rot * dir * (1 + i * 0.25) + (v / sides) * p.TWO_PI;
            const jit = (p.noise(i, v, p.millis() * 0.0006) - 0.5) * std * 1.2; // textura distorsionada
            p.vertex(cx + Math.cos(a) * (r + jit), cy + Math.sin(a) * (r + jit));
          }
          p.endShape(p.CLOSE);
        }
      }

      function drawPulses(pal) {
        p.noFill();
        for (let i = pulses.length - 1; i >= 0; i--) {
          const q = pulses[i];
          const c = PALETTES[q.level].accent;
          p.stroke(c[0], c[1], c[2], 255 * q.life);
          p.strokeWeight(1 + 2 * q.vel);
          p.circle(q.x, q.y, q.r * 2);
          q.r += q.grow * kMove;
          q.life -= q.decay * k60;
          if (q.life <= 0) pulses.splice(i, 1);
        }
      }

      function glitch(w2) {
        if (opts.reduceMotion || p.random() > 0.35 * w2) return;
        const ctx = p.drawingContext;
        const cnv = p.canvas;
        const slices = 1 + Math.floor(p.random(3));
        for (let s = 0; s < slices; s++) {
          const y = Math.floor(p.random(sceneH - 4));
          const h = Math.floor(Math.min(p.random(3, 28), sceneH - y));
          const dx = Math.floor(p.random(-50, 50) * w2);
          ctx.drawImage(cnv, 0, y, W, h, dx, y, W, h);
        }
      }

      function drawHistory(pos) {
        const hH = H - sceneH;
        p.image(historyLayer, 0, sceneH);
        const x = pos * W;
        p.noStroke();
        p.fill(6, 8, 12, 175);
        p.rect(x, sceneH, W - x, hH); // lo que aún no ha sonado
        p.stroke(255, 230);
        p.strokeWeight(1.5);
        p.line(x, sceneH, x, H);
      }

      function drawHud(t, value, lvl, pal) {
        p.noStroke();
        p.fill(0, 0, 0, 215); // casi opaco: el glitch no debe volver ilegible el texto
        p.rect(8, 8, Math.min(W - 16, 330), 46, 6);
        p.textFont("system-ui, sans-serif");
        p.textSize(13);
        p.fill(235);
        p.textAlign(p.LEFT, p.TOP);
        p.text(primary + ": " + (Math.round(value * 100) / 100), 16, 14);
        p.fill(pal.accent[0], pal.accent[1], pal.accent[2]);
        p.text("rango " + levelNames[lvl], 16, 32);
        p.fill(235);
        p.textAlign(p.RIGHT, p.TOP);
        p.text(formatTime(t) + " / " + formatTime(duration), Math.min(W - 16, 330), 14);
      }

      p.setup = function () {
        computeSize();
        const c = p.createCanvas(W, H);
        c.parent(container);
        p.pixelDensity(1);
        for (let i = 0; i < opts.particles; i++) particles.push(newParticle(true));
        buildHistory();
        p.background(PALETTES[0].bg[0], PALETTES[0].bg[1], PALETTES[0].bg[2]);
      };

      p.windowResized = function () {
        const oldW = W;
        computeSize();
        if (W === oldW) return;
        p.resizeCanvas(W, H);
        particles.length = 0;
        for (let i = 0; i < opts.particles; i++) particles.push(newParticle(true));
        buildHistory();
      };

      p.draw = function () {
        const dt = Math.max(0, p.deltaTime || 16.7) / 1000;
        k60 = Math.min(dt, 3) * 60;
        kMove = Math.min(dt, 0.1) * 60;
        stats.frames++;
        const t = currentTime();
        audioTime = t;
        const pos = clamp(t / duration, 0, 1);
        const f = pos * (n - 1);
        const norm = sampleAt(S.norm, f);
        const std = sampleAt(S.std, f);
        const change = sampleAt(S.change, f);
        const value = sampleAt(S.values, f);
        const lvl = S.level[Math.round(f)];

        const aw = smoothFactor(0.06, k60); // ~0,5 s para cambiar de estado
        for (let k = 0; k < 3; k++) weights[k] += ((k === lvl ? 1 : 0) - weights[k]) * aw;
        energy += (norm / 100 - energy) * smoothFactor(0.1, k60);
        const pal = mixPalette(weights);

        // Fondo con estela: larga en bajo, corta en alto (opacidad por segundo, no por cuadro).
        const trail = (14 + 70 * weights[2] + 20 * weights[1]) / 255;
        p.noStroke();
        p.fill(pal.bg[0], pal.bg[1], pal.bg[2], 255 * smoothFactor(trail, kMove));
        p.rect(0, 0, W, sceneH);

        handleEvents(t);
        const changeRel = clamp((change * (n - 1)) / 100 / 10, 0, 1); // mismo criterio que el tempo
        const speed = 0.35 + 2.5 * energy * energy + 7 * weights[2] * (0.4 + changeRel);
        drawParticles(pal, weights, speed);
        if (weights[1] > 0.02) drawGeometry(pal, weights[1], std, 0.004 + 0.03 * changeRel);
        drawPulses(pal);
        if (weights[2] > 0.05) glitch(weights[2]);

        if (flash > 0.01) {
          p.noStroke();
          if (flashRed) p.fill(255, 40, 50, 110 * flash);
          else p.fill(255, 255, 255, 110 * flash);
          p.rect(0, 0, W, sceneH);
          flash *= Math.pow(0.8, k60);
        }

        drawHistory(pos);
        drawHud(t, value, lvl, pal);
        lastT = t;
      };
    };

    return new global.p5(sketch);
  }

  global.createDecisionSketch = createDecisionSketch;
  global.DecisionSketchUtils = {
    sampleAt: sampleAt, lowerBound: lowerBound, mixPalette: mixPalette, clamp: clamp, smoothFactor: smoothFactor,
  };
})(typeof window !== "undefined" ? window : globalThis);
