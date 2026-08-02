// path: frontend/src/audio.ts
//
// All sound is synthesised at runtime with WebAudio. There are no sample files in this
// repository — which means no licensing questions, no download weight, and no 404s if
// someone moves the assets folder.
//
// A gunshot here is: a short noise burst through a bandpass (the crack), a fast pitch-
// swept sine (the body), and a decaying noise tail (the room). That combination reads as
// a firearm to the ear without any recorded material.

export type SoundName =
  | 'rifle'
  | 'smg'
  | 'sniper'
  | 'shotgun'
  | 'pistol'
  | 'knife'
  | 'reload'
  | 'hit'
  | 'headshot'
  | 'hurt'
  | 'death'
  | 'spawn'
  | 'switch'
  | 'empty'
  | 'ui';

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private noiseBuffer: AudioBuffer | null = null;
  enabled = true;
  volume = 0.6;

  /**
   * Must be called from a user gesture — browsers refuse to start an AudioContext
   * otherwise, and a suspended context fails silently rather than loudly.
   */
  init(): void {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') void this.ctx.resume();
      return;
    }
    const Ctor = window.AudioContext || (window as any).webkitAudioContext;
    if (!Ctor) return;
    this.ctx = new Ctor();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.volume;
    this.master.connect(this.ctx.destination);
    this.noiseBuffer = this.makeNoise(1.0);
  }

  setVolume(v: number): void {
    this.volume = v;
    if (this.master) this.master.gain.value = v;
  }

  private makeNoise(seconds: number): AudioBuffer {
    const ctx = this.ctx!;
    const length = Math.floor(ctx.sampleRate * seconds);
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
    return buffer;
  }

  /**
   * @param pan  -1 (left) .. 1 (right)
   * @param gain relative loudness, already distance-attenuated by the caller
   */
  play(name: SoundName, pan = 0, gain = 1): void {
    if (!this.enabled) return;
    this.init();
    if (!this.ctx || !this.master) return;
    const ctx = this.ctx;
    const t = ctx.currentTime;

    const panner = ctx.createStereoPanner();
    panner.pan.value = Math.max(-1, Math.min(1, pan));
    panner.connect(this.master);

    switch (name) {
      // Each weapon gets its own body frequency, decay and crack. Pitch alone would make
      // them sound like the same gun sped up; the decay length is what separates a snappy
      // SMG from a rolling shotgun boom.
      case 'rifle':
        this.gunshot(t, panner, gain, 220, 0.14, 1800);
        break;
      case 'smg':
        this.gunshot(t, panner, gain * 0.8, 300, 0.085, 2600);
        break;
      case 'sniper':
        this.gunshot(t, panner, gain * 1.25, 130, 0.34, 1200, 0.75);
        break;
      case 'shotgun':
        this.gunshot(t, panner, gain * 1.15, 105, 0.26, 900, 0.55);
        break;
      case 'pistol':
        this.gunshot(t, panner, gain * 0.85, 320, 0.11, 2400);
        break;
      case 'knife':
        this.swish(t, panner, gain);
        break;
      case 'reload':
        this.click(t, panner, gain, 0.0);
        this.click(t + 0.35, panner, gain, 0.05);
        this.click(t + 0.9, panner, gain * 1.1, -0.03);
        break;
      case 'switch':
        this.click(t, panner, gain * 0.7, 0.02);
        break;
      case 'empty':
        this.click(t, panner, gain * 0.5, 0.08);
        break;
      case 'hit':
        this.blip(t, panner, gain, 1300, 0.05);
        break;
      case 'headshot':
        this.blip(t, panner, gain * 1.2, 1900, 0.07);
        break;
      case 'hurt':
        this.thud(t, panner, gain, 160);
        break;
      case 'death':
        this.thud(t, panner, gain * 1.3, 90);
        break;
      case 'spawn':
        this.blip(t, panner, gain * 0.6, 520, 0.16);
        break;
      case 'ui':
        this.blip(t, panner, gain * 0.35, 760, 0.05);
        break;
    }
  }

  // ── primitives ──────────────────────────────────────────────────────────────

  private noiseSource(t: number, duration: number): AudioBufferSourceNode {
    const ctx = this.ctx!;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuffer;
    src.loop = true;
    src.start(t);
    src.stop(t + duration + 0.05);
    return src;
  }

  private gunshot(
    t: number,
    out: AudioNode,
    gain: number,
    bodyFreq: number,
    duration: number,
    crackFreq: number,
    tailLength = 0.32,
  ): void {
    const ctx = this.ctx!;

    // Crack: filtered noise, very fast decay.
    const noise = this.noiseSource(t, duration);
    const bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.setValueAtTime(crackFreq, t);
    bp.frequency.exponentialRampToValueAtTime(crackFreq * 0.35, t + duration);
    bp.Q.value = 0.9;
    const noiseGain = ctx.createGain();
    noiseGain.gain.setValueAtTime(gain * 0.55, t);
    noiseGain.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    noise.connect(bp).connect(noiseGain).connect(out);

    // Body: a pitch-swept sine gives the shot weight without muddying the crack.
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(bodyFreq, t);
    osc.frequency.exponentialRampToValueAtTime(bodyFreq * 0.25, t + duration * 0.8);
    const oscGain = ctx.createGain();
    oscGain.gain.setValueAtTime(gain * 0.5, t);
    oscGain.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(oscGain).connect(out);
    osc.start(t);
    osc.stop(t + duration + 0.02);

    // Tail: a longer, quieter, low-passed noise wash standing in for the room. Big
    // weapons get a longer one, which is most of why they sound powerful.
    const tail = this.noiseSource(t, tailLength);
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(900, t);
    const tailGain = ctx.createGain();
    tailGain.gain.setValueAtTime(gain * 0.14, t + 0.01);
    tailGain.gain.exponentialRampToValueAtTime(0.0001, t + tailLength);
    tail.connect(lp).connect(tailGain).connect(out);
  }

  private swish(t: number, out: AudioNode, gain: number): void {
    const ctx = this.ctx!;
    const noise = this.noiseSource(t, 0.18);
    const bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.setValueAtTime(600, t);
    bp.frequency.exponentialRampToValueAtTime(3000, t + 0.14);
    bp.Q.value = 2.5;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain * 0.3, t + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    noise.connect(bp).connect(g).connect(out);
  }

  private click(t: number, out: AudioNode, gain: number, detune: number): void {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    osc.type = 'square';
    osc.frequency.setValueAtTime(180 * (1 + detune), t);
    osc.frequency.exponentialRampToValueAtTime(60, t + 0.05);
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain * 0.12, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    osc.connect(g).connect(out);
    osc.start(t);
    osc.stop(t + 0.08);
  }

  private blip(t: number, out: AudioNode, gain: number, freq: number, duration: number): void {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 1.35, t + duration);
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain * 0.22, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(g).connect(out);
    osc.start(t);
    osc.stop(t + duration + 0.02);
  }

  private thud(t: number, out: AudioNode, gain: number, freq: number): void {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 0.4, t + 0.25);
    const g = ctx.createGain();
    g.gain.setValueAtTime(gain * 0.4, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
    osc.connect(g).connect(out);
    osc.start(t);
    osc.stop(t + 0.32);

    const noise = this.noiseSource(t, 0.12);
    const lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 400;
    const ng = ctx.createGain();
    ng.gain.setValueAtTime(gain * 0.2, t);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
    noise.connect(lp).connect(ng).connect(out);
  }
}

/**
 * Convert a world-space sound into pan and gain relative to the listener.
 *
 * Inverse-square would be physically right and practically useless — distant gunfire is
 * information the player needs. This rolls off gently and floors at an audible level.
 */
export function spatialise(
  listenerX: number,
  listenerZ: number,
  listenerYaw: number,
  sourceX: number,
  sourceZ: number,
  maxDistance = 60,
): { pan: number; gain: number } {
  const dx = sourceX - listenerX;
  const dz = sourceZ - listenerZ;
  const dist = Math.hypot(dx, dz);
  if (dist < 0.5) return { pan: 0, gain: 1 };

  // Project onto the listener's right vector, which for this yaw convention is
  // (cos(yaw), 0, -sin(yaw)) — the same one movement.ts uses for strafing.
  const rightX = Math.cos(listenerYaw);
  const rightZ = -Math.sin(listenerYaw);
  const along = (dx * rightX + dz * rightZ) / dist;
  const pan = Math.max(-1, Math.min(1, along));
  const gain = Math.max(0.06, 1 - dist / maxDistance) ** 1.6;
  return { pan, gain };
}
